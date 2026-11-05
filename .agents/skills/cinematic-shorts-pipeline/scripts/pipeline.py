#!/usr/bin/env python3
"""Validate and preview pipelines, simulate TTS, publish mock runs, and run local Kokoro pilots."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import struct
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


PIPELINE_VERSION = 1
MANIFEST_VERSION = 1
REQUIRED_STAGE_IDS = (
    "01-validate-project",
    "02-blender-scenes",
    "04-remotion-compose",
    "05-ffmpeg-finalize",
    "06-quality-control",
)
LEGACY_NARRATION_STAGE_ID = "03-kokoro-narration"
TTS_NARRATION_STAGE_ID = "03-tts-narration"
NARRATION_STAGE_IDS = frozenset({LEGACY_NARRATION_STAGE_ID, TTS_NARRATION_STAGE_ID})
TTS_PROVIDER_TO_TOOL = {"kokoro": "kokoro", "piper": "piper"}
VOICEOVER_SCRIPT_ID = "voiceover-script"
VOICEOVER_SCRIPT_INPUT = f"resource-{VOICEOVER_SCRIPT_ID}"
MAX_VOICEOVER_SCRIPT_BYTES = 1024 * 1024
MAX_MOCK_WAV_BYTES = 512 * 1024
MAX_MOCK_PROVENANCE_BYTES = 64 * 1024
MOCK_DURATION_MS = 1000
MOCK_AUDIO_COMMENT = b"SIMULATED MOCK SILENCE; NOT SPEECH"
TTS_SIMULATION_DIRECTORY = (".pipeline", "simulations", "tts")
TTS_SIMULATION_FILES = frozenset({"mock-narration.wav", "mock-provenance.json"})
TTS_LOCAL_RUN_DIRECTORY = (".pipeline", "tts-local-runs")
TTS_LOCAL_RUN_FILES = frozenset({"narration.wav", "narration-provenance.json"})
KOKORO_RUNTIME_VERSION = "0.6.1"
KOKORO_LANGUAGE = "es"
KOKORO_VOICE = "em_alex"
KOKORO_AUDIO = {
    "container": "wav",
    "sample_rate_hz": 24000,
    "channels": 1,
    "sample_format": "pcm_s16le",
}
MAX_KOKORO_SCRIPT_BYTES = 8192
MAX_KOKORO_WAV_BYTES = 16 * 1024 * 1024
MAX_KOKORO_DURATION_SECONDS = 300
MAX_KOKORO_PROVENANCE_BYTES = 64 * 1024
KOKORO_TIMEOUT_SECONDS = 300
TTS_SAMPLE_RATES = {16000, 22050, 24000, 32000, 44100, 48000}
TTS_SAMPLE_FORMATS = {"pcm_s16le", "pcm_s24le", "pcm_f32le"}
TTS_VOICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TTS_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
STAGE_STATES = {"pending", "completed", "failed", "blocked"}
RETRYABLE_ERRORS = {"transient", "timeout", "resource-pressure"}
OUTPUT_FORMATS = {"mp4", "mov", "webm"}
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXECUTABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

_storyboard_spec = importlib.util.spec_from_file_location(
    "pipeline_storyboard_validator", Path(__file__).with_name("validate-storyboard.py")
)
STORYBOARD_VALIDATOR = importlib.util.module_from_spec(_storyboard_spec)
_storyboard_spec.loader.exec_module(STORYBOARD_VALIDATOR)


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(value):
    """Return a deterministic SHA-256 fingerprint for JSON-compatible data."""
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_load(path):
    return STORYBOARD_VALIDATOR.parse_json(Path(path).read_text(encoding="utf-8"))


def _object(value, label, required, optional, errors):
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    missing = sorted(set(required) - value.keys())
    extra = sorted(value.keys() - set(required) - set(optional))
    errors.extend(f"{label} missing required field: {key}" for key in missing)
    errors.extend(f"{label} has unknown field: {key}" for key in extra)
    return not missing


def _string(value, label, errors, pattern=None):
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return False
    if pattern is not None and not pattern.fullmatch(value):
        errors.append(f"{label} has an unsupported format")
        return False
    return True


def _integer(value, label, errors, minimum, maximum=None):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        errors.append(f"{label} must be an integer >= {minimum}")
        return False
    if maximum is not None and value > maximum:
        errors.append(f"{label} must be <= {maximum}")
        return False
    return True


def _path(root, value, label, errors, warnings=None, required_file=False):
    """Resolve a project-relative POSIX path and reject traversal and symlink escapes."""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty relative path")
        return None
    if "\x00" in value:
        errors.append(f"{label} must not contain a NUL character")
        return None
    if "\\" in value or ":" in value or value.startswith("/"):
        errors.append(f"{label} must use a project-relative path with forward slashes")
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        errors.append(f"{label} must not contain empty, dot, or parent path segments")
        return None
    try:
        project_root = Path(root).resolve()
        candidate = (project_root.joinpath(*PurePosixPath(value).parts)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"{label} cannot be resolved safely: {exc}")
        return None
    try:
        candidate.relative_to(project_root)
    except ValueError:
        errors.append(f"{label} escapes the project directory through a symbolic link")
        return None
    if required_file and not candidate.is_file():
        if warnings is None:
            errors.append(f"{label} does not exist as a file: {value}")
        else:
            warnings.append(f"optional resource is not present: {value}")
    return candidate


def _validate_voiceover_script(path, label, errors):
    """Validate a bounded UTF-8 voiceover script without inferring captions."""
    try:
        with Path(path).open("rb") as stream:
            content = stream.read(MAX_VOICEOVER_SCRIPT_BYTES + 1)
    except OSError as exc:
        errors.append(f"{label} cannot be read: {exc}")
        return None
    if len(content) > MAX_VOICEOVER_SCRIPT_BYTES:
        errors.append(f"{label} exceeds maximum size of {MAX_VOICEOVER_SCRIPT_BYTES} bytes")
        return content
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        errors.append(f"{label} must contain valid UTF-8 text")
        return content
    if not text.strip():
        errors.append(f"{label} must not be empty or whitespace-only")
    if "\x00" in text:
        errors.append(f"{label} must not contain NUL characters")
    return content


def _validate_tts_contract(stage, label, project_resources, tool_ids, errors):
    """Validate the declarative, non-executable TTS stage contract."""
    tts = stage.get("tts")
    tts_label = f"{label}.tts"
    if not _object(tts, tts_label, ["provider", "voice", "language", "audio", "script_input"], [], errors):
        return

    provider = tts.get("provider")
    provider_valid = _string(provider, f"{tts_label}.provider", errors, SLUG)
    tool_id = TTS_PROVIDER_TO_TOOL.get(provider) if provider_valid else None
    stage_tools = stage.get("tools") if isinstance(stage.get("tools"), list) else []
    if tool_id is None:
        errors.append(f"{tts_label}.provider is unsupported; expected one of {', '.join(sorted(TTS_PROVIDER_TO_TOOL))}")
    elif stage_tools != [tool_id]:
        errors.append(f"{label}.tools must contain exactly the registered provider tool {tool_id}")
    if stage.get("adapter") != "tts-narration":
        errors.append(f"{label}.adapter must be 'tts-narration'")
    if stage.get("adapter_status") != "pending":
        errors.append(f"{label}.adapter_status must remain 'pending'; no TTS executor is implemented")

    _string(tts.get("voice"), f"{tts_label}.voice", errors, TTS_VOICE)
    _string(tts.get("language"), f"{tts_label}.language", errors, TTS_LANGUAGE)
    audio = tts.get("audio")
    if _object(audio, f"{tts_label}.audio", ["container", "sample_rate_hz", "channels", "sample_format"], [], errors):
        if audio.get("container") != "wav":
            errors.append(f"{tts_label}.audio.container must be 'wav'")
        sample_rate = audio.get("sample_rate_hz")
        if _integer(sample_rate, f"{tts_label}.audio.sample_rate_hz", errors, 1) and sample_rate not in TTS_SAMPLE_RATES:
            errors.append(f"{tts_label}.audio.sample_rate_hz must be one of {', '.join(map(str, sorted(TTS_SAMPLE_RATES)))}")
        channels = audio.get("channels")
        if _integer(channels, f"{tts_label}.audio.channels", errors, 1) and channels not in {1, 2}:
            errors.append(f"{tts_label}.audio.channels must be 1 or 2")
        sample_format = audio.get("sample_format")
        if not isinstance(sample_format, str) or sample_format not in TTS_SAMPLE_FORMATS:
            errors.append(f"{tts_label}.audio.sample_format must be one of {', '.join(sorted(TTS_SAMPLE_FORMATS))}")

    script_input = tts.get("script_input")
    if script_input != VOICEOVER_SCRIPT_INPUT:
        errors.append(f"{tts_label}.script_input must be '{VOICEOVER_SCRIPT_INPUT}'")
    stage_inputs = stage.get("inputs") if isinstance(stage.get("inputs"), list) else []
    if script_input not in stage_inputs:
        errors.append(f"{label}.inputs must include {VOICEOVER_SCRIPT_INPUT}")
    resource = project_resources.get(VOICEOVER_SCRIPT_ID)
    if resource is None:
        errors.append(f"project.resources must declare required {VOICEOVER_SCRIPT_ID}")
    else:
        if resource.get("required") is not True:
            errors.append(f"project.resources.{VOICEOVER_SCRIPT_ID}.required must be true")
        if resource.get("kind") != VOICEOVER_SCRIPT_ID:
            errors.append(f"project.resources.{VOICEOVER_SCRIPT_ID}.kind must be '{VOICEOVER_SCRIPT_ID}'")

    stage_outputs = stage.get("outputs") if isinstance(stage.get("outputs"), list) else []
    outputs = {
        item.get("id"): item.get("path")
        for item in stage_outputs
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    expected_outputs = {"narration": ".wav", "narration-provenance": ".json"}
    if set(outputs) != set(expected_outputs):
        errors.append(f"{label}.outputs must declare narration WAV and narration-provenance JSON outputs")
    else:
        for output_id, extension in expected_outputs.items():
            output_path = outputs[output_id]
            if not isinstance(output_path, str) or Path(output_path).suffix.lower() != extension:
                errors.append(f"{label}.outputs.{output_id} path must end in {extension}")


def _safe_relative(root, value, label, errors):
    return _path(root, value, label, errors)


def _unique_strings(values, label, errors, pattern=None):
    if not isinstance(values, list):
        errors.append(f"{label} must be an array")
        return []
    result = []
    seen = set()
    for index, value in enumerate(values):
        item_label = f"{label}[{index}]"
        if not _string(value, item_label, errors, pattern):
            continue
        if value in seen:
            errors.append(f"{item_label} duplicates {value}")
        seen.add(value)
        result.append(value)
    return result


def _topological_waves(stages, errors):
    stage_ids = {stage.get("id") for stage in stages if isinstance(stage, dict) and isinstance(stage.get("id"), str)}
    by_id = {stage.get("id"): stage for stage in stages if isinstance(stage, dict) and isinstance(stage.get("id"), str)}
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_id = stage.get("id", "<unknown>")
        dependencies = stage.get("depends_on", [])
        if not isinstance(dependencies, list):
            errors.append(f"stage {stage_id}.depends_on must be an array")
            continue
        if len(set(dependency for dependency in dependencies if isinstance(dependency, str))) != len(
            [dependency for dependency in dependencies if isinstance(dependency, str)]
        ):
            errors.append(f"stage {stage_id}.depends_on contains duplicates")
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency not in stage_ids:
                errors.append(f"stage {stage_id} has unknown dependency: {dependency}")
            elif dependency == stage_id:
                errors.append(f"stage {stage_id} cannot depend on itself")

    waves = []
    completed = set()
    remaining = set(by_id)
    while remaining:
        ready = sorted(
            stage_id for stage_id in remaining
            if isinstance(by_id[stage_id].get("depends_on", []), list)
            and all(
                isinstance(dependency, str) and dependency in completed
                for dependency in by_id[stage_id].get("depends_on", [])
            )
        )
        if not ready:
            errors.append("stage dependencies contain a cycle")
            break
        waves.append(ready)
        completed.update(ready)
        remaining.difference_update(ready)
    return waves


def validate_config(data, config_path, captured_resources=None):
    """Return (errors, warnings, deterministic execution waves)."""
    errors, warnings = [], []
    if not _object(
        data, "pipeline", ["pipeline_version", "project", "profile", "workspace", "retry_policy", "tools", "stages"], [], errors
    ):
        return errors, warnings, []

    version = data.get("pipeline_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != PIPELINE_VERSION:
        errors.append(f"pipeline_version must be integer {PIPELINE_VERSION}")

    root = Path(config_path).resolve().parent
    storyboard = None
    project_resources = {}
    project_resource_paths = {}
    project = data.get("project")
    if _object(project, "project", ["id", "storyboard", "resources"], [], errors):
        _string(project.get("id"), "project.id", errors, SLUG)
        storyboard_path = _path(root, project.get("storyboard"), "project.storyboard", errors, required_file=True)
        resources = project.get("resources")
        if not isinstance(resources, list):
            errors.append("project.resources must be an array")
            resources = []
        resource_ids = set()
        for index, resource in enumerate(resources):
            label = f"project.resources[{index}]"
            if not _object(resource, label, ["id", "path", "required"], ["kind"], errors):
                continue
            resource_id = resource.get("id")
            if not _string(resource_id, f"{label}.id", errors, SLUG):
                continue
            if resource_id in resource_ids:
                errors.append(f"{label}.id duplicates {resource_id}")
            resource_ids.add(resource_id)
            project_resources[resource_id] = resource
            if "kind" in resource:
                _string(resource["kind"], f"{label}.kind", errors, SLUG)
            if not isinstance(resource.get("required"), bool):
                errors.append(f"{label}.required must be a boolean")
            resource_path = _path(root, resource.get("path"), f"{label}.path", errors)
            if resource_path is not None:
                project_resource_paths[resource_id] = resource_path
            if resource_path is not None and not resource_path.is_file():
                if resource.get("required") is True:
                    errors.append(f"required resource does not exist: {resource.get('path')}")
                else:
                    warnings.append(f"optional resource is not present: {resource.get('path')}")
        if storyboard_path is not None and storyboard_path.is_file():
            try:
                storyboard = _strict_load(storyboard_path)
                errors.extend(
                    f"storyboard: {error}" for error in STORYBOARD_VALIDATOR.validate(storyboard)
                )
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(f"cannot read project storyboard: {exc}")

    profile = data.get("profile")
    if _object(profile, "profile", ["width", "height", "fps", "format", "limits"], [], errors):
        width_valid = _integer(profile.get("width"), "profile.width", errors, 1, 16384)
        height_valid = _integer(profile.get("height"), "profile.height", errors, 1, 16384)
        fps_valid = _integer(profile.get("fps"), "profile.fps", errors, 1, 120)
        if isinstance(storyboard, dict):
            storyboard_fps = storyboard.get("fps")
            if fps_valid and isinstance(storyboard_fps, int) and not isinstance(storyboard_fps, bool):
                if profile["fps"] != storyboard_fps:
                    errors.append("profile.fps must match storyboard.fps; retiming is not available")
            aspect_ratio = storyboard.get("aspect_ratio")
            if (
                width_valid and height_valid and isinstance(aspect_ratio, str)
                and re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", aspect_ratio)
                and all(
                    len(component) <= STORYBOARD_VALIDATOR.MAX_ASPECT_RATIO_COMPONENT_DIGITS
                    for component in aspect_ratio.split(":")
                )
            ):
                aspect_width, aspect_height = (int(value) for value in aspect_ratio.split(":"))
                if profile["width"] * aspect_height != profile["height"] * aspect_width:
                    errors.append(
                        "profile dimensions must match storyboard.aspect_ratio; cropping or adaptation is not available"
                    )
        if not isinstance(profile.get("format"), str) or profile.get("format") not in OUTPUT_FORMATS:
            errors.append(f"profile.format must be one of {', '.join(sorted(OUTPUT_FORMATS))}")
        limits = profile.get("limits")
        if _object(limits, "profile.limits", ["cpu_threads", "memory_mb", "vram_mb", "max_parallel_stages"], [], errors):
            _integer(limits.get("cpu_threads"), "profile.limits.cpu_threads", errors, 1, 4)
            _integer(limits.get("memory_mb"), "profile.limits.memory_mb", errors, 1024, 12288)
            _integer(limits.get("vram_mb"), "profile.limits.vram_mb", errors, 512, 3584)
            _integer(limits.get("max_parallel_stages"), "profile.limits.max_parallel_stages", errors, 1, 4)

    workspace = data.get("workspace")
    if _object(workspace, "workspace", ["intermediate_dir", "artifact_dir", "manifest_dir"], [], errors):
        for key in ("intermediate_dir", "artifact_dir", "manifest_dir"):
            _safe_relative(root, workspace.get(key), f"workspace.{key}", errors)

    retry = data.get("retry_policy")
    if _object(retry, "retry_policy", ["max_attempts", "retryable_errors", "resume_policy"], [], errors):
        _integer(retry.get("max_attempts"), "retry_policy.max_attempts", errors, 1, 5)
        retryable = _unique_strings(retry.get("retryable_errors"), "retry_policy.retryable_errors", errors)
        for error_kind in retryable:
            if error_kind not in RETRYABLE_ERRORS:
                errors.append(f"unsupported retryable error: {error_kind}")
        if retry.get("resume_policy") != "fingerprint":
            errors.append("retry_policy.resume_policy must be 'fingerprint'")

    tools = data.get("tools")
    if not isinstance(tools, list):
        errors.append("tools must be an array")
        tools = []
    tool_ids = set()
    for index, tool in enumerate(tools):
        label = f"tools[{index}]"
        if not _object(tool, label, ["id", "executable", "version_requirement", "availability", "adapter_status"], [], errors):
            continue
        tool_id = tool.get("id")
        if not _string(tool_id, f"{label}.id", errors, SLUG):
            continue
        if tool_id in tool_ids:
            errors.append(f"{label}.id duplicates {tool_id}")
        tool_ids.add(tool_id)
        _string(tool.get("executable"), f"{label}.executable", errors, EXECUTABLE)
        _string(tool.get("version_requirement"), f"{label}.version_requirement", errors)
        if not isinstance(tool.get("availability"), str) or tool.get("availability") not in {"not_checked", "verified"}:
            errors.append(f"{label}.availability must be 'not_checked' or 'verified'")
        if not isinstance(tool.get("adapter_status"), str) or tool.get("adapter_status") not in {"pending", "implemented"}:
            errors.append(f"{label}.adapter_status must be 'pending' or 'implemented'")

    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("stages must be a non-empty array")
        stages = []
    stage_ids = []
    by_id = {}
    output_ids = {}
    output_paths = set()
    project_input_paths = set()
    workspace_paths = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    output_roots = [workspace_paths.get("intermediate_dir"), workspace_paths.get("artifact_dir")]
    top_inputs = {"storyboard"}
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    resources = project.get("resources", []) if isinstance(project.get("resources", []), list) else []
    top_inputs.update(f"resource-{item.get('id')}" for item in resources if isinstance(item, dict) and isinstance(item.get("id"), str))
    if isinstance(project.get("storyboard"), str):
        storyboard_input = _path(root, project["storyboard"], "project.storyboard", [])
        if storyboard_input is not None:
            project_input_paths.add(str(storyboard_input).casefold())
    for item in resources:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            resource_path = _path(root, item["path"], "project resource path", [])
            if resource_path is not None:
                project_input_paths.add(str(resource_path).casefold())
    project_input_paths.add(str(Path(config_path).resolve()).casefold())

    for index, stage in enumerate(stages):
        label = f"stages[{index}]"
        required = ["id", "adapter", "adapter_status", "depends_on", "inputs", "outputs", "tools"]
        if not _object(stage, label, required, ["tts"], errors):
            continue
        stage_id = stage.get("id")
        if not _string(stage_id, f"{label}.id", errors, SLUG):
            continue
        stage_ids.append(stage_id)
        if stage_id in by_id:
            errors.append(f"duplicate stage id: {stage_id}")
        by_id[stage_id] = stage
        if stage_id == TTS_NARRATION_STAGE_ID:
            _validate_tts_contract(stage, label, project_resources, tool_ids, errors)
        elif "tts" in stage:
            errors.append(f"{label}.tts is only valid on {TTS_NARRATION_STAGE_ID}")
        _string(stage.get("adapter"), f"{label}.adapter", errors, SLUG)
        if not isinstance(stage.get("adapter_status"), str) or stage.get("adapter_status") not in {"pending", "implemented"}:
            errors.append(f"{label}.adapter_status must be 'pending' or 'implemented'")
        _unique_strings(stage.get("inputs"), f"{label}.inputs", errors, SLUG)
        _unique_strings(stage.get("tools"), f"{label}.tools", errors, SLUG)
        if isinstance(stage.get("tools"), list):
            for tool_id in stage["tools"]:
                if isinstance(tool_id, str) and tool_id not in tool_ids:
                    errors.append(f"{label} references unknown tool: {tool_id}")
        outputs = stage.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            errors.append(f"{label}.outputs must be a non-empty array")
            continue
        for output_index, output in enumerate(outputs):
            output_label = f"{label}.outputs[{output_index}]"
            if not _object(output, output_label, ["id", "path"], [], errors):
                continue
            output_id = output.get("id")
            if not _string(output_id, f"{output_label}.id", errors, SLUG):
                continue
            if output_id in output_ids or output_id in top_inputs:
                errors.append(f"duplicate artifact id: {output_id}")
            output_ids[output_id] = stage_id
            path = output.get("path")
            resolved = _safe_relative(root, path, f"{output_label}.path", errors)
            if resolved is not None:
                path_key = str(resolved).casefold()
                separator = str(Path("/")).casefold()
                if path_key in output_paths:
                    errors.append(f"duplicate output path: {path}")
                if any(
                    path_key == source
                    or path_key.startswith(source + separator)
                    or source.startswith(path_key + separator)
                    for source in project_input_paths
                ):
                    errors.append(f"{output_label}.path would overwrite a project input or pipeline configuration")
                if any(
                    existing.startswith(path_key + separator)
                    or path_key.startswith(existing + separator)
                    for existing in output_paths
                ):
                    errors.append(f"{output_label}.path overlaps another output file or its parent directory")
                output_paths.add(path_key)
                allowed = False
                for output_root in output_roots:
                    if isinstance(output_root, str):
                        output_root_path = _path(root, output_root, "workspace output root", [])
                        if output_root_path is not None:
                            try:
                                relative_output = resolved.relative_to(output_root_path)
                                if relative_output == Path("."):
                                    errors.append(f"{output_label}.path cannot be the workspace directory itself")
                                    continue
                                allowed = True
                            except ValueError:
                                continue
                if not allowed:
                    errors.append(f"{output_label}.path must be under workspace.intermediate_dir or workspace.artifact_dir")

    for required_id in REQUIRED_STAGE_IDS:
        if required_id not in by_id:
            errors.append(f"missing required pipeline stage: {required_id}")
    declared_narration_stages = NARRATION_STAGE_IDS.intersection(by_id)
    if len(declared_narration_stages) != 1:
        errors.append(
            "pipeline must declare exactly one narration stage: "
            f"{LEGACY_NARRATION_STAGE_ID} or {TTS_NARRATION_STAGE_ID}"
        )
    if TTS_NARRATION_STAGE_ID in declared_narration_stages:
        tts_stage = by_id[TTS_NARRATION_STAGE_ID]
        dependencies = tts_stage.get("depends_on")
        if not isinstance(dependencies, list) or "01-validate-project" not in dependencies:
            errors.append(
                f"stage {TTS_NARRATION_STAGE_ID} must directly depend on 01-validate-project"
            )
        script_path = project_resource_paths.get(VOICEOVER_SCRIPT_ID)
        if script_path is not None and script_path.is_file():
            script_content = _validate_voiceover_script(
                script_path, "project.resources.voiceover-script", errors
            )
            if captured_resources is not None and script_content is not None:
                captured_resources[VOICEOVER_SCRIPT_ID] = script_content
    waves = _topological_waves(stages, errors)

    known_inputs = top_inputs | set(output_ids)
    final_stage = by_id.get("05-ffmpeg-finalize", {})
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    final_outputs = final_stage.get("outputs", []) if isinstance(final_stage, dict) else []
    if isinstance(final_outputs, list) and final_outputs and isinstance(final_outputs[0], dict):
        final_path = final_outputs[0].get("path")
        output_format = profile.get("format")
        if isinstance(final_path, str) and isinstance(output_format, str) and Path(final_path).suffix.lstrip(".").lower() != output_format:
            errors.append("05-ffmpeg-finalize output extension must match profile.format")
    ancestors = {}
    for wave in waves:
        for stage_id in wave:
            stage = by_id.get(stage_id, {})
            dependencies = stage.get("depends_on", [])
            if not isinstance(dependencies, list):
                dependencies = []
            ancestors[stage_id] = set(dependencies)
            for dependency in dependencies:
                ancestors[stage_id].update(ancestors.get(dependency, set()))
            inputs = stage.get("inputs", [])
            if not isinstance(inputs, list):
                continue
            for input_id in inputs:
                if not isinstance(input_id, str):
                    continue
                if input_id not in known_inputs:
                    errors.append(f"stage {stage_id} references unknown input or artifact: {input_id}")
                elif input_id in output_ids and output_ids[input_id] not in ancestors[stage_id]:
                    errors.append(f"stage {stage_id} must depend on producer of input {input_id}")

    return errors, warnings, waves


def load_and_validate_config(path, captured_resources=None):
    """Load a pipeline and return (config, errors, warnings, execution_waves)."""
    config_path = Path(path)
    try:
        data = _strict_load(config_path)
    except (OSError, UnicodeError, ValueError) as exc:
        return None, [f"cannot read pipeline JSON: {exc}"], [], []
    errors, warnings, waves = validate_config(data, config_path, captured_resources)
    return data, errors, warnings, waves


def _riff_chunk(chunk_id, content):
    padding = b"\x00" if len(content) & 1 else b""
    return chunk_id + struct.pack("<I", len(content)) + content + padding


def _mock_silence_wav(audio):
    """Create deterministic one-second silent RIFF/WAVE bytes for contract testing."""
    sample_formats = {
        "pcm_s16le": (1, 2, 1),
        "pcm_s24le": (1, 3, 1),
        "pcm_f32le": (3, 4, 3),
    }
    try:
        format_code, sample_width, _ = sample_formats[audio["sample_format"]]
        sample_rate = audio["sample_rate_hz"]
        channels = audio["channels"]
    except (KeyError, TypeError) as exc:
        raise ValueError("mock backend received unsupported WAV parameters") from exc
    if (
        not isinstance(sample_rate, int) or isinstance(sample_rate, bool)
        or sample_rate not in TTS_SAMPLE_RATES
        or not isinstance(channels, int) or isinstance(channels, bool)
        or channels not in {1, 2}
    ):
        raise ValueError("mock backend received invalid WAV parameters")

    frames = sample_rate * MOCK_DURATION_MS // 1000
    block_align = channels * sample_width
    comment = MOCK_AUDIO_COMMENT + b"\x00"
    software = b"cinematic-shorts-pipeline mock\x00"
    info_size = 4 + sum(8 + len(content) + (len(content) & 1) for content in (comment, software))
    chunk_sizes = [16, info_size, frames * block_align]
    if format_code == 3:
        chunk_sizes.insert(1, 4)
    expected_size = 12 + sum(8 + size + (size & 1) for size in chunk_sizes)
    if expected_size > MAX_MOCK_WAV_BYTES:
        raise ValueError(f"mock WAV exceeds maximum size of {MAX_MOCK_WAV_BYTES} bytes")
    data = bytes(frames * block_align)
    fmt = struct.pack(
        "<HHIIHH",
        format_code,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        sample_width * 8,
    )
    chunks = [_riff_chunk(b"fmt ", fmt)]
    if format_code == 3:
        chunks.append(_riff_chunk(b"fact", struct.pack("<I", frames)))
    info = b"INFO" + _riff_chunk(b"ICMT", MOCK_AUDIO_COMMENT + b"\x00")
    info += _riff_chunk(b"ISFT", b"cinematic-shorts-pipeline mock\x00")
    chunks.append(_riff_chunk(b"LIST", info))
    chunks.append(_riff_chunk(b"data", data))
    body = b"WAVE" + b"".join(chunks)
    wav_bytes = b"RIFF" + struct.pack("<I", len(body)) + body
    if len(wav_bytes) != expected_size:
        raise ValueError("mock WAV size does not match its calculated RIFF size")
    return wav_bytes


SIMULATION_BACKENDS = {"mock": _mock_silence_wav}


def _inspect_mock_wav(data):
    """Read back and verify the RIFF/WAVE contract generated by the mock."""
    if not isinstance(data, bytes) or len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("mock backend did not return RIFF/WAVE bytes")
    if struct.unpack_from("<I", data, 4)[0] != len(data) - 8:
        raise ValueError("mock WAV RIFF size does not match its byte length")
    chunks = {}
    offset = 12
    while offset < len(data):
        if len(data) - offset < 8:
            raise ValueError("mock WAV contains a truncated RIFF chunk header")
        chunk_id = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > len(data):
            raise ValueError("mock WAV contains a truncated RIFF chunk")
        if chunk_id in chunks:
            raise ValueError(f"mock WAV contains duplicate {chunk_id!r} chunks")
        chunks[chunk_id] = data[start:end]
        offset = end + (size & 1)
    if offset != len(data) or b"fmt " not in chunks or b"data" not in chunks or b"LIST" not in chunks:
        raise ValueError("mock WAV is missing required RIFF chunks")
    if MOCK_AUDIO_COMMENT not in chunks[b"LIST"]:
        raise ValueError("mock WAV is missing its simulation marker")
    fmt = chunks[b"fmt "]
    if len(fmt) != 16:
        raise ValueError("mock WAV fmt chunk has an unsupported size")
    format_code, channels, sample_rate, byte_rate, block_align, bits = struct.unpack("<HHIIHH", fmt)
    format_by_bits = {(1, 16): "pcm_s16le", (1, 24): "pcm_s24le", (3, 32): "pcm_f32le"}
    sample_format = format_by_bits.get((format_code, bits))
    if sample_format is None or channels not in {1, 2} or sample_rate not in TTS_SAMPLE_RATES:
        raise ValueError("mock WAV header has unsupported effective parameters")
    sample_width = bits // 8
    expected_block_align = channels * sample_width
    if block_align != expected_block_align or byte_rate != sample_rate * expected_block_align:
        raise ValueError("mock WAV byte rate or block alignment is inconsistent")
    if len(chunks[b"data"]) % block_align:
        raise ValueError("mock WAV data does not contain whole frames")
    if any(chunks[b"data"]):
        raise ValueError("mock WAV data is not digital silence")
    frames = len(chunks[b"data"]) // block_align
    duration_ms = frames * 1000 // sample_rate
    if frames * 1000 != sample_rate * MOCK_DURATION_MS or duration_ms != MOCK_DURATION_MS:
        raise ValueError("mock WAV is not exactly one second long")
    if format_code == 3:
        if chunks.get(b"fact") != struct.pack("<I", frames):
            raise ValueError("mock float WAV fact chunk does not match its frame count")
    elif b"fact" in chunks:
        raise ValueError("mock PCM WAV must not contain a float fact chunk")
    return {
        "container": "wav",
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "sample_format": sample_format,
    }, frames, duration_ms


def simulate_tts(config_path, backend="mock"):
    """Validate a generic TTS pipeline and return an in-memory simulation only."""
    backend_function = SIMULATION_BACKENDS.get(backend)
    if backend_function is None:
        raise ValueError(f"unsupported simulation backend: {backend}")
    captured_resources = {}
    config, errors, _, _ = load_and_validate_config(config_path, captured_resources)
    if errors:
        raise ValueError("pipeline validation failed: " + "; ".join(errors))
    stage = next(
        (item for item in config["stages"] if item.get("id") == TTS_NARRATION_STAGE_ID),
        None,
    )
    if stage is None:
        raise ValueError(f"simulation requires {TTS_NARRATION_STAGE_ID}")
    script_bytes = captured_resources.get(VOICEOVER_SCRIPT_ID)
    if script_bytes is None:
        raise ValueError("validated voiceover script bytes were not captured")

    wav_bytes = backend_function(stage["tts"]["audio"])
    if len(wav_bytes) > MAX_MOCK_WAV_BYTES:
        raise ValueError(f"mock WAV exceeds maximum size of {MAX_MOCK_WAV_BYTES} bytes")
    audio_effective, frames, duration_ms = _inspect_mock_wav(wav_bytes)
    requested_audio = stage["tts"]["audio"]
    if any(audio_effective[key] != requested_audio[key] for key in audio_effective):
        raise ValueError("mock WAV effective parameters do not match the TTS contract")

    provenance = {
        "provenance_version": 1,
        "simulation": True,
        "backend": backend,
        "provider_requested": stage["tts"]["provider"],
        "provider_executed": None,
        "voice": stage["tts"]["voice"],
        "language": stage["tts"]["language"],
        "audio": audio_effective,
        "frames": frames,
        "duration_ms": duration_ms,
        "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
        "wav_sha256": hashlib.sha256(wav_bytes).hexdigest(),
    }
    summary = dict(provenance)
    summary["wav_size_bytes"] = len(wav_bytes)
    return {"summary": summary, "provenance": provenance, "wav_bytes": wav_bytes}


def _is_reparse_point(file_stat):
    """Detect symlinks and every Windows reparse point exposed by os.stat()."""
    if stat.S_ISLNK(file_stat.st_mode):
        return True
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(file_stat, "st_file_attributes", 0) & reparse_attribute)


def _file_identity(file_stat):
    """Return a comparable filesystem identity, or None when the platform omits it."""
    device = getattr(file_stat, "st_dev", None)
    inode = getattr(file_stat, "st_ino", None)
    if device is None or inode in (None, 0):
        return None
    return device, inode


def _inspect_path(path):
    """lstat a path and reject links/reparse points before using the entry."""
    try:
        entry = Path(path).lstat()
    except FileNotFoundError:
        return None
    if _is_reparse_point(entry):
        raise ValueError(f"unsafe destination component is a symbolic link or reparse point: {path}")
    return entry


def _ensure_project_output_directory(project_root, components):
    """Create a fixed project-local output path without following existing links."""
    current = Path(project_root)
    root_stat = _inspect_path(current)
    if root_stat is None or not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"project root is not a directory: {current}")
    for component in components:
        current = current / component
        entry = _inspect_path(current)
        if entry is None:
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                # Another trusted invocation may have created this shared parent.
                pass
            entry = _inspect_path(current)
        if entry is None or not stat.S_ISDIR(entry.st_mode):
            raise ValueError(f"unsafe destination component is not a directory: {current}")
    return current


def _ensure_tts_simulation_directory(project_root):
    """Create the fixed mock destination using the shared safe path walk."""
    return _ensure_project_output_directory(project_root, TTS_SIMULATION_DIRECTORY)


def _write_exclusive_file(path, payload, owned_files):
    """Create a file without replacement and record identity before writing bytes."""
    with Path(path).open("xb") as stream:
        identity = _file_identity(os.fstat(stream.fileno()))
        owned_files[Path(path)] = identity
        stream.write(payload)
        stream.flush()


def _read_owned_file(path, maximum_size, identity):
    """Read a bounded regular file only while its recorded identity still matches."""
    path = Path(path)
    before = _inspect_path(path)
    if before is None or not stat.S_ISREG(before.st_mode) or identity is None:
        raise ValueError(f"cannot verify identity of published file: {path}")
    if _file_identity(before) != identity:
        raise ValueError(f"published file identity changed: {path}")
    with path.open("rb") as stream:
        payload = stream.read(maximum_size + 1)
    after = _inspect_path(path)
    if after is None or _file_identity(after) != identity:
        raise ValueError(f"published file identity changed while reading: {path}")
    if len(payload) > maximum_size:
        raise ValueError(f"published file exceeds maximum size of {maximum_size} bytes: {path}")
    return payload


def _remove_owned_file(path, identity):
    """Unlink only a regular, non-reparse file whose identity we recorded at creation."""
    path = Path(path)
    if identity is None:
        return False
    try:
        entry = _inspect_path(path)
        if entry is None or not stat.S_ISREG(entry.st_mode) or _file_identity(entry) != identity:
            return False
        os.unlink(path)
        return True
    except (OSError, ValueError):
        return False


def _cleanup_tts_publication(run_dir, run_identity, owned_files, allowed_files=None):
    """Remove only verified files from this run and then its still-owned empty directory."""
    run_dir = Path(run_dir)
    allowed_files = TTS_SIMULATION_FILES if allowed_files is None else allowed_files
    remaining = False
    for path, identity in owned_files.items():
        if path.parent != run_dir or path.name not in allowed_files:
            remaining = True
            continue
        if not _remove_owned_file(path, identity):
            if os.path.lexists(path):
                remaining = True
    if run_identity is None:
        remaining = True
    else:
        try:
            entry = _inspect_path(run_dir)
            if entry is None or not stat.S_ISDIR(entry.st_mode) or _file_identity(entry) != run_identity:
                remaining = True
            elif not remaining:
                os.rmdir(run_dir)
        except (OSError, ValueError):
            remaining = True
    return remaining


def _verify_tts_publication(run_dir, wav_bytes, provenance, owned_files):
    wav_path = Path(run_dir) / "mock-narration.wav"
    provenance_path = Path(run_dir) / "mock-provenance.json"
    actual_wav = _read_owned_file(wav_path, MAX_MOCK_WAV_BYTES, owned_files.get(wav_path))
    if actual_wav != wav_bytes:
        raise ValueError("published WAV bytes do not match the simulated output")
    audio_effective, frames, duration_ms = _inspect_mock_wav(actual_wav)
    if (
        hashlib.sha256(actual_wav).hexdigest() != provenance["wav_sha256"]
        or audio_effective != provenance["audio"]
        or frames != provenance["frames"]
        or duration_ms != provenance["duration_ms"]
    ):
        raise ValueError("published WAV verification does not match provenance")

    provenance_bytes = _read_owned_file(
        provenance_path, MAX_MOCK_PROVENANCE_BYTES, owned_files.get(provenance_path)
    )
    try:
        parsed_provenance = STORYBOARD_VALIDATOR.parse_json(provenance_bytes.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"published provenance JSON is invalid: {exc}") from exc
    if parsed_provenance != provenance:
        raise ValueError("published provenance JSON does not match the expected simulation")
    if (
        parsed_provenance.get("simulation") is not True
        or parsed_provenance.get("backend") != "mock"
        or parsed_provenance.get("provider_executed") is not None
        or parsed_provenance.get("wav_sha256") != hashlib.sha256(actual_wav).hexdigest()
    ):
        raise ValueError("published provenance does not verify the mock WAV")
    entries = {_entry.name for _entry in Path(run_dir).iterdir()}
    if entries != TTS_SIMULATION_FILES:
        raise ValueError("publication directory contains unexpected or incomplete files")


def save_tts_simulation(config_path):
    """Generate the existing mock output and publish it under a fixed project-local path."""
    simulation = simulate_tts(config_path, backend="mock")
    project_root = Path(config_path).resolve().parent
    output_root = _ensure_tts_simulation_directory(project_root)
    run_dir = output_root / f"run-{uuid.uuid4().hex}"
    owned_files = {}
    run_identity = None
    run_created = False
    try:
        os.mkdir(run_dir, 0o700)
        run_created = True
        run_entry = _inspect_path(run_dir)
        if run_entry is None or not stat.S_ISDIR(run_entry.st_mode):
            raise ValueError(f"created run path is not a regular directory: {run_dir}")
        run_identity = _file_identity(run_entry)

        wav_path = run_dir / "mock-narration.wav"
        provenance_path = run_dir / "mock-provenance.json"
        _write_exclusive_file(wav_path, simulation["wav_bytes"], owned_files)
        wav_check = _read_owned_file(wav_path, MAX_MOCK_WAV_BYTES, owned_files.get(wav_path))
        audio_effective, frames, duration_ms = _inspect_mock_wav(wav_check)
        if (
            wav_check != simulation["wav_bytes"]
            or audio_effective != simulation["provenance"]["audio"]
            or frames != simulation["provenance"]["frames"]
            or duration_ms != simulation["provenance"]["duration_ms"]
        ):
            raise ValueError("published WAV does not match the in-memory simulation")

        provenance_bytes = (
            json.dumps(simulation["provenance"], ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        if len(provenance_bytes) > MAX_MOCK_PROVENANCE_BYTES:
            raise ValueError(f"mock provenance exceeds maximum size of {MAX_MOCK_PROVENANCE_BYTES} bytes")
        _write_exclusive_file(provenance_path, provenance_bytes, owned_files)
        _verify_tts_publication(run_dir, simulation["wav_bytes"], simulation["provenance"], owned_files)
    except Exception as exc:
        if not run_created:
            raise
        retained = _cleanup_tts_publication(run_dir, run_identity, owned_files)
        if retained:
            raise ValueError(
                f"TTS simulation publication failed: {exc}; partial publication retained at {run_dir}"
            ) from exc
        raise

    summary = dict(simulation["summary"])
    summary.update(
        publication_dir=str(run_dir),
        wav_path=str(run_dir / "mock-narration.wav"),
        provenance_path=str(run_dir / "mock-provenance.json"),
    )
    return {"summary": summary, "provenance": simulation["provenance"], "publication_dir": str(run_dir)}


def _kokoro_stage(config_path):
    """Validate a local Kokoro pilot contract and capture its exact script bytes."""
    captured_resources = {}
    config, errors, _, _ = load_and_validate_config(config_path, captured_resources)
    if errors:
        raise ValueError("pipeline validation failed: " + "; ".join(errors))
    stage = next(
        (item for item in config["stages"] if item.get("id") == TTS_NARRATION_STAGE_ID),
        None,
    )
    if stage is None:
        raise ValueError(f"local Kokoro execution requires {TTS_NARRATION_STAGE_ID}")
    tts = stage["tts"]
    if tts["provider"] != "kokoro":
        raise ValueError("local Kokoro execution requires provider 'kokoro'")
    if tts["voice"] != KOKORO_VOICE:
        raise ValueError(f"local Kokoro pilot currently requires voice '{KOKORO_VOICE}'")
    if tts["language"] != KOKORO_LANGUAGE:
        raise ValueError(f"local Kokoro pilot currently requires language '{KOKORO_LANGUAGE}'")
    if tts["audio"] != KOKORO_AUDIO:
        raise ValueError("local Kokoro pilot requires WAV mono 24000 Hz pcm_s16le")
    if stage.get("adapter_status") != "pending":
        raise ValueError("local pilot does not change or complete the productive adapter status")
    script_bytes = captured_resources.get(VOICEOVER_SCRIPT_ID)
    if not isinstance(script_bytes, bytes):
        raise ValueError("validated voiceover script bytes were not captured")
    if len(script_bytes) > MAX_KOKORO_SCRIPT_BYTES:
        raise ValueError(f"pilot script exceeds {MAX_KOKORO_SCRIPT_BYTES} bytes")
    return config, stage, script_bytes


def _hash_local_runtime_file(value, label, maximum_size):
    """Hash one explicit local regular file after rejecting link/reparse components."""
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError(f"{label} path must be provided explicitly")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current = current / part
        entry = _inspect_path(current)
        if entry is None:
            raise ValueError(f"{label} file or parent does not exist: {current}")
        is_leaf = index == len(parts) - 1
        if is_leaf:
            if not stat.S_ISREG(entry.st_mode):
                raise ValueError(f"{label} is not a regular file: {current}")
        elif not stat.S_ISDIR(entry.st_mode):
            raise ValueError(f"{label} parent is not a regular directory: {current}")
    entry = _inspect_path(path)
    identity = _file_identity(entry) if entry is not None else None
    if entry is None or identity is None:
        raise ValueError(f"cannot verify filesystem identity for {label}: {path}")
    if entry.st_size > maximum_size:
        raise ValueError(f"{label} exceeds maximum size of {maximum_size} bytes")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if _file_identity(os.fstat(stream.fileno())) != identity:
            raise ValueError(f"{label} identity changed while opening")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        final_stat = os.fstat(stream.fileno())
    after = _inspect_path(path)
    if (
        after is None
        or _file_identity(after) != identity
        or _file_identity(final_stat) != identity
        or final_stat.st_size != entry.st_size
    ):
        raise ValueError(f"{label} changed while hashing")
    return path, digest.hexdigest(), entry.st_size


def _inspect_kokoro_wav(data):
    """Verify real Kokoro WAV structure, bounds, signal, and PCM clipping."""
    if not isinstance(data, bytes) or len(data) < 44:
        raise ValueError("Kokoro worker did not return a complete WAV")
    if len(data) > MAX_KOKORO_WAV_BYTES:
        raise ValueError(f"Kokoro WAV exceeds pilot maximum of {MAX_KOKORO_WAV_BYTES} bytes")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE" or struct.unpack_from("<I", data, 4)[0] != len(data) - 8:
        raise ValueError("Kokoro output has an invalid RIFF/WAVE header or size")
    chunks = {}
    offset = 12
    while offset < len(data):
        if len(data) - offset < 8:
            raise ValueError("Kokoro WAV has a truncated RIFF chunk header")
        chunk_id = data[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        end = start + chunk_size
        if end > len(data) or chunk_id in chunks:
            raise ValueError("Kokoro WAV has a truncated or duplicate RIFF chunk")
        chunks[chunk_id] = data[start:end]
        offset = end + (chunk_size & 1)
    if offset != len(data) or b"fmt " not in chunks or b"data" not in chunks:
        raise ValueError("Kokoro WAV is missing required RIFF chunks")
    fmt = chunks[b"fmt "]
    if len(fmt) < 16:
        raise ValueError("Kokoro WAV fmt chunk is malformed")
    audio_format, channels, sample_rate, byte_rate, block_align, bits = struct.unpack_from("<HHIIHH", fmt)
    if (audio_format, channels, sample_rate, bits) != (1, 1, 24000, 16):
        raise ValueError("Kokoro WAV must be mono PCM16 at 24000 Hz")
    if block_align != 2 or byte_rate != 48000 or len(chunks[b"data"]) % block_align:
        raise ValueError("Kokoro WAV PCM frame alignment is inconsistent")
    pcm = chunks[b"data"]
    frames = len(pcm) // block_align
    duration_seconds = frames / sample_rate
    if frames <= 0 or duration_seconds > MAX_KOKORO_DURATION_SECONDS:
        raise ValueError("Kokoro WAV is empty or exceeds the pilot duration limit")
    count = 0
    peak = 0
    sum_squares = 0
    full_scale = 0
    for (sample,) in struct.iter_unpack("<h", pcm):
        count += 1
        magnitude = abs(sample)
        peak = max(peak, magnitude)
        sum_squares += sample * sample
        if sample in (-32768, 32767):
            full_scale += 1
    if count != frames or peak == 0:
        raise ValueError("Kokoro WAV contains no non-silent PCM signal")
    severe_clipping = full_scale > max(3, int(count * 0.001))
    if severe_clipping:
        raise ValueError("Kokoro WAV has severe digital clipping")
    return {
        "container": "wav",
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "sample_format": "pcm_s16le",
        "frames": frames,
        "duration_seconds": duration_seconds,
        "size_bytes": len(data),
        "peak_abs_pcm16": peak,
        "rms_pcm16": (sum_squares / count) ** 0.5,
        "full_scale_samples": full_scale,
        "severe_clipping_detected": False,
        "signal_non_silent": True,
    }


def _verify_kokoro_publication(run_dir, provenance, owned_files):
    wav_path = Path(run_dir) / "narration.wav"
    provenance_path = Path(run_dir) / "narration-provenance.json"
    wav_bytes = _read_owned_file(wav_path, MAX_KOKORO_WAV_BYTES, owned_files.get(wav_path))
    audio = _inspect_kokoro_wav(wav_bytes)
    if (
        hashlib.sha256(wav_bytes).hexdigest() != provenance["hashes"]["wav_sha256"]
        or audio != provenance["audio"]
    ):
        raise ValueError("published real WAV does not match its provenance")
    provenance_bytes = _read_owned_file(
        provenance_path, MAX_KOKORO_PROVENANCE_BYTES, owned_files.get(provenance_path)
    )
    try:
        parsed = STORYBOARD_VALIDATOR.parse_json(provenance_bytes.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"published Kokoro provenance JSON is invalid: {exc}") from exc
    if parsed != provenance:
        raise ValueError("published Kokoro provenance does not match the expected record")
    if (
        parsed.get("simulation") is not False
        or parsed.get("execution_mode") != "local-standalone"
        or parsed.get("productive_stage_completed") is not False
        or parsed.get("manifest_updated") is not False
        or parsed.get("hashes", {}).get("wav_sha256") != hashlib.sha256(wav_bytes).hexdigest()
    ):
        raise ValueError("published provenance misidentifies the local pilot")
    if {entry.name for entry in Path(run_dir).iterdir()} != TTS_LOCAL_RUN_FILES:
        raise ValueError("local Kokoro run directory is incomplete or contains unexpected files")


def run_tts_kokoro(config_path, *, python_path, model_path, voices_path):
    """Run one validated Kokoro inference and publish it outside the productive DAG."""
    config, stage, script_bytes = _kokoro_stage(config_path)
    python_file, _, _ = _hash_local_runtime_file(python_path, "Python interpreter", 512 * 1024 * 1024)
    if os.name == "nt" and python_file.suffix.lower() != ".exe":
        raise ValueError("Python interpreter must be an explicit .exe path on Windows")
    if os.name != "nt" and not os.access(python_file, os.X_OK):
        raise ValueError("Python interpreter is not executable")
    model_file, model_sha256, model_size = _hash_local_runtime_file(
        model_path, "Kokoro model", 1024 * 1024 * 1024
    )
    voices_file, voices_sha256, voices_size = _hash_local_runtime_file(
        voices_path, "Kokoro voices", 256 * 1024 * 1024
    )
    worker = Path(__file__).with_name("tts-kokoro-worker.py")
    worker_entry = _inspect_path(worker)
    if worker_entry is None or not stat.S_ISREG(worker_entry.st_mode):
        raise ValueError("fixed Kokoro worker is missing or unsafe")

    command = [
        str(python_file),
        str(worker),
        "--model", str(model_file),
        "--voices", str(voices_file),
        "--voice", KOKORO_VOICE,
        "--language", KOKORO_LANGUAGE,
    ]
    worker_env = os.environ.copy()
    worker_env["ONNX_PROVIDER"] = "CPUExecutionProvider"
    worker_env["PYTHONDONTWRITEBYTECODE"] = "1"
    worker_env["PYTHONNOUSERSITE"] = "1"
    worker_env.pop("PYTHONPATH", None)
    worker_env.pop("PYTHONHOME", None)
    try:
        completed = subprocess.run(
            command,
            input=script_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=KOKORO_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            cwd=Path(config_path).resolve().parent,
            env=worker_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Kokoro worker exceeded {KOKORO_TIMEOUT_SECONDS} second timeout; no retry was attempted") from exc
    if completed.returncode != 0:
        raise ValueError(f"Kokoro worker failed with exit code {completed.returncode}; no retry was attempted")
    wav_bytes = completed.stdout
    if len(wav_bytes) > MAX_KOKORO_WAV_BYTES:
        raise ValueError(f"Kokoro worker output exceeds maximum size of {MAX_KOKORO_WAV_BYTES} bytes")
    marker = b"KOKORO_RESULT="
    result_line = next(
        (line[len(marker):] for line in reversed(completed.stderr.splitlines()) if line.startswith(marker)),
        None,
    )
    if result_line is None:
        raise ValueError("Kokoro worker did not return a result record")
    try:
        worker_result = STORYBOARD_VALIDATOR.parse_json(result_line.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Kokoro worker returned invalid result JSON") from exc
    if not isinstance(worker_result, dict) or worker_result.get("error"):
        raise ValueError("Kokoro worker reported a synthesis error")
    audio = _inspect_kokoro_wav(wav_bytes)
    if worker_result.get("provider") != "kokoro-onnx":
        raise ValueError("Kokoro worker returned an unexpected runtime provider")
    if worker_result.get("onnx_execution_providers") != ["CPUExecutionProvider"]:
        raise ValueError("Kokoro worker did not use CPUExecutionProvider")
    if worker_result.get("voice") != KOKORO_VOICE or worker_result.get("language") != KOKORO_LANGUAGE:
        raise ValueError("Kokoro worker returned unexpected voice or language")
    worker_audio = worker_result.get("audio")
    if not isinstance(worker_audio, dict) or any(
        worker_audio.get(key) != value
        for key, value in {
            "container": "wav",
            "sample_rate_hz": audio["sample_rate_hz"],
            "channels": audio["channels"],
            "sample_format": audio["sample_format"],
            "frames": audio["frames"],
            "duration_seconds": audio["duration_seconds"],
            "size_bytes": len(wav_bytes),
            "sha256": hashlib.sha256(wav_bytes).hexdigest(),
        }.items()
    ):
        raise ValueError("Kokoro worker audio report does not match returned WAV")
    versions = worker_result.get("versions")
    expected_versions = {"kokoro-onnx", "onnxruntime", "numpy", "phonemizer", "espeakng-loader", "soundfile"}
    if (
        not isinstance(versions, dict)
        or set(versions) != expected_versions
        or any(not isinstance(value, str) or not value for value in versions.values())
    ):
        raise ValueError("Kokoro worker runtime versions are incomplete")
    if versions.get("kokoro-onnx") != KOKORO_RUNTIME_VERSION:
        raise ValueError(f"Kokoro runtime must be version {KOKORO_RUNTIME_VERSION}")
    if not isinstance(worker_result.get("python_version"), str) or not worker_result["python_version"]:
        raise ValueError("Kokoro worker returned no Python version")
    timing = worker_result.get("timing_seconds")
    if not isinstance(timing, dict) or any(
        not isinstance(timing.get(key), (int, float)) or isinstance(timing.get(key), bool)
        or not math.isfinite(timing[key]) or timing[key] < 0
        for key in ("model_initialization", "inference")
    ):
        raise ValueError("Kokoro worker returned invalid timing measurements")
    peak_memory = worker_result.get("peak_working_set_bytes")
    if peak_memory is not None and (
        not isinstance(peak_memory, int) or isinstance(peak_memory, bool) or peak_memory < 0
    ):
        raise ValueError("Kokoro worker returned invalid memory measurement")
    model_after = _hash_local_runtime_file(model_file, "Kokoro model", 1024 * 1024 * 1024)
    voices_after = _hash_local_runtime_file(voices_file, "Kokoro voices", 256 * 1024 * 1024)
    if model_after[1] != model_sha256 or voices_after[1] != voices_sha256:
        raise ValueError("Kokoro model or voice file changed during inference")

    script_sha256 = hashlib.sha256(script_bytes).hexdigest()
    wav_sha256 = hashlib.sha256(wav_bytes).hexdigest()
    provenance = {
        "provenance_version": 1,
        "record_type": "local-kokoro-speech-pilot",
        "simulation": False,
        "execution_mode": "local-standalone",
        "dag_stage_id": stage["id"],
        "productive_stage_completed": False,
        "manifest_updated": False,
        "attempts": 1,
        "automatic_retries": False,
        "provider_requested": "kokoro",
        "provider_executed": worker_result["provider"],
        "onnx_execution_providers": worker_result["onnx_execution_providers"],
        "voice": KOKORO_VOICE,
        "language": KOKORO_LANGUAGE,
        "requested_audio": dict(KOKORO_AUDIO),
        "audio": audio,
        "hashes": {
            "script_sha256": script_sha256,
            "wav_sha256": wav_sha256,
            "model_sha256": model_sha256,
            "voices_sha256": voices_sha256,
        },
        "assets": {
            "model_file": model_file.name,
            "model_size_bytes": model_size,
            "voices_file": voices_file.name,
            "voices_size_bytes": voices_size,
        },
        "versions": {"python": worker_result.get("python_version"), **versions},
        "timing_seconds": timing,
        "peak_working_set_bytes": peak_memory,
        "timeout_seconds": KOKORO_TIMEOUT_SECONDS,
        "script_bytes": len(script_bytes),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    provenance_bytes = (json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(provenance_bytes) > MAX_KOKORO_PROVENANCE_BYTES:
        raise ValueError(f"Kokoro provenance exceeds maximum size of {MAX_KOKORO_PROVENANCE_BYTES} bytes")

    project_root = Path(config_path).resolve().parent
    output_root = _ensure_project_output_directory(project_root, TTS_LOCAL_RUN_DIRECTORY)
    run_dir = output_root / f"run-{uuid.uuid4().hex}"
    owned_files = {}
    run_identity = None
    run_created = False
    try:
        os.mkdir(run_dir, 0o700)
        run_created = True
        run_entry = _inspect_path(run_dir)
        if run_entry is None or not stat.S_ISDIR(run_entry.st_mode):
            raise ValueError(f"created run path is not a regular directory: {run_dir}")
        run_identity = _file_identity(run_entry)
        wav_path = run_dir / "narration.wav"
        provenance_path = run_dir / "narration-provenance.json"
        _write_exclusive_file(wav_path, wav_bytes, owned_files)
        published_wav = _read_owned_file(wav_path, MAX_KOKORO_WAV_BYTES, owned_files.get(wav_path))
        if _inspect_kokoro_wav(published_wav) != audio or hashlib.sha256(published_wav).hexdigest() != wav_sha256:
            raise ValueError("published Kokoro WAV failed post-write verification")
        _write_exclusive_file(provenance_path, provenance_bytes, owned_files)
        _verify_kokoro_publication(run_dir, provenance, owned_files)
    except Exception as exc:
        if not run_created:
            raise
        retained = _cleanup_tts_publication(run_dir, run_identity, owned_files, TTS_LOCAL_RUN_FILES)
        if retained:
            raise ValueError(f"Kokoro publication failed: {exc}; partial run retained at {run_dir}") from exc
        raise

    summary = {
        "simulation": False,
        "execution_mode": "local-standalone",
        "productive_stage_completed": False,
        "provider": "kokoro-onnx",
        "voice": KOKORO_VOICE,
        "language": KOKORO_LANGUAGE,
        "audio": audio,
        "hashes": provenance["hashes"],
        "publication_dir": str(run_dir),
        "wav_path": str(run_dir / "narration.wav"),
        "provenance_path": str(run_dir / "narration-provenance.json"),
    }
    return {"summary": summary, "provenance": provenance, "publication_dir": str(run_dir)}


def _input_fingerprint(config, config_path):
    root = Path(config_path).resolve().parent
    inputs = [("storyboard", config["project"]["storyboard"])]
    inputs.extend((f"resource-{item['id']}", item["path"]) for item in config["project"]["resources"])
    values = []
    for input_id, relative in sorted(inputs):
        path = root.joinpath(*PurePosixPath(relative).parts).resolve()
        if path.is_file():
            values.append({"id": input_id, "path": relative, "sha256": sha256_file(path)})
    return fingerprint(values)


def _project_input_hashes(config, config_path):
    root = Path(config_path).resolve().parent
    inputs = {"storyboard": config["project"]["storyboard"]}
    inputs.update({f"resource-{item['id']}": item["path"] for item in config["project"]["resources"]})
    hashes = {}
    for input_id, relative in inputs.items():
        path = root.joinpath(*PurePosixPath(relative).parts).resolve()
        if path.is_file():
            hashes[input_id] = sha256_file(path)
    return hashes


def stage_fingerprint(config, stage, input_hashes, tool_versions):
    """Fingerprint one stage's effective config, inputs, and resolved tool versions."""
    tools = {tool["id"]: tool for tool in config["tools"] if tool["id"] in stage["tools"]}
    return fingerprint({
        "pipeline_version": config["pipeline_version"],
        "profile": config["profile"],
        "stage": stage,
        "inputs": input_hashes,
        "tools": tools,
        "tool_versions": tool_versions,
    })


def new_pending_manifest(config, config_path):
    """Create an in-memory pending manifest; no file is written."""
    stages = {}
    for stage in config.get("stages", []):
        stages[stage["id"]] = {
            "status": "pending",
            "attempts": 0,
            "error": None,
            "depends_on": list(stage["depends_on"]),
            "artifacts": [],
            "stage_fingerprint": None,
            "tool_versions": {},
        }
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": str(uuid.uuid4()),
        "project_id": config.get("project", {}).get("id"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "config_fingerprint": fingerprint(config),
        "inputs_fingerprint": _input_fingerprint(config, config_path),
        "stages": stages,
    }


def validate_manifest(manifest, config, config_path, manifest_path):
    errors, warnings = [], []
    if not _object(
        manifest,
        "manifest",
        ["manifest_version", "run_id", "project_id", "created_at", "config_fingerprint", "inputs_fingerprint", "stages"],
        [],
        errors,
    ):
        return errors, warnings
    version = manifest.get("manifest_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != MANIFEST_VERSION:
        errors.append(f"manifest_version must be integer {MANIFEST_VERSION}")
    try:
        uuid.UUID(manifest.get("run_id", ""))
    except (ValueError, AttributeError, TypeError):
        errors.append("manifest.run_id must be a UUID")
    if manifest.get("project_id") != config.get("project", {}).get("id"):
        errors.append("manifest.project_id does not match pipeline project.id")
    created = manifest.get("created_at")
    if not isinstance(created, str):
        errors.append("manifest.created_at must be an ISO-8601 timestamp with timezone")
    else:
        try:
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                errors.append("manifest.created_at must include a timezone")
        except ValueError:
            errors.append("manifest.created_at must be an ISO-8601 timestamp with timezone")
    for field in ("config_fingerprint", "inputs_fingerprint"):
        if not isinstance(manifest.get(field), str) or not FINGERPRINT.fullmatch(manifest[field]):
            errors.append(f"manifest.{field} must be a lowercase SHA-256 fingerprint")
    if manifest.get("config_fingerprint") != fingerprint(config):
        warnings.append("pipeline config changed since this run; validate stage fingerprints before resuming")
    try:
        current_input_fingerprint = _input_fingerprint(config, config_path)
    except OSError as exc:
        errors.append(f"cannot fingerprint project inputs: {exc}")
        current_input_fingerprint = None
    if current_input_fingerprint is not None and manifest.get("inputs_fingerprint") != current_input_fingerprint:
        warnings.append("project inputs changed since this run; affected stages must be invalidated before resuming")

    stage_entries = manifest.get("stages")
    configured = {stage["id"]: stage for stage in config.get("stages", []) if isinstance(stage, dict) and isinstance(stage.get("id"), str)}
    if not isinstance(stage_entries, dict):
        errors.append("manifest.stages must be an object keyed by stage id")
        return errors, warnings
    if set(stage_entries) != set(configured):
        errors.append("manifest.stages must contain exactly the configured stage ids")
    retry_policy = config.get("retry_policy", {})
    max_attempts = retry_policy.get("max_attempts", 0) if isinstance(retry_policy, dict) else 0
    root = Path(config_path).resolve().parent
    output_by_id = {
        output["id"]: output
        for stage in configured.values()
        for output in stage.get("outputs", [])
        if isinstance(output, dict) and isinstance(output.get("id"), str)
    }
    configured_tools = {
        tool["id"]: tool for tool in config.get("tools", [])
        if isinstance(tool, dict) and isinstance(tool.get("id"), str)
    }
    artifact_digests = {}
    for candidate in stage_entries.values():
        if not isinstance(candidate, dict) or not isinstance(candidate.get("artifacts"), list):
            continue
        for artifact in candidate["artifacts"]:
            if isinstance(artifact, dict) and isinstance(artifact.get("id"), str):
                artifact_digests[artifact["id"]] = artifact.get("sha256")
    for stage_id, entry in stage_entries.items():
        label = f"manifest.stages.{stage_id}"
        if stage_id not in configured:
            continue
        required = ["status", "attempts", "error", "depends_on", "artifacts", "stage_fingerprint", "tool_versions"]
        if not _object(entry, label, required, [], errors):
            continue
        stage = configured[stage_id]
        state = entry.get("status")
        if not isinstance(state, str) or state not in STAGE_STATES:
            errors.append(f"{label}.status is unsupported")
        attempts = entry.get("attempts")
        if not isinstance(attempts, int) or isinstance(attempts, bool) or not 0 <= attempts <= max_attempts:
            errors.append(f"{label}.attempts must be an integer from 0 to retry_policy.max_attempts")
        if entry.get("depends_on") != stage.get("depends_on"):
            errors.append(f"{label}.depends_on does not match pipeline stage dependencies")
        error_text = entry.get("error")
        if error_text is not None and (not isinstance(error_text, str) or not error_text.strip()):
            errors.append(f"{label}.error must be null or a non-empty string")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, list):
            errors.append(f"{label}.artifacts must be an array")
            artifacts = []
        declared_stage_fingerprint = entry.get("stage_fingerprint")
        if declared_stage_fingerprint is not None and (
            not isinstance(declared_stage_fingerprint, str) or not FINGERPRINT.fullmatch(declared_stage_fingerprint)
        ):
            errors.append(f"{label}.stage_fingerprint must be null or a lowercase SHA-256 fingerprint")
        tool_versions = entry.get("tool_versions")
        if not isinstance(tool_versions, dict) or any(
            not isinstance(tool_id, str) or not isinstance(version, str) or not version.strip()
            for tool_id, version in (tool_versions.items() if isinstance(tool_versions, dict) else [])
        ):
            errors.append(f"{label}.tool_versions must map tool ids to non-empty version strings")
            tool_versions = {}
        if state == "pending":
            if attempts != 0 or error_text is not None or artifacts or declared_stage_fingerprint is not None or tool_versions:
                errors.append(f"{label} pending state cannot contain attempts, error, artifacts, fingerprints, or tool versions")
        elif state == "blocked":
            if not isinstance(error_text, str) or not error_text.strip() or artifacts or tool_versions:
                errors.append(f"{label} blocked state requires a reason and no reusable artifacts")
        elif state == "failed":
            if not isinstance(attempts, int) or attempts < 1 or not isinstance(error_text, str) or not error_text.strip() or artifacts:
                errors.append(f"{label} failed state requires an attempt, an error, and no reusable artifacts")
            if declared_stage_fingerprint is not None and not isinstance(declared_stage_fingerprint, str):
                errors.append(f"{label}.stage_fingerprint must be null or a SHA-256 fingerprint")
        elif state == "completed":
            if not isinstance(attempts, int) or attempts < 1 or error_text is not None:
                errors.append(f"{label} completed state requires an attempt and no error")
            if stage.get("adapter_status") != "implemented":
                errors.append(f"{label} cannot be completed while its adapter is pending")
            for tool_id in stage.get("tools", []):
                tool = configured_tools.get(tool_id)
                if tool is None:
                    continue
                if tool.get("availability") != "verified":
                    errors.append(
                        f"{label} cannot be completed because tool {tool_id} availability is not verified"
                    )
                if tool.get("adapter_status") != "implemented":
                    errors.append(
                        f"{label} cannot be completed because tool {tool_id} adapter is pending"
                    )
            if not isinstance(declared_stage_fingerprint, str) or not FINGERPRINT.fullmatch(declared_stage_fingerprint):
                errors.append(f"{label} completed state requires a stage fingerprint")
            dependencies = stage.get("depends_on", [])
            if any(
                not isinstance(stage_entries.get(dep), dict)
                or stage_entries[dep].get("status") != "completed"
                for dep in dependencies
            ):
                errors.append(f"{label} cannot be completed until all dependencies are completed")
            expected_tools = set(stage.get("tools", []))
            if set(tool_versions) != expected_tools:
                errors.append(f"{label}.tool_versions must contain exactly the tools used by the stage")
            expected = {item["id"]: item["path"] for item in stage.get("outputs", []) if isinstance(item, dict)}
            actual = {}
            for index, artifact in enumerate(artifacts):
                artifact_label = f"{label}.artifacts[{index}]"
                if not _object(artifact, artifact_label, ["id", "path", "sha256"], [], errors):
                    continue
                artifact_id = artifact.get("id")
                artifact_path = artifact.get("path")
                digest = artifact.get("sha256")
                if not isinstance(artifact_id, str) or artifact_id not in output_by_id or artifact_id in actual:
                    errors.append(f"{artifact_label}.id is unknown or duplicated")
                    continue
                actual[artifact_id] = artifact_path
                if expected.get(artifact_id) != artifact_path:
                    errors.append(f"{artifact_label} does not match the configured output path")
                if not isinstance(digest, str) or not FINGERPRINT.fullmatch(digest):
                    errors.append(f"{artifact_label}.sha256 must be a lowercase SHA-256 fingerprint")
                    continue
                resolved = _path(root, artifact_path, f"{artifact_label}.path", errors, required_file=True)
                if resolved is not None and resolved.is_file():
                    try:
                        if sha256_file(resolved) != digest:
                            errors.append(f"{artifact_label} artifact hash does not match the file")
                    except OSError as exc:
                        errors.append(f"{artifact_label} cannot be read for hash verification: {exc}")
            if actual != expected:
                errors.append(f"{label} completed state must reference every configured output exactly once")
            input_hashes = {}
            source_hashes = _project_input_hashes(config, config_path)
            for input_id in stage.get("inputs", []):
                if input_id in source_hashes:
                    input_hashes[input_id] = source_hashes[input_id]
                else:
                    producer_hash = artifact_digests.get(input_id)
                    if producer_hash is not None:
                        input_hashes[input_id] = producer_hash
                    else:
                        errors.append(f"{label} has no completed input artifact fingerprint for {input_id}")
            if set(input_hashes) == set(stage.get("inputs", [])) and isinstance(declared_stage_fingerprint, str):
                calculated = stage_fingerprint(config, stage, input_hashes, tool_versions)
                if calculated != declared_stage_fingerprint:
                    errors.append(f"{label}.stage_fingerprint is stale for current config, inputs, or tool versions")
    return errors, warnings


def validate_manifest_file(config_path, manifest_path):
    config, errors, warnings, _ = load_and_validate_config(config_path)
    if config is None or errors:
        return errors, warnings
    try:
        manifest = _strict_load(manifest_path)
    except (OSError, UnicodeError, ValueError) as exc:
        return [f"cannot read manifest JSON: {exc}"], warnings
    manifest_errors, manifest_warnings = validate_manifest(manifest, config, config_path, manifest_path)
    return errors + manifest_errors, warnings + manifest_warnings


def _print_validation(config, warnings):
    print(f"PASS: pipeline_version {PIPELINE_VERSION}; project {config['project']['id']}; storyboard valid")
    for warning in warnings:
        print(f"WARN: {warning}")


def _print_plan(config, waves):
    limits = config["profile"]["limits"]
    print("DRY RUN - no stages executed; no files written; no tools launched.")
    print(
        f"Project: {config['project']['id']} | {config['profile']['width']}x{config['profile']['height']} "
        f"@ {config['profile']['fps']} fps | {config['profile']['format']}"
    )
    print(
        f"Resource budget: CPU {limits['cpu_threads']} threads, RAM {limits['memory_mb']} MB, "
        f"VRAM {limits['vram_mb']} MB; configured parallel stages {limits['max_parallel_stages']}"
    )
    by_id = {stage["id"]: stage for stage in config["stages"]}
    tool_by_id = {tool["id"]: tool for tool in config["tools"]}
    planned_states = {}
    for wave_index, stage_ids in enumerate(waves, 1):
        print(f"Wave {wave_index} (graph order):")
        for stage_id in stage_ids:
            stage = by_id[stage_id]
            dependencies = ", ".join(stage["depends_on"]) or "none"
            inputs = ", ".join(stage["inputs"]) or "none"
            outputs = ", ".join(output["path"] for output in stage["outputs"])
            blocked_dependencies = [
                dependency for dependency in stage["depends_on"]
                if planned_states.get(dependency, "").startswith("blocked")
            ]
            reasons = []
            if stage["adapter_status"] == "pending":
                reasons.append("adapter pending")
            for tool_id in stage["tools"]:
                tool = tool_by_id[tool_id]
                if tool["adapter_status"] == "pending":
                    reasons.append(f"tool {tool_id} adapter pending")
                if tool["availability"] != "verified":
                    reasons.append(f"tool {tool_id} availability not verified")
            if blocked_dependencies:
                reasons.append("dependencies blocked: " + ", ".join(blocked_dependencies))
            state = f"blocked ({'; '.join(reasons)})" if reasons else "planned (not executed)"
            planned_states[stage_id] = state
            print(
                f"  {stage_id}: {state}; deps=[{dependencies}]; "
                f"inputs=[{inputs}]; outputs=[{outputs}]; adapter={stage['adapter_status']}"
            )
            tts = stage.get("tts")
            if isinstance(tts, dict):
                audio = tts["audio"]
                print(
                    "    TTS contract (declarative; runtime not probed): "
                    f"provider={tts['provider']}; voice={tts['voice']}; language={tts['language']}; "
                    f"audio={audio['container']}/{audio['sample_rate_hz']}Hz/"
                    f"{audio['channels']}ch/{audio['sample_format']}; script={tts['script_input']}"
                )
            for tool_id in stage["tools"]:
                tool = tool_by_id[tool_id]
                print(
                    f"    tool {tool_id}: declared availability={tool['availability']} (runtime not probed); "
                    f"adapter={tool['adapter_status']}; expected={tool['version_requirement']}"
                )
        if len(stage_ids) > 1:
            if limits["max_parallel_stages"] > 1:
                print("  Independent graph stages; future parallel execution is possible within the configured limit.")
            else:
                print("  Independent graph stages; serialized by the conservative concurrency limit.")
    print("Production adapters are not executable; the TTS contract is declarative only.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "plan", "dry-run"):
        subparser = commands.add_parser(command)
        subparser.add_argument("config", help="pipeline JSON file; its directory is the project root")
    manifest_parser = commands.add_parser("validate-manifest")
    manifest_parser.add_argument("config", help="pipeline JSON file")
    manifest_parser.add_argument("manifest", help="run manifest JSON file")
    simulation_parser = commands.add_parser(
        "simulate-tts", help="validate and simulate TTS entirely in memory; writes no files"
    )
    simulation_parser.add_argument("config", help="pipeline JSON file; its directory is the project root")
    simulation_parser.add_argument("--backend", choices=sorted(SIMULATION_BACKENDS), required=True)
    publication_parser = commands.add_parser(
        "save-tts-simulation",
        help="publish verified mock WAV and provenance in a new project-local run directory",
    )
    publication_parser.add_argument("config", help="pipeline JSON file; its directory is the project root")
    kokoro_parser = commands.add_parser(
        "run-tts-kokoro",
        help="run one local Kokoro speech pilot without completing a productive DAG stage",
    )
    kokoro_parser.add_argument("config", help="pipeline JSON file; its directory is the project root")
    kokoro_parser.add_argument("--python", required=True, help="absolute path to the trusted Python interpreter")
    kokoro_parser.add_argument("--model", required=True, help="absolute path to the local Kokoro ONNX model")
    kokoro_parser.add_argument("--voices", required=True, help="absolute path to the local Kokoro voices file")
    args = parser.parse_args(argv)

    if args.command == "validate-manifest":
        errors, warnings = validate_manifest_file(args.config, args.manifest)
        if errors:
            print("FAIL: run manifest validation")
            for error in errors:
                print(f"- {error}")
            return 1
        print("PASS: run manifest structure, dependencies, fingerprints, and reusable artifacts")
        for warning in warnings:
            print(f"WARN: {warning}")
        return 0

    if args.command == "simulate-tts":
        try:
            result = simulate_tts(args.config, backend=args.backend)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc), "simulation": True}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    if args.command == "save-tts-simulation":
        try:
            result = save_tts_simulation(args.config)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc), "simulation": True}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    if args.command == "run-tts-kokoro":
        try:
            result = run_tts_kokoro(
                args.config,
                python_path=args.python,
                model_path=args.model,
                voices_path=args.voices,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc), "simulation": False}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    config, errors, warnings, waves = load_and_validate_config(args.config)
    if errors:
        print("FAIL: pipeline validation")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.command == "validate":
        _print_validation(config, warnings)
    else:
        for warning in warnings:
            print(f"WARN: {warning}")
        _print_plan(config, waves)
    return 0


if __name__ == "__main__":
    sys.exit(main())
