#!/usr/bin/env python3
"""Validate and preview pipelines, simulate TTS, publish mock runs, and run local Kokoro pilots."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from scene_package_builder import build_scene_package
from scene_package_validator import MAX_ARCHIVE_BYTES, ScenePackageContext, validate_scene_package


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
MAX_VALIDATED_PLAN_BYTES = 1024 * 1024
MAX_PRODUCTIVE_STORYBOARD_BYTES = 4 * 1024 * 1024
MAX_BLENDER_SCENE_SPEC_BYTES = 64 * 1024
MAX_BLENDER_PREFLIGHT_OUTPUT_BYTES = 16 * 1024
MAX_BLENDER_WORKER_REQUEST_BYTES = 128 * 1024
MAX_BLENDER_WORKER_RESPONSE_BYTES = 64 * 1024
BLENDER_PREFLIGHT_TIMEOUT_SECONDS = 15
BLENDER_WORKER_TIMEOUT_SECONDS = 30 * 60
BLENDER_TERMINATION_GRACE_SECONDS = 3
BLENDER_VERSION_PATTERN = re.compile(r"(?m)^Blender\s+(\d+)\.(\d+)\.(\d+)(?:\s|$)")
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


def _scene_spec_number(value, label, errors, minimum, maximum):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"{label} must be a finite number from {minimum} to {maximum}")
        return None
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{label} must be a finite number from {minimum} to {maximum}")
        return None
    if value < minimum or value > maximum:
        errors.append(f"{label} must be from {minimum} to {maximum}")
        return None
    return value


def _scene_spec_vector(value, label, size, minimum, maximum, errors):
    if not isinstance(value, list) or len(value) != size:
        errors.append(f"{label} must contain exactly {size} numbers")
        return None
    result = []
    valid = True
    for index, item in enumerate(value):
        number = _scene_spec_number(item, f"{label}[{index}]", errors, minimum, maximum)
        if number is None:
            valid = False
        else:
            result.append(number)
    return result if valid else None


def _validate_blender_scene_spec(spec, project_id, storyboard):
    errors = []
    if not _object(
        spec, "scene spec", ["scene_spec_version", "project_id", "scene_id", "render", "materials", "objects", "camera", "lights"], [], errors,
    ):
        return errors

    version = spec.get("scene_spec_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        errors.append("scene spec scene_spec_version must be integer 1")
    if _string(spec.get("project_id"), "scene spec project_id", errors, SLUG) and spec["project_id"] != project_id:
        errors.append("scene spec project_id must match pipeline project.id")
    if not _string(spec.get("scene_id"), "scene spec scene_id", errors, SLUG):
        scene_id = None
    else:
        scene_id = spec["scene_id"]

    if isinstance(storyboard, dict):
        scenes = storyboard.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != 1 or not isinstance(scenes[0], dict):
            errors.append("scene spec requires exactly one storyboard scene")
        else:
            if scene_id is not None and scenes[0].get("id") != scene_id:
                errors.append("scene spec scene_id must match the only storyboard scene id")
            if scenes[0].get("start_frame") != 0 or scenes[0].get("end_frame") != 24:
                errors.append("scene spec storyboard scene must cover [0,24)")

    render_fields = [
        "width", "height", "fps", "start_frame", "end_frame_exclusive", "engine", "device",
        "samples", "threads", "seed", "denoising", "image_format",
    ]
    render = spec.get("render")
    if _object(render, "scene spec render", render_fields, [], errors):
        fixed_integers = {
            "width": 540, "height": 960, "fps": 12, "start_frame": 0,
            "end_frame_exclusive": 24, "samples": 8, "threads": 2,
        }
        for key, expected in fixed_integers.items():
            value = render.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"scene spec render.{key} must be integer {expected}")
            elif value != expected:
                errors.append(f"scene spec render.{key} must equal {expected}")
        _integer(render.get("seed"), "scene spec render.seed", errors, 0, 2147483647)
        for key, expected in {
            "engine": "CYCLES", "device": "CPU", "image_format": "PNG_RGB8_SRGB",
        }.items():
            if render.get(key) != expected:
                errors.append(f"scene spec render.{key} must equal {expected}")
        if render.get("denoising") is not False:
            errors.append("scene spec render.denoising must be false")
        if isinstance(storyboard, dict):
            if render.get("fps") != storyboard.get("fps"):
                errors.append("scene spec render.fps must match storyboard.fps")
            scenes = storyboard.get("scenes")
            if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
                if render.get("start_frame") != scenes[0].get("start_frame"):
                    errors.append("scene spec render.start_frame must match storyboard scene start_frame")
            if render.get("end_frame_exclusive") != storyboard.get("total_frames"):
                errors.append("scene spec render.end_frame_exclusive must match storyboard.total_frames")

    declared_ids = set()
    material_ids = set()
    materials = spec.get("materials")
    if not isinstance(materials, list):
        errors.append("scene spec materials must be an array")
    elif not 1 <= len(materials) <= 12:
        errors.append("scene spec materials must contain from 1 to 12 entries")
    else:
        for index, material in enumerate(materials):
            label = f"scene spec materials[{index}]"
            if not _object(material, label, ["id", "base_color_linear_rgba", "metallic", "roughness"], [], errors):
                continue
            material_id = material.get("id")
            if _string(material_id, f"{label}.id", errors, SLUG):
                if material_id in declared_ids:
                    errors.append(f"{label}.id has duplicate id {material_id}")
                declared_ids.add(material_id)
                material_ids.add(material_id)
            _scene_spec_vector(material.get("base_color_linear_rgba"), f"{label}.base_color_linear_rgba", 4, 0, 1, errors)
            _scene_spec_number(material.get("metallic"), f"{label}.metallic", errors, 0, 1)
            _scene_spec_number(material.get("roughness"), f"{label}.roughness", errors, 0.05, 1)

    objects = spec.get("objects")
    if not isinstance(objects, list):
        errors.append("scene spec objects must be an array")
    elif not 1 <= len(objects) <= 24:
        errors.append("scene spec objects must contain from 1 to 24 entries")
    else:
        for index, scene_object in enumerate(objects):
            label = f"scene spec objects[{index}]"
            if not _object(scene_object, label, ["id", "primitive", "material_id", "location_m", "rotation_rad", "scale"], [], errors):
                continue
            object_id = scene_object.get("id")
            if _string(object_id, f"{label}.id", errors, SLUG):
                if object_id in declared_ids:
                    errors.append(f"{label}.id has duplicate id {object_id}")
                declared_ids.add(object_id)
            primitive = scene_object.get("primitive")
            if not isinstance(primitive, str) or primitive not in {"cube", "uv_sphere", "cylinder", "plane"}:
                errors.append(f"{label}.primitive is unsupported")
            material_id = scene_object.get("material_id")
            if not _string(material_id, f"{label}.material_id", errors, SLUG) or material_id not in material_ids:
                errors.append(f"{label}.material_id must reference a declared material id")
            _scene_spec_vector(scene_object.get("location_m"), f"{label}.location_m", 3, -20, 20, errors)
            _scene_spec_vector(scene_object.get("rotation_rad"), f"{label}.rotation_rad", 3, -6.28319, 6.28319, errors)
            _scene_spec_vector(scene_object.get("scale"), f"{label}.scale", 3, 0.05, 10, errors)

    camera = spec.get("camera")
    if _object(camera, "scene spec camera", ["lens_mm", "interpolation", "keyframes"], [], errors):
        _scene_spec_number(camera.get("lens_mm"), "scene spec camera.lens_mm", errors, 24, 50)
        if camera.get("interpolation") != "LINEAR":
            errors.append("scene spec camera.interpolation must equal LINEAR")
        keyframes = camera.get("keyframes")
        if not isinstance(keyframes, list) or len(keyframes) != 2:
            errors.append("scene spec camera.keyframes must contain exactly two keyframes")
        else:
            frames = []
            positions = []
            targets = []
            for index, keyframe in enumerate(keyframes):
                label = f"scene spec camera.keyframes[{index}]"
                if not _object(keyframe, label, ["frame", "location_m", "target_m"], [], errors):
                    continue
                frame = keyframe.get("frame")
                if not isinstance(frame, int) or isinstance(frame, bool):
                    errors.append(f"{label}.frame must be an integer")
                else:
                    frames.append(frame)
                location = _scene_spec_vector(keyframe.get("location_m"), f"{label}.location_m", 3, -20, 20, errors)
                target = _scene_spec_vector(keyframe.get("target_m"), f"{label}.target_m", 3, -20, 20, errors)
                positions.append(location)
                targets.append(target)
                if location is not None and target is not None and math.dist(location, target) <= 0.01:
                    errors.append(f"{label} camera-target distance must be greater than 0.01 m")
            if frames and frames != [0, 23]:
                errors.append("scene spec camera.keyframes must use frames 0 and 23 in order")
            if len(positions) == 2 and all(value is not None for value in positions) and len(targets) == 2 and all(value is not None for value in targets):
                camera_moved = math.dist(positions[0], positions[1]) >= 0.01 or math.dist(targets[0], targets[1]) >= 0.01
                if not camera_moved:
                    errors.append("scene spec camera must move by at least 0.01 m between keyframes")

    lights = spec.get("lights")
    if not isinstance(lights, list):
        errors.append("scene spec lights must be an array")
    elif not 1 <= len(lights) <= 3:
        errors.append("scene spec lights must contain from 1 to 3 entries")
    else:
        for index, light in enumerate(lights):
            label = f"scene spec lights[{index}]"
            if not _object(light, label, ["id", "type", "location_m", "target_m", "color_linear_rgb", "power_w", "size_m"], [], errors):
                continue
            light_id = light.get("id")
            if _string(light_id, f"{label}.id", errors, SLUG):
                if light_id in declared_ids:
                    errors.append(f"{label}.id has duplicate id {light_id}")
                declared_ids.add(light_id)
            if light.get("type") != "AREA":
                errors.append(f"{label}.type must equal AREA")
            location = _scene_spec_vector(light.get("location_m"), f"{label}.location_m", 3, -20, 20, errors)
            target = _scene_spec_vector(light.get("target_m"), f"{label}.target_m", 3, -20, 20, errors)
            if location is not None and target is not None and location == target:
                errors.append(f"{label} location and target must differ")
            _scene_spec_vector(light.get("color_linear_rgb"), f"{label}.color_linear_rgb", 3, 0, 1, errors)
            _scene_spec_number(light.get("power_w"), f"{label}.power_w", errors, 1, 2000)
            _scene_spec_number(light.get("size_m"), f"{label}.size_m", errors, 0.1, 10)

    return errors


def _validate_blender_scene_spec_file(path, project_id, storyboard):
    label = "blender-scene-spec"
    try:
        with Path(path).open("rb") as stream:
            content = stream.read(MAX_BLENDER_SCENE_SPEC_BYTES + 1)
    except OSError as exc:
        return [f"{label} cannot be read: {exc}"]
    if len(content) > MAX_BLENDER_SCENE_SPEC_BYTES:
        return [f"{label} exceeds maximum size of {MAX_BLENDER_SCENE_SPEC_BYTES} bytes"]
    try:
        text = content.decode("utf-8", errors="strict")
        spec = STORYBOARD_VALIDATOR.parse_json(text)
    except UnicodeDecodeError:
        return [f"{label} must contain valid UTF-8"]
    except ValueError as exc:
        return [f"{label} is not strict valid JSON: {exc}"]
    return _validate_blender_scene_spec(spec, project_id, storyboard)


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
    errors.extend(_voiceover_script_errors(content, label))
    return content


def _voiceover_script_errors(content, label):
    errors = []
    if len(content) > MAX_VOICEOVER_SCRIPT_BYTES:
        errors.append(f"{label} exceeds maximum size of {MAX_VOICEOVER_SCRIPT_BYTES} bytes")
        return errors
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        errors.append(f"{label} must contain valid UTF-8 text")
        return errors
    if not text.strip():
        errors.append(f"{label} must not be empty or whitespace-only")
    if "\x00" in text:
        errors.append(f"{label} must not contain NUL characters")
    return errors


def _validate_tts_contract(stage, label, project_resources, tool_ids, tool_by_id, errors):
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
    productive = stage.get("adapter_status") == "implemented"
    if productive:
        if provider != "kokoro":
            errors.append("productive TTS currently supports Kokoro only; Piper remains declarative")
        stage_inputs = stage.get("inputs") if isinstance(stage.get("inputs"), list) else []
        if "validated-plan" not in stage_inputs:
            errors.append(f"{label}.inputs must include validated-plan for productive TTS")
        if len(stage_inputs) != 2 or not all(input_id in stage_inputs for input_id in ("validated-plan", VOICEOVER_SCRIPT_INPUT)):
            errors.append(f"{label}.inputs must contain exactly validated-plan and {VOICEOVER_SCRIPT_INPUT} for productive TTS")
        tool = tool_by_id.get(tool_id)
        if tool is not None and (tool.get("availability") != "verified" or tool.get("adapter_status") != "implemented"):
            errors.append(f"productive TTS tool {tool_id} must be implemented and availability verified")
        if provider == "kokoro":
            if tts.get("voice") != KOKORO_VOICE or tts.get("language") != KOKORO_LANGUAGE:
                errors.append("productive Kokoro requires voice em_alex and language es")
            if tts.get("audio") != KOKORO_AUDIO:
                errors.append("productive Kokoro requires WAV mono 24000 Hz pcm_s16le")
    elif stage.get("adapter_status") != "pending":
        errors.append(f"{label}.adapter_status must be pending or an eligible productive implementation")

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


def _validate_pending_stage_tools(stage, label, tool_by_id, errors):
    tool_ids = stage.get("tools") if isinstance(stage, dict) else None
    if not isinstance(tool_ids, list) or any(not isinstance(tool_id, str) for tool_id in tool_ids):
        return
    for tool_id in tool_ids:
        tool = tool_by_id.get(tool_id)
        if isinstance(tool, dict) and tool.get("adapter_status") == "implemented":
            errors.append(f"{label} cannot keep its adapter pending while tool {tool_id} is implemented")


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
    tool_by_id = {tool["id"]: tool for tool in tools if isinstance(tool, dict) and isinstance(tool.get("id"), str)}
    stage_ids = []
    by_id = {}
    output_ids = {}
    output_paths = set()
    project_input_paths = set()
    workspace_paths = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    output_roots = [workspace_paths.get("intermediate_dir"), workspace_paths.get("artifact_dir")]
    top_inputs = {"storyboard", "pipeline-config"}
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
            _validate_tts_contract(stage, label, project_resources, tool_ids, tool_by_id, errors)
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

    if _intends_productive_scenes_config(data):
        validation_stage = by_id.get("01-validate-project", {})
        scene_stage = by_id.get("02-blender-scenes", {})
        if not isinstance(profile, dict) or (profile.get("width"), profile.get("height"), profile.get("fps")) != (540, 960, 12):
            errors.append("productive scene profile requires 540x960 at 12 FPS")
        storyboard_scenes = storyboard.get("scenes") if isinstance(storyboard, dict) else None
        if (
            not isinstance(storyboard, dict)
            or storyboard.get("aspect_ratio") != "9:16"
            or storyboard.get("fps") != 12
            or storyboard.get("total_frames") != 24
            or not isinstance(storyboard_scenes, list)
            or len(storyboard_scenes) != 1
            or not isinstance(storyboard_scenes[0], dict)
            or storyboard_scenes[0].get("start_frame") != 0
            or storyboard_scenes[0].get("end_frame") != 24
        ):
            errors.append("productive scene profile requires one storyboard scene covering [0,24) at 9:16 and 12 FPS")
        scene_specs = [
            (resource_id, resource) for resource_id, resource in project_resources.items()
            if resource.get("kind") == "blender-scene-spec"
        ]
        if scene_stage.get("adapter") != "blender-scenes":
            errors.append("productive scene profile Stage 02 adapter must be blender-scenes")
        scene_status = scene_stage.get("adapter_status")
        if scene_status == "implemented":
            blender = tool_by_id.get("blender", {})
            if blender.get("adapter_status") != "implemented" or blender.get("availability") != "verified":
                errors.append("productive scene profile requires a verified implemented blender tool")
        elif scene_status == "pending":
            _validate_pending_stage_tools(scene_stage, "productive scene profile 02-blender-scenes", tool_by_id, errors)
        elif scene_status != "pending":
            errors.append("productive scene profile Stage 02 adapter_status must be pending or implemented")
        if scene_stage.get("depends_on") != ["01-validate-project"]:
            errors.append("productive 02-blender-scenes must depend exclusively on 01-validate-project")
        if scene_stage.get("tools") != ["blender"] or "blender" not in tool_by_id:
            errors.append("productive 02-blender-scenes must declare the blender tool only")
        outputs = scene_stage.get("outputs", [])
        if (
            not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict)
            or outputs[0].get("id") != "scene-package"
            or not isinstance(outputs[0].get("path"), str)
            or PurePosixPath(outputs[0]["path"]).suffix.lower() != ".zip"
        ):
            errors.append("productive 02-blender-scenes must declare one scene-package ZIP output")
        if len(scene_specs) != 1 or scene_specs[0][1].get("required") is not True:
            errors.append("productive scene profile requires exactly one required blender-scene-spec resource")
        else:
            scene_spec_id = scene_specs[0][0]
            scene_spec_input = f"resource-{scene_spec_id}"
            expected_scene_inputs = {"storyboard", "validated-plan", scene_spec_input}
            scene_inputs = scene_stage.get("inputs")
            if (
                not isinstance(scene_inputs, list)
                or any(not isinstance(item, str) for item in scene_inputs)
                or set(scene_inputs) != expected_scene_inputs
                or len(scene_inputs) != len(expected_scene_inputs)
            ):
                errors.append("productive 02-blender-scenes.inputs must contain storyboard, validated-plan, and the declared scene spec only")
            scene_spec_path = project_resource_paths.get(scene_spec_id)
            scene_project_id = project.get("id") if isinstance(project, dict) else None
            if scene_spec_path is not None and isinstance(scene_project_id, str):
                errors.extend(_validate_blender_scene_spec_file(scene_spec_path, scene_project_id, storyboard))

        if validation_stage.get("adapter") != "builtin-validation" or validation_stage.get("adapter_status") != "implemented":
            errors.append("productive scene profile requires implemented builtin-validation on Stage 01")
        if validation_stage.get("depends_on") != []:
            errors.append("productive scene profile Stage 01 must have no dependencies")
        validation_outputs = validation_stage.get("outputs", [])
        validation_output_ids = [item.get("id") for item in validation_outputs if isinstance(item, dict)] if isinstance(validation_outputs, list) else []
        if validation_output_ids != ["validated-plan"]:
            errors.append("productive scene profile Stage 01 must declare exactly validated-plan output")
        required_validation_inputs = {"pipeline-config", "storyboard"} | {
            f"resource-{resource_id}" for resource_id, resource in project_resources.items()
            if resource.get("required") is True
        }
        validation_inputs = validation_stage.get("inputs")
        if (
            not isinstance(validation_inputs, list)
            or any(not isinstance(item, str) for item in validation_inputs)
            or len(validation_inputs) != len(required_validation_inputs)
            or set(validation_inputs) != required_validation_inputs
        ):
            errors.append("productive scene profile Stage 01 inputs must contain pipeline-config, storyboard, and all required resources only")

        combined_candidate = _has_productive_combined_contract(data)
        for stage_id in (TTS_NARRATION_STAGE_ID, LEGACY_NARRATION_STAGE_ID,
                         "04-remotion-compose", "05-ffmpeg-finalize", "06-quality-control"):
            stage = by_id.get(stage_id)
            if stage_id == TTS_NARRATION_STAGE_ID and combined_candidate:
                continue
            if stage is not None and stage_id != "01-validate-project" and stage.get("adapter_status") != "pending":
                errors.append(f"productive scene profile requires {stage_id}.adapter_status to remain pending")
            if stage is not None and stage.get("adapter_status") == "pending":
                _validate_pending_stage_tools(stage, f"productive scene profile {stage_id}", tool_by_id, errors)

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
        if tts_stage.get("adapter_status") == "implemented":
            if dependencies != ["01-validate-project"]:
                errors.append("productive 03-tts-narration must depend exclusively on 01-validate-project")
            validation_stage = by_id.get("01-validate-project", {})
            if validation_stage.get("adapter") != "builtin-validation" or validation_stage.get("adapter_status") != "implemented":
                errors.append("productive 01-validate-project must use implemented builtin-validation")
            validation_outputs = validation_stage.get("outputs", [])
            validation_output_ids = [output.get("id") for output in validation_outputs if isinstance(output, dict)] if isinstance(validation_outputs, list) else []
            if validation_output_ids != ["validated-plan"]:
                errors.append("productive 01-validate-project must declare exactly validated-plan output")
            if "pipeline-config" not in validation_stage.get("inputs", []):
                errors.append("productive 01-validate-project.inputs must include pipeline-config")
            validation_inputs = validation_stage.get("inputs", [])
            required_validation_inputs = {"pipeline-config", "storyboard"} | {
                f"resource-{resource_id}" for resource_id, resource in project_resources.items()
                if resource.get("required") is True
            }
            if (
                not isinstance(validation_inputs, list)
                or len(validation_inputs) != len(required_validation_inputs)
                or not all(input_id in validation_inputs for input_id in required_validation_inputs)
            ):
                errors.append("productive 01-validate-project.inputs must contain pipeline-config, storyboard, and all required resources only")
            kokoro_tool = tool_by_id.get("kokoro", {})
            if kokoro_tool.get("version_requirement") != "kokoro-onnx==0.6.1; Python>=3.10,<3.14; CPUExecutionProvider":
                errors.append("productive Kokoro tool version_requirement must declare the supported runtime contract")
            combined_candidate = _has_productive_combined_contract(data)
            for audiovisual_stage_id in (
                "02-blender-scenes", "04-remotion-compose", "05-ffmpeg-finalize", "06-quality-control",
            ):
                audiovisual_stage = by_id.get(audiovisual_stage_id, {})
                if audiovisual_stage_id == "02-blender-scenes" and combined_candidate:
                    continue
                if audiovisual_stage.get("adapter_status") != "pending":
                    errors.append(f"productive profile requires {audiovisual_stage_id}.adapter_status to remain pending")
                else:
                    _validate_pending_stage_tools(audiovisual_stage, f"productive profile {audiovisual_stage_id}", tool_by_id, errors)
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
                if input_id == "pipeline-config" and stage_id != "01-validate-project":
                    errors.append("pipeline-config may only be used by 01-validate-project")
                if input_id not in known_inputs:
                    errors.append(f"stage {stage_id} references unknown input or artifact: {input_id}")
                elif input_id in output_ids and output_ids[input_id] not in ancestors[stage_id]:
                    errors.append(f"stage {stage_id} must depend on producer of input {input_id}")

    if _has_productive_execution_intent(data) and _classify_productive_profile(data) is None:
        errors.append("productive profile declaration does not match an authorized complete profile")

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


def _parse_blender_version_output(stdout, stderr=""):
    parts = []
    for value in (stdout, stderr):
        if isinstance(value, bytes):
            parts.append(value.decode("utf-8", errors="replace"))
        elif isinstance(value, str):
            parts.append(value)
        elif value is not None:
            raise ValueError("Blender version probe returned an unsupported output type")
    encoded = "\n".join(parts).encode("utf-8")
    if len(encoded) > MAX_BLENDER_PREFLIGHT_OUTPUT_BYTES:
        raise ValueError(
            f"Blender version probe output exceeds {MAX_BLENDER_PREFLIGHT_OUTPUT_BYTES} bytes"
        )
    matches = list(BLENDER_VERSION_PATTERN.finditer("\n".join(parts)))
    if len(matches) != 1:
        raise ValueError("Blender version output must contain exactly one Blender x.y.z version")
    version = tuple(int(component) for component in matches[0].groups())
    version_line = next(line for line in "\n".join(parts).splitlines() if BLENDER_VERSION_PATTERN.match(line))
    suffix = version_line[BLENDER_VERSION_PATTERN.match(version_line).end():].strip()
    if suffix and suffix.casefold() != "lts":
        raise ValueError("Blender prerelease or development builds are unsupported")
    if version[0:2] != (5, 2):
        raise ValueError(f"Blender {version[0]}.{version[1]}.{version[2]} is unsupported; expected Blender 5.2.x")
    return version


def _run_blender_version_probe(command, cwd):
    """Capture at most the allowed version output while supervising the process."""
    options = {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
        "shell": False, "cwd": cwd,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    job = None
    reader = None
    captured = bytearray()
    errors = []
    oversized = threading.Event()
    succeeded = False

    def read_output():
        try:
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    return
                remaining = MAX_BLENDER_PREFLIGHT_OUTPUT_BYTES - len(captured)
                captured.extend(chunk[:max(remaining, 0)])
                if len(chunk) > remaining:
                    oversized.set()
                    return
        except (OSError, ValueError) as exc:
            errors.append(exc)

    try:
        if os.name == "nt":
            job = _attach_windows_job(process)
        deadline = time.monotonic() + BLENDER_PREFLIGHT_TIMEOUT_SECONDS
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        reader.join(max(0, deadline - time.monotonic()))
        if reader.is_alive():
            raise ValueError("Blender version probe timed out")
        if oversized.is_set():
            raise ValueError(f"Blender version probe output exceeds {MAX_BLENDER_PREFLIGHT_OUTPUT_BYTES} bytes")
        if errors:
            raise ValueError("Blender version probe output could not be read") from errors[0]
        try:
            returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Blender version probe timed out") from exc
        succeeded = True
        return subprocess.CompletedProcess(command, returncode, stdout=bytes(captured), stderr=b"")
    finally:
        if not succeeded:
            _terminate_blender_process_tree(process, job)
        if job is not None:
            job.close()
        if reader is not None:
            reader.join(timeout=BLENDER_TERMINATION_GRACE_SECONDS)
        process.stdout.close()


def _preflight_blender_runtime(blender_path):
    """Check one explicit local Blender executable and observe its actual 5.2.x version."""
    if not isinstance(blender_path, (str, os.PathLike)) or not str(blender_path).strip():
        raise ValueError("--blender path must be provided explicitly")
    executable = Path(blender_path)
    if not executable.is_absolute():
        raise ValueError("--blender must be an absolute path")
    executable, digest, size_bytes = _hash_local_runtime_file(
        executable, "Blender executable", 1024 * 1024 * 1024,
    )
    if os.name == "nt":
        if executable.suffix.lower() != ".exe":
            raise ValueError("Blender executable must be a regular .exe file on Windows")
    elif not os.access(executable, os.X_OK):
        raise ValueError("Blender executable does not have execution permission")

    command = [str(executable), "--version"]
    completed = _run_blender_version_probe(command, executable.parent)
    if completed.returncode != 0:
        raise ValueError(f"Blender version probe failed with exit code {completed.returncode}")
    version = _parse_blender_version_output(completed.stdout, completed.stderr)
    return {
        "path": str(executable),
        "version": version,
        "version_string": ".".join(str(component) for component in version),
        "sha256": digest,
        "size_bytes": size_bytes,
    }


def _blender_worker_path():
    worker = Path(__file__).with_name("blender-scenes-worker.py")
    worker, digest, size_bytes = _hash_local_runtime_file(worker, "Blender scenes worker", 4 * 1024 * 1024)
    return worker, digest, size_bytes


def _blender_worker_command(runtime, request_path):
    version = runtime.get("version") if isinstance(runtime, dict) else None
    if not isinstance(version, tuple) or version[:2] != (5, 2) or len(version) != 3:
        raise ValueError("Blender worker requires a successful 5.2.x runtime preflight")
    executable = runtime.get("path")
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        raise ValueError("Blender worker requires an absolute preflighted executable path")
    request_path = Path(request_path)
    if not request_path.is_absolute():
        raise ValueError("Blender worker request path must be absolute")
    worker, _, _ = _blender_worker_path()
    return [
        executable,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code", "1",
        "--threads", "2",
        "--python", str(worker),
        "--", "--request", str(request_path),
    ]


def _attach_windows_job(process):
    """Place Blender and descendants in a kill-on-close Windows Job Object."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, wintypes.INT, ctypes.c_void_p, wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    try:
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    except Exception:
        kernel32.CloseHandle(job)
        raise

    class Job:
        def terminate(self):
            if not kernel32.TerminateJobObject(job, 1):
                raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

        def close(self):
            if job:
                kernel32.CloseHandle(job)

    return Job()


def _process_group_exists(process_id):
    try:
        os.killpg(process_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_blender_process_tree(process, windows_job=None):
    if os.name == "nt":
        if windows_job is None:
            process.kill()
        else:
            windows_job.terminate()
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + BLENDER_TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline and _process_group_exists(process.pid):
        time.sleep(0.05)
    if _process_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _run_blender_process(command, cwd):
    """Run the fixed Blender command and terminate its whole process tree on stop."""
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
        "cwd": cwd,
        "env": environment,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    windows_job = None
    try:
        if os.name == "nt":
            try:
                windows_job = _attach_windows_job(process)
            except Exception:
                _terminate_blender_process_tree(process)
                raise
        try:
            returncode = process.wait(timeout=BLENDER_WORKER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _terminate_blender_process_tree(process, windows_job)
            raise ValueError(
                f"Blender worker exceeded {BLENDER_WORKER_TIMEOUT_SECONDS} second timeout; no retry was attempted"
            ) from exc
        except KeyboardInterrupt as exc:
            _terminate_blender_process_tree(process, windows_job)
            raise ValueError("Blender worker was cancelled; no retry was attempted") from exc
        if returncode != 0:
            raise ValueError(f"Blender worker failed with exit code {returncode}; no retry was attempted")
    finally:
        if windows_job is not None:
            windows_job.close()


def _read_bounded_regular_file(path, maximum_size, label):
    path = Path(path)
    before = _inspect_path(path)
    if before is None or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular non-link file")
    if before.st_size > maximum_size:
        raise ValueError(f"{label} exceeds maximum size of {maximum_size} bytes")
    identity = _file_identity(before)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if identity is not None and _file_identity(opened) != identity:
            raise ValueError(f"{label} changed while opening")
        payload = stream.read(maximum_size + 1)
    after = _inspect_path(path)
    if after is None or (identity is not None and _file_identity(after) != identity):
        raise ValueError(f"{label} changed while reading")
    if len(payload) > maximum_size:
        raise ValueError(f"{label} exceeds maximum size of {maximum_size} bytes")
    return payload


def _create_blender_staging(project_root):
    parent = _ensure_project_output_directory(project_root, (".pipeline", "work", "blender-scenes"))
    for _ in range(3):
        run_dir = parent / f"run-{uuid.uuid4().hex}"
        try:
            os.mkdir(run_dir, 0o700)
        except FileExistsError:
            continue
        entry = _inspect_path(run_dir)
        if entry is None or not stat.S_ISDIR(entry.st_mode):
            raise ValueError("could not establish an exclusive Blender staging directory")
        return run_dir
    raise ValueError("could not allocate an exclusive Blender staging directory")


def _validate_blender_worker_response(data, runtime, project_id, scene_id, staging_root):
    try:
        response = STORYBOARD_VALIDATOR.parse_json(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Blender worker response is not strict valid JSON") from exc
    fields = {
        "record_type", "worker_version", "blender_version", "blender_version_string",
        "project_id", "scene_id", "frames", "outputs", "minimum_camera_target_distance_m",
        "render_seconds", "worker_seconds",
    }
    if not isinstance(response, dict) or set(response) != fields:
        raise ValueError("Blender worker response has missing or unknown fields")
    if (
        response.get("record_type") != "blender-scenes-worker-result"
        or type(response.get("worker_version")) is not int or response["worker_version"] != 1
        or response.get("blender_version") != list(runtime["version"])
        or response.get("blender_version_string") != runtime["version_string"]
        or response.get("project_id") != project_id
        or response.get("scene_id") != scene_id
        or response.get("frames") != list(range(24))
    ):
        raise ValueError("Blender worker response does not match the preflighted request")
    expected_outputs = [f"scenes/{scene_id}/scene.blend"] + [
        f"scenes/{scene_id}/frames/frame-{frame:06d}.png" for frame in range(24)
    ]
    if response.get("outputs") != expected_outputs:
        raise ValueError("Blender worker response does not declare the exact expected staging outputs")
    for field in ("minimum_camera_target_distance_m", "render_seconds", "worker_seconds"):
        value = response.get(field)
        if (
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0
        ):
            raise ValueError(f"Blender worker response {field} must be a finite non-negative number")
    if response["minimum_camera_target_distance_m"] <= 0.01:
        raise ValueError("Blender worker response reports an invalid camera-target distance")
    root = Path(staging_root)
    for relative in expected_outputs:
        path = root
        parts = PurePosixPath(relative).parts
        for component in parts[:-1]:
            path /= component
            entry = _inspect_path(path)
            if entry is None or not stat.S_ISDIR(entry.st_mode):
                raise ValueError(f"Blender worker output parent is absent or unsafe: {relative}")
        path /= parts[-1]
        entry = _inspect_path(path)
        if entry is None or not stat.S_ISREG(entry.st_mode) or entry.st_size <= 0:
            raise ValueError(f"Blender worker expected output is absent or invalid: {relative}")
        maximum = 512 * 1024 * 1024 if relative.endswith(".blend") else 32 * 1024 * 1024
        if entry.st_size > maximum:
            raise ValueError(f"Blender worker staging output exceeds size limit: {relative}")
    return response


def _run_blender_scenes_worker(config_path, runtime):
    """Prepare exclusive staging and run the fixed worker without publishing or updating a manifest."""
    config, errors, _, _ = load_and_validate_config(config_path)
    if errors or not _is_productive_scenes_config(config):
        raise ValueError("Blender worker requires a valid productive Stage 01 + Stage 02 profile")
    version = runtime.get("version") if isinstance(runtime, dict) else None
    if not isinstance(version, tuple) or len(version) != 3 or version[:2] != (5, 2):
        raise ValueError("Blender worker requires a successful Blender 5.2.x preflight")
    runtime_path, runtime_hash, _ = _hash_local_runtime_file(
        runtime.get("path"), "Blender executable", 1024 * 1024 * 1024,
    )
    if runtime_hash != runtime.get("sha256"):
        raise ValueError("Blender executable changed after preflight")
    input_hashes, _ = _productive_snapshot(config, config_path)
    root = Path(config_path).resolve().parent
    resource = next(
        item for item in config["project"]["resources"] if item.get("kind") == "blender-scene-spec"
    )
    scene_spec_path = root.joinpath(*PurePosixPath(resource["path"]).parts)
    scene_spec_bytes = _read_bounded_regular_file(
        scene_spec_path, MAX_BLENDER_SCENE_SPEC_BYTES, "blender-scene-spec",
    )
    try:
        scene_spec = STORYBOARD_VALIDATOR.parse_json(scene_spec_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("blender-scene-spec changed to invalid JSON before worker execution") from exc
    storyboard_path = root.joinpath(*PurePosixPath(config["project"]["storyboard"]).parts)
    storyboard_bytes = _read_bounded_regular_file(
        storyboard_path, MAX_PRODUCTIVE_STORYBOARD_BYTES, "storyboard",
    )
    storyboard = STORYBOARD_VALIDATOR.parse_json(storyboard_bytes.decode("utf-8", errors="strict"))
    scene_spec_errors = _validate_blender_scene_spec(
        scene_spec, config["project"]["id"], storyboard,
    )
    if scene_spec_errors:
        raise ValueError(
            "blender-scene-spec no longer matches the validated project and storyboard: "
            + "; ".join(scene_spec_errors)
        )
    staging_root = _create_blender_staging(root)
    request = {
        "request_version": 1,
        "project_id": config["project"]["id"],
        "scene_spec": scene_spec,
        "expected_blender_version": list(runtime["version"]),
        "expected_blender_version_string": runtime["version_string"],
        "staging_root": str(staging_root.resolve()),
    }
    request_bytes = (json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(request_bytes) > MAX_BLENDER_WORKER_REQUEST_BYTES:
        raise ValueError("Blender worker request exceeds its maximum size")
    request_path = staging_root / "request.json"
    _write_exclusive_file(request_path, request_bytes, {})
    command = _blender_worker_command(runtime, request_path)
    worker_path, worker_hash, _ = _blender_worker_path()
    try:
        _run_blender_process(command, root)
        _assert_productive_config_unchanged(config, config_path)
        current_hashes, _ = _productive_snapshot(config, config_path)
        if current_hashes != input_hashes:
            raise ValueError("project inputs changed while Blender worker was running")
        if _hash_local_runtime_file(runtime_path, "Blender executable", 1024 * 1024 * 1024)[1] != runtime_hash:
            raise ValueError("Blender executable changed while worker was running")
        if _hash_local_runtime_file(worker_path, "Blender scenes worker", 4 * 1024 * 1024)[1] != worker_hash:
            raise ValueError("Blender scenes worker changed while it was running")
        if hashlib.sha256(_read_bounded_regular_file(
            request_path, MAX_BLENDER_WORKER_REQUEST_BYTES, "Blender worker request",
        )).digest() != hashlib.sha256(request_bytes).digest():
            raise ValueError("Blender worker request changed while the process was running")
        response_path = staging_root / "response.json"
        response_bytes = _read_bounded_regular_file(
            response_path, MAX_BLENDER_WORKER_RESPONSE_BYTES, "Blender worker response",
        )
        response = _validate_blender_worker_response(
            response_bytes, runtime, config["project"]["id"], scene_spec["scene_id"], staging_root,
        )
        return {"staging_root": str(staging_root), "response": response, "stage_02_status": "pending"}
    except (OSError, ValueError) as exc:
        if "staging_root" in str(exc):
            raise
        raise ValueError(f"Blender worker failed; staged files remain untrusted for manual inspection: {exc}") from exc


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


def _productive_stage(config, stage_id):
    return next(stage for stage in config["stages"] if stage["id"] == stage_id)


def _is_productive_tts_config(config):
    stage = next(
        (item for item in config.get("stages", []) if isinstance(item, dict) and item.get("id") == TTS_NARRATION_STAGE_ID),
        None,
    )
    return (
        isinstance(stage, dict)
        and stage.get("adapter_status") == "implemented"
        and isinstance(stage.get("tts"), dict)
        and stage["tts"].get("provider") == "kokoro"
    )


def _intends_productive_scenes_config(config):
    """Detect a Blender profile attempt even when one of its contract markers is wrong or missing."""
    project = config.get("project") if isinstance(config, dict) else None
    resources = project.get("resources", []) if isinstance(project, dict) else []
    stages = config.get("stages", []) if isinstance(config, dict) else []
    stage02 = next(
        (item for item in stages if isinstance(item, dict) and item.get("id") == "02-blender-scenes"),
        None,
    ) if isinstance(stages, list) else None
    if not isinstance(stage02, dict):
        return False
    profile = config.get("profile") if isinstance(config, dict) else None
    if stage02.get("adapter_status") == "implemented" and isinstance(profile, dict) and profile.get("fps") == 12:
        return True
    if stage02.get("adapter_status") == "implemented" and stage02.get("adapter") == "blender-scenes":
        outputs = stage02.get("outputs", [])
        if isinstance(outputs, list) and any(
            isinstance(output, dict) and isinstance(output.get("path"), str)
            and PurePosixPath(output["path"]).suffix.lower() == ".zip"
            for output in outputs
        ):
            return True
    inputs = stage02.get("inputs", [])
    has_scene_spec_input = isinstance(inputs, list) and "resource-scene-spec" in inputs
    if isinstance(resources, list) and any(
        isinstance(resource, dict) and (
            resource.get("kind") == "blender-scene-spec"
            or (resource.get("id") == "scene-spec" and has_scene_spec_input)
        )
        for resource in resources
    ):
        return True
    outputs = stage02.get("outputs", [])
    has_scene_package = isinstance(outputs, list) and any(
        isinstance(output, dict) and output.get("id") == "scene-package"
        and isinstance(output.get("path"), str)
        and PurePosixPath(output["path"]).suffix.lower() == ".zip"
        for output in outputs
    )
    return has_scene_spec_input or has_scene_package


def _intends_productive_tts_config(config):
    stages = config.get("stages") if isinstance(config, dict) else None
    if not isinstance(stages, list):
        return False
    stage = next((
        item for item in stages
        if isinstance(item, dict) and item.get("id") == TTS_NARRATION_STAGE_ID
    ), None)
    return isinstance(stage, dict) and (
        stage.get("adapter") == "tts-narration"
        or "tts" in stage
        or stage.get("adapter_status") == "implemented"
    )


def _has_productive_execution_intent(config):
    """Require strict profile authorization once a productive stage is activated."""
    stages = config.get("stages") if isinstance(config, dict) else None
    if not isinstance(stages, list):
        return False
    active_productive_stage = any(
        isinstance(stage, dict)
        and isinstance(stage.get("id"), str)
        and stage.get("id") in {"02-blender-scenes", TTS_NARRATION_STAGE_ID}
        and stage.get("adapter_status") == "implemented"
        for stage in stages
    )
    active_extra_stage = any(
        isinstance(stage, dict)
        and isinstance(stage.get("id"), str)
        and stage.get("id") not in (set(REQUIRED_STAGE_IDS) | set(NARRATION_STAGE_IDS))
        and stage.get("adapter_status") == "implemented"
        for stage in stages
    )
    productive_markers = (
        _intends_productive_scenes_config(config) or _intends_productive_tts_config(config)
    )
    return productive_markers and (active_productive_stage or active_extra_stage)


def _is_productive_scenes_config(config):
    """Return whether a scene-profile declaration matches the 5B.1 eligibility signature."""
    project = config.get("project") if isinstance(config, dict) else None
    resources = project.get("resources", []) if isinstance(project, dict) else []
    stages = config.get("stages", []) if isinstance(config, dict) else []
    if not isinstance(resources, list) or not isinstance(stages, list) or not _intends_productive_scenes_config(config):
        return False
    by_id = {stage.get("id"): stage for stage in stages if isinstance(stage, dict)}
    stage01, stage02 = by_id.get("01-validate-project"), by_id.get("02-blender-scenes")
    specs = [item for item in resources if isinstance(item, dict) and item.get("kind") == "blender-scene-spec"]
    if len(specs) != 1 or specs[0].get("required") is not True or not isinstance(stage01, dict) or not isinstance(stage02, dict):
        return False
    spec_input = f"resource-{specs[0].get('id')}"
    required_inputs = {"pipeline-config", "storyboard"} | {
        f"resource-{item.get('id')}" for item in resources if isinstance(item, dict) and item.get("required") is True
    }
    scene_inputs = stage02.get("inputs")
    validation_inputs = stage01.get("inputs")
    outputs = stage02.get("outputs")
    if (
        stage01.get("adapter") != "builtin-validation" or stage01.get("adapter_status") != "implemented"
        or stage01.get("depends_on") != [] or not isinstance(validation_inputs, list)
        or any(not isinstance(item, str) for item in validation_inputs)
        or set(validation_inputs) != required_inputs or len(validation_inputs) != len(required_inputs)
        or not isinstance(stage01.get("outputs"), list)
        or [item.get("id") for item in stage01["outputs"] if isinstance(item, dict)] != ["validated-plan"]
        or stage02.get("adapter") != "blender-scenes" or stage02.get("adapter_status") not in {"pending", "implemented"}
        or stage02.get("depends_on") != ["01-validate-project"]
        or not isinstance(scene_inputs, list)
        or any(not isinstance(item, str) for item in scene_inputs)
        or set(scene_inputs) != {"storyboard", "validated-plan", spec_input}
        or len(scene_inputs) != 3 or stage02.get("tools") != ["blender"]
        or not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict)
        or outputs[0].get("id") != "scene-package"
        or not isinstance(outputs[0].get("path"), str) or PurePosixPath(outputs[0]["path"]).suffix.lower() != ".zip"
    ):
        return False
    profile = config.get("profile")
    if not isinstance(profile, dict) or (profile.get("width"), profile.get("height"), profile.get("fps")) != (540, 960, 12):
        return False
    if stage02["adapter_status"] == "implemented":
        blender = next((tool for tool in config["tools"] if tool["id"] == "blender"), None)
        if blender is None or blender.get("adapter_status") != "implemented" or blender.get("availability") != "verified":
            return False
    return all(
        stage_id in {"01-validate-project", "02-blender-scenes"}
        or (stage_id == TTS_NARRATION_STAGE_ID and _has_productive_combined_contract(config))
        or stage.get("adapter_status") == "pending"
        for stage_id, stage in by_id.items()
    )


def _has_productive_combined_contract(config):
    """Recognize the complete 5C.2 declaration; this does not authorize Stage 04."""
    by_id = _profile_stage_map(config)
    if by_id is None:
        return False
    stage01 = by_id.get("01-validate-project", {})
    stage02 = by_id.get("02-blender-scenes", {})
    stage03 = by_id.get(TTS_NARRATION_STAGE_ID, {})
    stage04 = by_id.get("04-remotion-compose", {})
    if not (
        _is_productive_scenes_config_shallow(config)
        and _is_productive_tts_contract_shallow(config)
        and stage01.get("adapter_status") == "implemented"
        and stage02.get("adapter_status") == "implemented"
        and stage03.get("adapter_status") == "implemented"
        and stage04.get("adapter_status") == "pending"
    ):
        return False
    for stage_id in ("05-ffmpeg-finalize", "06-quality-control"):
        if by_id.get(stage_id, {}).get("adapter_status") != "pending":
            return False
    tools = _profile_tool_map(config)
    if tools is None:
        return False
    blender, kokoro = tools.get("blender", {}), tools.get("kokoro", {})
    return (
        blender.get("availability") == "verified" and blender.get("adapter_status") == "implemented"
        and kokoro.get("availability") == "verified" and kokoro.get("adapter_status") == "implemented"
        and _profile_inactive_stages_pending(by_id, {"01-validate-project", "02-blender-scenes", TTS_NARRATION_STAGE_ID})
        and _profile_inactive_tools_pending(by_id, tools, {"blender", "kokoro"})
    )


def _profile_stage_map(config):
    if not isinstance(config, dict) or not isinstance(config.get("stages"), list):
        return None
    result = {}
    for stage in config["stages"]:
        if not isinstance(stage, dict):
            return None
        stage_id = stage.get("id")
        if not isinstance(stage_id, str) or not stage_id or stage_id in result:
            return None
        result[stage_id] = stage
    if (
        not set(REQUIRED_STAGE_IDS).issubset(result)
        or len(NARRATION_STAGE_IDS.intersection(result)) != 1
    ):
        return None
    return result


def _profile_tool_map(config):
    if not isinstance(config, dict) or not isinstance(config.get("tools"), list):
        return None
    result = {}
    for tool in config["tools"]:
        if not isinstance(tool, dict):
            return None
        tool_id = tool.get("id")
        if not isinstance(tool_id, str) or not tool_id or tool_id in result:
            return None
        result[tool_id] = tool
    return result


def _profile_inactive_stages_pending(stages, active_ids):
    return all(
        isinstance(stage, dict)
        and (stage_id in active_ids or stage.get("adapter_status") == "pending")
        for stage_id, stage in stages.items()
    )


def _profile_inactive_tools_pending(stages, tools, active_tool_ids):
    for stage_id, stage in stages.items():
        if stage.get("adapter_status") != "pending":
            continue
        stage_tools = stage.get("tools")
        if not isinstance(stage_tools, list) or any(not isinstance(tool_id, str) for tool_id in stage_tools):
            return False
        if any(
            tool_id not in active_tool_ids
            and tools.get(tool_id, {}).get("adapter_status") != "pending"
            for tool_id in stage_tools
        ):
            return False
    return True


def _is_productive_scenes_config_shallow(config):
    """Scene contract without downstream-state classification."""
    by_id = _profile_stage_map(config)
    if by_id is None or not isinstance(config.get("project"), dict) or not isinstance(config.get("profile"), dict):
        return False
    stage01, stage02 = by_id.get("01-validate-project"), by_id.get("02-blender-scenes")
    resources = config["project"].get("resources")
    if not isinstance(stage01, dict) or not isinstance(stage02, dict) or not isinstance(resources, list):
        return False
    if any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        or not isinstance(item.get("required"), bool)
        for item in resources
    ):
        return False
    specs = [item for item in resources if item.get("kind") == "blender-scene-spec"]
    if len(specs) != 1 or specs[0].get("required") is not True:
        return False
    validation_inputs = stage01.get("inputs")
    scene_inputs = stage02.get("inputs")
    stage01_outputs = stage01.get("outputs")
    outputs = stage02.get("outputs")
    if (
        not _profile_string_list(validation_inputs)
        or not _profile_string_list(scene_inputs)
        or not isinstance(stage01_outputs, list)
        or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in stage01_outputs)
        or not isinstance(outputs, list)
    ):
        return False
    required_inputs = {"pipeline-config", "storyboard"} | {
        f"resource-{item['id']}" for item in resources if item["required"] is True
    }
    expected_scene_inputs = {"storyboard", "validated-plan", f"resource-{specs[0]['id']}"}
    if any(not isinstance(item, dict) for item in outputs):
        return False
    return (
        _intends_productive_scenes_config(config)
        and stage01.get("adapter") == "builtin-validation"
        and stage01.get("adapter_status") == "implemented"
        and stage01.get("depends_on") == []
        and set(validation_inputs) == required_inputs
        and len(validation_inputs) == len(set(validation_inputs))
        and [item["id"] for item in stage01_outputs] == ["validated-plan"]
        and stage02.get("adapter") == "blender-scenes"
        and stage02.get("depends_on") == ["01-validate-project"]
        and stage02.get("tools") == ["blender"]
        and stage02.get("adapter_status") == "implemented"
        and set(scene_inputs) == expected_scene_inputs
        and len(scene_inputs) == 3
        and len(outputs) == 1
        and outputs[0].get("id") == "scene-package"
        and isinstance(outputs[0].get("path"), str)
        and PurePosixPath(outputs[0]["path"]).suffix.lower() == ".zip"
        and (config["profile"].get("width"), config["profile"].get("height"), config["profile"].get("fps")) == (540, 960, 12)
    )


def _profile_string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value) and len(value) == len(set(value))


def _is_productive_tts_contract_shallow(config):
    by_id, tools = _profile_stage_map(config), _profile_tool_map(config)
    if by_id is None or tools is None or not isinstance(config.get("project"), dict):
        return False
    stage01, stage03 = by_id.get("01-validate-project"), by_id.get(TTS_NARRATION_STAGE_ID)
    resources = config["project"].get("resources")
    if not isinstance(stage01, dict) or not isinstance(stage03, dict) or not isinstance(resources, list):
        return False
    if any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        or not isinstance(item.get("required"), bool)
        for item in resources
    ):
        return False
    script_ids = [item["id"] for item in resources if item.get("kind") == "voiceover-script" and item["required"] is True]
    if len(script_ids) != 1:
        return False
    inputs, validation_inputs = stage03.get("inputs"), stage01.get("inputs")
    outputs, stage01_outputs = stage03.get("outputs"), stage01.get("outputs")
    if (
        not _profile_string_list(inputs) or not _profile_string_list(validation_inputs)
        or not isinstance(outputs, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("path"), str) for item in outputs)
        or not isinstance(stage01_outputs, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in stage01_outputs)
    ):
        return False
    required_validation_inputs = {"pipeline-config", "storyboard"} | {
        f"resource-{item['id']}" for item in resources if item["required"] is True
    }
    output_paths = {item["id"]: item["path"] for item in outputs}
    tts = stage03.get("tts")
    if not isinstance(tts, dict) or not isinstance(tts.get("audio"), dict):
        return False
    return (
        len(script_ids) == 1 and stage01.get("adapter") == "builtin-validation"
        and stage01.get("adapter_status") == "implemented"
        and stage01.get("depends_on") == []
        and _profile_string_list(validation_inputs)
        and set(validation_inputs) == required_validation_inputs
        and [item["id"] for item in stage01_outputs] == ["validated-plan"]
        and stage03.get("adapter") == "tts-narration"
        and stage03.get("adapter_status") == "implemented"
        and stage03.get("depends_on") == ["01-validate-project"]
        and set(inputs) == {f"resource-{script_ids[0]}", "validated-plan"}
        and len(inputs) == 2
        and stage03.get("tools") == ["kokoro"]
        and tts.get("provider") == "kokoro" and tts.get("voice") == KOKORO_VOICE
        and tts.get("language") == KOKORO_LANGUAGE and tts.get("script_input") == VOICEOVER_SCRIPT_INPUT
        and tts.get("audio") == KOKORO_AUDIO
        and set(output_paths) == {"narration", "narration-provenance"}
        and Path(output_paths["narration"]).suffix.lower() == ".wav"
        and Path(output_paths["narration-provenance"]).suffix.lower() == ".json"
        and len(outputs) == 2
        and tools.get("kokoro", {}).get("availability") == "verified"
        and tools.get("kokoro", {}).get("adapter_status") == "implemented"
        and tools.get("kokoro", {}).get("version_requirement") == "kokoro-onnx==0.6.1; Python>=3.10,<3.14; CPUExecutionProvider"
    )


def _classify_productive_profile(config):
    """Return one complete productive profile, or None for ambiguous declarations."""
    stages, tools = _profile_stage_map(config), _profile_tool_map(config)
    if stages is None or tools is None:
        return None
    combined = _has_productive_combined_contract(config)
    if combined:
        return "audiovisual-combined"
    if (
        _is_productive_tts_config(config)
        and _is_productive_tts_contract_shallow(config)
        and not _intends_productive_scenes_config(config)
        and stages.get("02-blender-scenes", {}).get("adapter_status") == "pending"
        and _profile_inactive_stages_pending(stages, {"01-validate-project", TTS_NARRATION_STAGE_ID})
        and _profile_inactive_tools_pending(stages, tools, {"kokoro"})
    ):
        return "kokoro-independent"
    narration_id = next(iter(NARRATION_STAGE_IDS.intersection(stages)), None)
    if (
        narration_id is not None
        and stages.get("01-validate-project", {}).get("adapter_status") == "implemented"
        and stages.get("02-blender-scenes", {}).get("adapter_status") == "implemented"
        and _is_productive_scenes_config(config)
        and not _intends_productive_tts_config(config)
        and stages.get(narration_id, {}).get("adapter_status") == "pending"
        and _profile_inactive_stages_pending(stages, {"01-validate-project", "02-blender-scenes"})
        and _profile_inactive_tools_pending(stages, tools, {"blender"})
        and tools.get("blender", {}).get("availability") == "verified"
        and tools.get("blender", {}).get("adapter_status") == "implemented"
    ):
        return "blender-independent"
    return None


def _verify_combined_kokoro_checkpoint(config, config_path, manifest, stage_entries, narration_entry,
                                       wav_path, provenance_path, wav_artifact):
    """Bind Stage 03's WAV and provenance to this run's verified inputs and runtime."""
    stage01 = stage_entries.get("01-validate-project")
    if not isinstance(stage01, dict) or stage01.get("status") != "completed":
        raise ValueError("Stage 01 must be completed before Kokoro provenance can be validated")
    plan_artifacts = [
        item for item in stage01.get("artifacts", [])
        if isinstance(item, dict) and item.get("id") == "validated-plan"
    ]
    if len(plan_artifacts) != 1 or not isinstance(plan_artifacts[0].get("sha256"), str):
        raise ValueError("Stage 01 must declare one validated-plan artifact")
    expected_plan_hash = plan_artifacts[0]["sha256"]
    expected_script_hash = _project_input_hashes(config, config_path).get(VOICEOVER_SCRIPT_INPUT)
    if not isinstance(expected_script_hash, str) or not FINGERPRINT.fullmatch(expected_script_hash):
        raise ValueError("current voiceover-script SHA-256 is unavailable")

    wav_bytes = wav_path.read_bytes()
    wav_hash = hashlib.sha256(wav_bytes).hexdigest()
    audio = _inspect_kokoro_wav(wav_bytes)
    provenance = _strict_load(provenance_path)
    if not isinstance(provenance, dict):
        raise ValueError("Kokoro provenance must be an object")
    hashes = provenance.get("hashes")
    runtime = provenance.get("runtime")
    assets = provenance.get("assets")
    timing = provenance.get("timing_seconds")
    if not isinstance(hashes, dict) or set(hashes) != {"script_sha256", "validated_plan_sha256", "wav_sha256"}:
        raise ValueError("Kokoro provenance hashes must contain the three v1 inputs")
    if hashes.get("script_sha256") != expected_script_hash:
        raise ValueError("Kokoro provenance script hash does not match the current voiceover-script")
    if hashes.get("validated_plan_sha256") != expected_plan_hash:
        raise ValueError("Kokoro provenance validated-plan hash does not match completed Stage 01")
    if hashes.get("wav_sha256") != wav_hash or wav_hash != wav_artifact.get("sha256"):
        raise ValueError("Kokoro provenance WAV hash does not match the file and manifest artifact")
    if not isinstance(runtime, dict) or set(runtime) != {
        "python_version", "worker_sha256", "dependencies", "onnx_execution_providers",
    }:
        raise ValueError("Kokoro provenance runtime fields are incomplete")
    dependencies = runtime.get("dependencies")
    expected_dependencies = {"kokoro-onnx", "onnxruntime", "numpy", "phonemizer", "espeakng-loader", "soundfile"}
    if (
        not isinstance(runtime.get("python_version"), str) or not runtime["python_version"].strip()
        or not isinstance(runtime.get("worker_sha256"), str) or not FINGERPRINT.fullmatch(runtime["worker_sha256"])
        or not isinstance(runtime.get("onnx_execution_providers"), list)
        or runtime["onnx_execution_providers"] != ["CPUExecutionProvider"]
        or not isinstance(dependencies, dict) or set(dependencies) != expected_dependencies
        or any(not isinstance(value, str) or not value.strip() for value in dependencies.values())
        or dependencies.get("kokoro-onnx") != KOKORO_RUNTIME_VERSION
    ):
        raise ValueError("Kokoro provenance runtime identity or CPU dependency contract is invalid")
    if not isinstance(assets, dict) or set(assets) != {"model", "voices"}:
        raise ValueError("Kokoro provenance assets must declare model and voices")
    for asset_id, asset in assets.items():
        if (
            not isinstance(asset, dict)
            or set(asset) != {"file_name", "size_bytes", "sha256", "version_identity"}
            or not isinstance(asset.get("file_name"), str) or not asset["file_name"].strip()
            or not isinstance(asset.get("size_bytes"), int) or isinstance(asset.get("size_bytes"), bool) or asset["size_bytes"] < 1
            or not isinstance(asset.get("sha256"), str) or not FINGERPRINT.fullmatch(asset["sha256"])
            or asset.get("version_identity") != f"sha256:{asset.get('sha256')}"
        ):
            raise ValueError(f"Kokoro provenance {asset_id} asset identity is invalid")
    if (
        not isinstance(provenance.get("provenance_version"), int)
        or isinstance(provenance.get("provenance_version"), bool)
        or provenance.get("provenance_version") != 1
        or provenance.get("record_type") != "productive-local-kokoro-tts"
        or provenance.get("run_id") != manifest.get("run_id")
        or provenance.get("project_id") != manifest.get("project_id")
        or provenance.get("dag_stage_id") != TTS_NARRATION_STAGE_ID
        or provenance.get("provider") != "kokoro-onnx"
        or provenance.get("voice") != KOKORO_VOICE
        or provenance.get("language") != KOKORO_LANGUAGE
        or provenance.get("requested_audio") != KOKORO_AUDIO
        or provenance.get("audio") != audio
        or not isinstance(provenance.get("attempts"), int)
        or isinstance(provenance.get("attempts"), bool)
        or provenance.get("attempts") != 1
        or provenance.get("automatic_retries") is not False
        or not isinstance(provenance.get("timeout_seconds"), int)
        or isinstance(provenance.get("timeout_seconds"), bool)
        or provenance.get("timeout_seconds") != KOKORO_TIMEOUT_SECONDS
    ):
        raise ValueError("Kokoro provenance execution identity does not match the v1 productive contract")
    if (
        not isinstance(timing, dict) or set(timing) != {"model_initialization", "inference"}
        or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0 for value in timing.values())
    ):
        raise ValueError("Kokoro provenance timing fields are invalid")
    if narration_entry.get("attempts") != provenance.get("attempts"):
        raise ValueError("Kokoro provenance attempts do not match the Stage 03 checkpoint")
    tool_version_text = narration_entry.get("tool_versions", {}).get("kokoro")
    if not isinstance(tool_version_text, str):
        raise ValueError("Stage 03 Kokoro tool identity is missing")
    tool_identity = STORYBOARD_VALIDATOR.parse_json(tool_version_text)
    if not isinstance(tool_identity, dict) or set(tool_identity) != {"runtime", "assets"}:
        raise ValueError("Stage 03 Kokoro tool identity is invalid")
    expected_tool_assets = {
        key: {"sha256": value["sha256"]} for key, value in assets.items()
    }
    if tool_identity.get("runtime") != runtime or tool_identity.get("assets") != expected_tool_assets:
        raise ValueError("Stage 03 tool version does not match Kokoro provenance runtime and assets")


def _productive_paths(root, stage):
    return {
        output["id"]: root.joinpath(*PurePosixPath(output["path"]).parts)
        for output in stage["outputs"]
    }


def _preflight_productive_path(path, label):
    path = Path(path)
    missing_parent = False
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        if missing_parent:
            continue
        entry = _inspect_path(current)
        if entry is None:
            missing_parent = True
        elif not stat.S_ISDIR(entry.st_mode):
            raise ValueError(f"{label} parent is not a regular directory: {current}")
    if not missing_parent:
        entry = _inspect_path(path)
        if entry is not None:
            raise ValueError(f"{label} already exists: {path}")


def _atomic_manifest_write(path, manifest):
    path = Path(path)
    payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _productive_snapshot(config, config_path):
    root = Path(config_path).resolve().parent
    hashes = _project_input_hashes(config, config_path)
    entries = []
    declared = {"storyboard": config["project"]["storyboard"]}
    declared.update({f"resource-{item['id']}": item["path"] for item in config["project"]["resources"]})
    required_ids = {
        f"resource-{item['id']}" for item in config["project"]["resources"] if item["required"] is True
    }
    stage = _productive_stage(config, "01-validate-project")
    selected = [item for item in stage["inputs"] if item != "pipeline-config"]
    for input_id in sorted(selected):
        relative = declared.get(input_id)
        if relative is None:
            raise ValueError(f"Stage 01 references unsupported project input: {input_id}")
        path = root.joinpath(*PurePosixPath(relative).parts)
        if input_id == "storyboard":
            size_bytes = path.stat().st_size
            if size_bytes > MAX_PRODUCTIVE_STORYBOARD_BYTES:
                raise ValueError(f"storyboard exceeds maximum size of {MAX_PRODUCTIVE_STORYBOARD_BYTES} bytes")
            data = path.read_bytes()
            try:
                storyboard = STORYBOARD_VALIDATOR.parse_json(data.decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise ValueError("storyboard changed to invalid JSON during execution preflight") from exc
            storyboard_errors = STORYBOARD_VALIDATOR.validate(storyboard)
            if storyboard_errors:
                raise ValueError("storyboard changed to an invalid document during execution preflight")
            digest = hashlib.sha256(data).hexdigest()
        elif input_id in required_ids:
            size_bytes = path.stat().st_size
            if size_bytes > MAX_VOICEOVER_SCRIPT_BYTES and input_id == VOICEOVER_SCRIPT_INPUT:
                raise ValueError(f"voiceover script exceeds maximum size of {MAX_VOICEOVER_SCRIPT_BYTES} bytes")
            if input_id == VOICEOVER_SCRIPT_INPUT:
                data = path.read_bytes()
                script_errors = _voiceover_script_errors(data, input_id)
                if script_errors:
                    raise ValueError("; ".join(script_errors))
                digest = hashlib.sha256(data).hexdigest()
            else:
                digest = sha256_file(path)
        else:
            size_bytes = path.stat().st_size
            digest = sha256_file(path)
        if hashes.get(input_id) != digest:
            raise ValueError(f"project input changed while preparing execution: {input_id}")
        entries.append({"id": input_id, "path": relative, "size_bytes": size_bytes, "sha256": digest})
    return hashes, entries


def _validated_plan(config, config_path, waves, input_entries, input_hashes):
    stage = _productive_stage(config, "01-validate-project")
    stage_hashes = {key: input_hashes[key] for key in stage["inputs"]}
    ordered_stages = []
    for wave in waves:
        for stage_id in wave:
            item = _productive_stage(config, stage_id)
            ordered_stages.append({
                "id": item["id"],
                "adapter": item["adapter"],
                "depends_on": item["depends_on"],
                "inputs": item["inputs"],
                "outputs": item["outputs"],
                "tools": item["tools"],
                "adapter_status": item["adapter_status"],
            })
    return {
        "validated_plan_version": 1,
        "pipeline_version": config["pipeline_version"],
        "project_id": config["project"]["id"],
        "config_fingerprint": fingerprint(config),
        "profile": config["profile"],
        "workspace": config["workspace"],
        "validation": {
            "pipeline": "PASS",
            "storyboard_validator": "validate-storyboard-v1",
            "storyboard": "PASS",
            "dag": "PASS",
            "required_resources": "PASS",
        },
        "inputs": input_entries,
        "execution_waves": waves,
        "stages": ordered_stages,
        "stage_fingerprint": stage_fingerprint(config, stage, stage_hashes, {}),
    }


def _verify_validated_plan(data, config, config_path, waves):
    try:
        parsed = STORYBOARD_VALIDATOR.parse_json(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("validated-plan JSON is invalid") from exc
    hashes, entries = _productive_snapshot(config, config_path)
    expected = _validated_plan(config, config_path, waves, entries, hashes)
    canonical = (json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if parsed != expected or data != canonical:
        raise ValueError("validated-plan does not match the current validated project snapshot")
    return parsed


def _assert_productive_config_unchanged(config, config_path):
    current, errors, _, _ = load_and_validate_config(config_path)
    if errors or current != config:
        raise ValueError("pipeline config changed during execution")


def _productive_worker(config_path, script_bytes, *, python_path, model_path, voices_path):
    python_file, _, _ = _hash_local_runtime_file(python_path, "Python interpreter", 512 * 1024 * 1024)
    if os.name == "nt" and python_file.suffix.lower() != ".exe":
        raise ValueError("Python interpreter must be an explicit .exe path on Windows")
    if os.name != "nt" and not os.access(python_file, os.X_OK):
        raise ValueError("Python interpreter is not executable")
    model_file, model_sha256, model_size = _hash_local_runtime_file(model_path, "Kokoro model", 1024 * 1024 * 1024)
    voices_file, voices_sha256, voices_size = _hash_local_runtime_file(voices_path, "Kokoro voices", 256 * 1024 * 1024)
    worker = Path(__file__).with_name("tts-kokoro-worker.py")
    worker_file, worker_sha256, worker_size = _hash_local_runtime_file(worker, "Kokoro worker", 16 * 1024 * 1024)
    command = [str(python_file), str(worker_file), "--model", str(model_file), "--voices", str(voices_file), "--voice", KOKORO_VOICE, "--language", KOKORO_LANGUAGE]
    worker_env = os.environ.copy()
    worker_env["ONNX_PROVIDER"] = "CPUExecutionProvider"
    worker_env["PYTHONDONTWRITEBYTECODE"] = "1"
    worker_env["PYTHONNOUSERSITE"] = "1"
    worker_env.pop("PYTHONPATH", None)
    worker_env.pop("PYTHONHOME", None)
    try:
        completed = subprocess.run(
            command, input=script_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=KOKORO_TIMEOUT_SECONDS, check=False, shell=False,
            cwd=Path(config_path).resolve().parent, env=worker_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Kokoro worker exceeded {KOKORO_TIMEOUT_SECONDS} second timeout; no retry was attempted") from exc
    if completed.returncode != 0:
        raise ValueError(f"Kokoro worker failed with exit code {completed.returncode}; no retry was attempted")
    wav_bytes = completed.stdout
    if not isinstance(wav_bytes, bytes) or len(wav_bytes) > MAX_KOKORO_WAV_BYTES:
        raise ValueError("Kokoro worker output exceeds the supported WAV size")
    marker = b"KOKORO_RESULT="
    line = next((item[len(marker):] for item in reversed(completed.stderr.splitlines()) if item.startswith(marker)), None)
    if line is None:
        raise ValueError("Kokoro worker did not return a result record")
    try:
        result = STORYBOARD_VALIDATOR.parse_json(line.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Kokoro worker returned invalid result JSON") from exc
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("Kokoro worker reported a synthesis error")
    audio = _inspect_kokoro_wav(wav_bytes)
    digest = hashlib.sha256(wav_bytes).hexdigest()
    worker_audio = result.get("audio")
    if result.get("provider") != "kokoro-onnx" or result.get("onnx_execution_providers") != ["CPUExecutionProvider"]:
        raise ValueError("Kokoro worker did not verify the expected CPU runtime")
    if result.get("voice") != KOKORO_VOICE or result.get("language") != KOKORO_LANGUAGE:
        raise ValueError("Kokoro worker returned unexpected voice or language")
    if not isinstance(worker_audio, dict) or any(
        worker_audio.get(key) != value for key, value in {
            "container": "wav", "sample_rate_hz": audio["sample_rate_hz"], "channels": audio["channels"],
            "sample_format": audio["sample_format"], "frames": audio["frames"],
            "duration_seconds": audio["duration_seconds"], "size_bytes": len(wav_bytes), "sha256": digest,
        }.items()
    ):
        raise ValueError("Kokoro worker audio report does not match returned WAV")
    versions = result.get("versions")
    expected_versions = {"kokoro-onnx", "onnxruntime", "numpy", "phonemizer", "espeakng-loader", "soundfile"}
    if not isinstance(versions, dict) or set(versions) != expected_versions or any(not isinstance(v, str) or not v for v in versions.values()):
        raise ValueError("Kokoro worker runtime versions are incomplete")
    if versions.get("kokoro-onnx") != KOKORO_RUNTIME_VERSION:
        raise ValueError(f"Kokoro runtime must be version {KOKORO_RUNTIME_VERSION}")
    python_version = result.get("python_version")
    if not isinstance(python_version, str) or not python_version:
        raise ValueError("Kokoro worker returned no Python version")
    timing = result.get("timing_seconds")
    if not isinstance(timing, dict) or any(not isinstance(timing.get(key), (int, float)) or isinstance(timing[key], bool) or not math.isfinite(timing[key]) or timing[key] < 0 for key in ("model_initialization", "inference")):
        raise ValueError("Kokoro worker returned invalid timing measurements")
    for path, expected in ((model_file, model_sha256), (voices_file, voices_sha256), (worker_file, worker_sha256)):
        if _hash_local_runtime_file(path, "Kokoro runtime asset", 1024 * 1024 * 1024)[1] != expected:
            raise ValueError("Kokoro runtime asset changed during inference")
    return {
        "wav_bytes": wav_bytes, "audio": audio, "worker_result": result, "versions": versions,
        "python_version": python_version, "worker_sha256": worker_sha256,
        "model": {"file_name": model_file.name, "size_bytes": model_size, "sha256": model_sha256, "version_identity": f"sha256:{model_sha256}"},
        "voices": {"file_name": voices_file.name, "size_bytes": voices_size, "sha256": voices_sha256, "version_identity": f"sha256:{voices_sha256}"},
        "worker_size": worker_size,
    }


def run_productive_tts(config_path, *, python_path, model_path, voices_path):
    """Run only the validated-plan and Kokoro productive stages with manifest v1 checkpoints."""
    config, errors, _, waves = load_and_validate_config(config_path)
    if errors:
        raise ValueError("pipeline validation failed: " + "; ".join(errors))
    stage01 = _productive_stage(config, "01-validate-project")
    stage03 = _productive_stage(config, TTS_NARRATION_STAGE_ID)
    if stage03.get("adapter_status") != "implemented" or stage03["tts"].get("provider") != "kokoro":
        raise ValueError("run-productive-tts requires the productive Kokoro contract")
    root = Path(config_path).resolve().parent
    captured = {}
    config_again, errors, _, waves = load_and_validate_config(config_path, captured)
    if errors or config_again != config:
        raise ValueError("pipeline config changed during execution preflight")
    script_bytes = captured.get(VOICEOVER_SCRIPT_ID)
    if not isinstance(script_bytes, bytes) or len(script_bytes) > MAX_KOKORO_SCRIPT_BYTES:
        raise ValueError(f"productive script must contain 1 to {MAX_KOKORO_SCRIPT_BYTES} UTF-8 bytes")
    stage01_paths = _productive_paths(root, stage01)
    stage03_paths = _productive_paths(root, stage03)
    all_output_paths = list(stage01_paths.values()) + list(stage03_paths.values())
    if len(set(map(str.casefold, map(str, all_output_paths)))) != len(all_output_paths):
        raise ValueError("productive outputs collide")
    for path in all_output_paths:
        _preflight_productive_path(path, "productive output")
    manifest_components = tuple(PurePosixPath(config["workspace"]["manifest_dir"]).parts)
    manifest_root = root.joinpath(*manifest_components)
    run_id = str(uuid.uuid4())
    run_dir = manifest_root / f"run-{run_id}"
    manifest_path = run_dir / "manifest.json"
    _preflight_productive_path(manifest_path, "manifest destination")
    # Probe all explicit local assets before creating a run record or outputs.
    _hash_local_runtime_file(python_path, "Python interpreter", 512 * 1024 * 1024)
    _hash_local_runtime_file(model_path, "Kokoro model", 1024 * 1024 * 1024)
    _hash_local_runtime_file(voices_path, "Kokoro voices", 256 * 1024 * 1024)
    input_hashes, input_entries = _productive_snapshot(config, config_path)
    if hashlib.sha256(script_bytes).hexdigest() != input_hashes.get(VOICEOVER_SCRIPT_INPUT):
        raise ValueError("voiceover script changed during execution preflight")
    plan = _validated_plan(config, config_path, waves, input_entries, input_hashes)
    plan_bytes = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(plan_bytes) > MAX_VALIDATED_PLAN_BYTES:
        raise ValueError(f"validated-plan exceeds maximum size of {MAX_VALIDATED_PLAN_BYTES} bytes")
    _ensure_project_output_directory(root, manifest_components)
    try:
        os.mkdir(run_dir, 0o700)
    except FileExistsError as exc:
        raise ValueError(f"manifest run directory already exists: {run_dir}") from exc
    run_entry = _inspect_path(run_dir)
    run_identity = _file_identity(run_entry) if run_entry is not None else None
    if run_entry is None or not stat.S_ISDIR(run_entry.st_mode) or run_identity is None:
        raise ValueError("cannot verify exclusive manifest run directory")
    manifest = new_pending_manifest(config, config_path)
    manifest["run_id"] = run_id
    plan_owned = {}
    tts_owned = {}
    tts_started = False
    try:
        _atomic_manifest_write(manifest_path, manifest)
        plan_path = stage01_paths["validated-plan"]
        _ensure_project_output_directory(root, tuple(PurePosixPath(stage01_paths["validated-plan"].relative_to(root).as_posix()).parts[:-1]))
        try:
            _write_exclusive_file(plan_path, plan_bytes, plan_owned)
            verified_plan = _read_owned_file(plan_path, MAX_VALIDATED_PLAN_BYTES, plan_owned.get(plan_path))
            _verify_validated_plan(verified_plan, config, config_path, waves)
            plan_hash = hashlib.sha256(verified_plan).hexdigest()
        except Exception:
            retained = []
            for owned_path, identity in plan_owned.items():
                if not _remove_owned_file(owned_path, identity) and os.path.lexists(owned_path):
                    retained.append(str(owned_path))
            if retained:
                raise ValueError("validated-plan publication failed; partial output retained at " + ", ".join(retained))
            raise
        stage01_hashes = {key: input_hashes[key] for key in stage01["inputs"]}
        manifest["stages"]["01-validate-project"].update(
            status="completed", attempts=1,
            artifacts=[{"id": "validated-plan", "path": next(item["path"] for item in stage01["outputs"] if item["id"] == "validated-plan"), "sha256": plan_hash}],
            stage_fingerprint=stage_fingerprint(config, stage01, stage01_hashes, {}),
        )
        _atomic_manifest_write(manifest_path, manifest)
        _assert_productive_config_unchanged(config, config_path)
        if _productive_snapshot(config, config_path)[0] != input_hashes:
            raise ValueError("project inputs changed during execution")
        tts_started = True
        worker = _productive_worker(config_path, script_bytes, python_path=python_path, model_path=model_path, voices_path=voices_path)
        if _productive_snapshot(config, config_path)[0] != input_hashes:
            raise ValueError("project inputs changed during execution")
        _assert_productive_config_unchanged(config, config_path)
        if hashlib.sha256(script_bytes).hexdigest() != input_hashes[VOICEOVER_SCRIPT_INPUT]:
            raise ValueError("voiceover script changed during execution")
        runtime = {
            "python_version": worker["python_version"], "worker_sha256": worker["worker_sha256"],
            "dependencies": worker["versions"], "onnx_execution_providers": worker["worker_result"]["onnx_execution_providers"],
        }
        provenance = {
            "provenance_version": 1, "record_type": "productive-local-kokoro-tts",
            "run_id": run_id, "project_id": config["project"]["id"],
            "dag_stage_id": TTS_NARRATION_STAGE_ID, "provider": "kokoro-onnx", "voice": KOKORO_VOICE, "language": KOKORO_LANGUAGE,
            "requested_audio": dict(KOKORO_AUDIO), "audio": worker["audio"], "runtime": runtime,
            "assets": {"model": worker["model"], "voices": worker["voices"]},
            "hashes": {"script_sha256": input_hashes[VOICEOVER_SCRIPT_INPUT], "wav_sha256": hashlib.sha256(worker["wav_bytes"]).hexdigest(), "validated_plan_sha256": plan_hash},
            "timing_seconds": worker["worker_result"]["timing_seconds"], "timeout_seconds": KOKORO_TIMEOUT_SECONDS,
            "attempts": 1, "automatic_retries": False,
        }
        provenance_bytes = (json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(provenance_bytes) > MAX_KOKORO_PROVENANCE_BYTES:
            raise ValueError("productive Kokoro provenance exceeds maximum size")
        try:
            for output_id, payload in (("narration", worker["wav_bytes"]), ("narration-provenance", provenance_bytes)):
                path = stage03_paths[output_id]
                _ensure_project_output_directory(root, tuple(PurePosixPath(path.relative_to(root).as_posix()).parts[:-1]))
                _write_exclusive_file(path, payload, tts_owned)
            wav_published = _read_owned_file(stage03_paths["narration"], MAX_KOKORO_WAV_BYTES, tts_owned.get(stage03_paths["narration"]))
            audio_published = _inspect_kokoro_wav(wav_published)
            prov_published = _read_owned_file(stage03_paths["narration-provenance"], MAX_KOKORO_PROVENANCE_BYTES, tts_owned.get(stage03_paths["narration-provenance"]))
            parsed_provenance = STORYBOARD_VALIDATOR.parse_json(prov_published.decode("utf-8"))
            wav_published_sha256 = hashlib.sha256(wav_published).hexdigest()
            if (
                audio_published != worker["audio"]
                or wav_published_sha256 != provenance["hashes"]["wav_sha256"]
                or parsed_provenance != provenance
            ):
                raise ValueError("productive TTS outputs failed post-publication verification")
            if _productive_snapshot(config, config_path)[0] != input_hashes:
                raise ValueError("project inputs changed during execution")
        except Exception:
            retained = []
            for owned_path, identity in tts_owned.items():
                if not _remove_owned_file(owned_path, identity) and os.path.lexists(owned_path):
                    retained.append(str(owned_path))
            if retained:
                raise ValueError("productive TTS publication failed; partial outputs retained at " + ", ".join(retained))
            raise
        tool_identity = {"runtime": runtime, "assets": {
            "model": {"sha256": worker["model"]["sha256"]},
            "voices": {"sha256": worker["voices"]["sha256"]},
        }}
        compact_tool_version = json.dumps(tool_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        stage03_hashes = {"validated-plan": plan_hash, VOICEOVER_SCRIPT_INPUT: input_hashes[VOICEOVER_SCRIPT_INPUT]}
        verified_output_hashes = {
            "narration": wav_published_sha256,
            "narration-provenance": hashlib.sha256(prov_published).hexdigest(),
        }
        tts_artifacts = [
            {
                "id": output_id,
                "path": next(item["path"] for item in stage03["outputs"] if item["id"] == output_id),
                "sha256": verified_output_hashes[output_id],
            }
            for output_id in stage03_paths
        ]
        manifest["stages"][TTS_NARRATION_STAGE_ID].update(
            status="completed", attempts=1, artifacts=tts_artifacts,
            stage_fingerprint=stage_fingerprint(config, stage03, stage03_hashes, {"kokoro": compact_tool_version}),
            tool_versions={"kokoro": compact_tool_version},
        )
        _atomic_manifest_write(manifest_path, manifest)
        final_errors, final_warnings = validate_manifest_file(config_path, manifest_path)
        if final_errors or final_warnings:
            raise ValueError("final manifest validation failed: " + "; ".join(final_errors + final_warnings))
        return {
            "manifest_path": str(manifest_path), "run_id": run_id, "stages": {key: value["status"] for key, value in manifest["stages"].items()},
            "audio": worker["audio"], "provenance": provenance, "tool_versions": {"kokoro": compact_tool_version},
        }
    except Exception as exc:
        tts_entry = manifest["stages"][TTS_NARRATION_STAGE_ID]
        validation_entry = manifest["stages"]["01-validate-project"]
        changed_inputs = "project inputs changed" in str(exc) or "script changed" in str(exc) or "pipeline config changed" in str(exc)
        if changed_inputs and validation_entry["status"] == "completed":
            for owned_path, identity in plan_owned.items():
                _remove_owned_file(owned_path, identity)
            validation_entry.update(status="failed", attempts=1, error="project inputs changed during execution", artifacts=[], stage_fingerprint=None, tool_versions={})
            tts_entry.update(status="failed" if tts_started else "blocked", attempts=1 if tts_started else 0, error=str(exc)[:500], artifacts=[], stage_fingerprint=None, tool_versions={})
        elif validation_entry["status"] == "pending":
            validation_entry.update(status="failed", attempts=1, error=str(exc)[:500])
            tts_entry.update(status="blocked", error="01-validate-project failed")
        elif tts_entry["status"] != "completed":
            tts_entry.update(status="failed", attempts=1, error=str(exc)[:500], artifacts=[])
        else:
            for owned_path, identity in tts_owned.items():
                _remove_owned_file(owned_path, identity)
            tts_entry.update(status="failed", attempts=1, error="final manifest verification failed", artifacts=[], stage_fingerprint=None, tool_versions={})
        try:
            _atomic_manifest_write(manifest_path, manifest)
        except OSError as persist_exc:
            raise ValueError(f"productive TTS failed and failure manifest could not be persisted: {persist_exc}") from exc
        raise


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
    if "pipeline-config" in next(
        (stage.get("inputs", []) for stage in config.get("stages", []) if isinstance(stage, dict) and stage.get("id") == "01-validate-project"),
        [],
    ):
        hashes["pipeline-config"] = fingerprint(config)
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
                        if artifact_id == "scene-package" and resolved.suffix.lower() == ".zip" and resolved.stat().st_size > MAX_ARCHIVE_BYTES:
                            errors.append(f"{artifact_label} scene-package exceeds ZIP size limit")
                            continue
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
    productive_tts = _is_productive_tts_config(config)
    productive_scenes = _is_productive_scenes_config(config)
    combined_profile = _classify_productive_profile(config) == "audiovisual-combined"
    if productive_tts or productive_scenes:
        pending_stage_ids = {"04-remotion-compose", "05-ffmpeg-finalize", "06-quality-control"}
        if not combined_profile and (productive_tts or configured["02-blender-scenes"]["adapter_status"] == "pending"):
            pending_stage_ids.add("02-blender-scenes")
        if productive_scenes and not combined_profile:
            pending_stage_ids.update(NARRATION_STAGE_IDS.intersection(configured))
        for stage_id in pending_stage_ids:
            entry = stage_entries.get(stage_id)
            if isinstance(entry, dict) and entry.get("status") != "pending":
                profile_name = "audiovisual combined" if combined_profile else "productive TTS" if productive_tts else "productive scenes"
                errors.append(f"manifest.stages.{stage_id} must remain pending in the {profile_name} profile")

        validation_entry = stage_entries.get("01-validate-project")
        if isinstance(validation_entry, dict) and validation_entry.get("status") == "completed":
            plan_artifact = next((
                artifact for artifact in validation_entry.get("artifacts", [])
                if isinstance(artifact, dict) and artifact.get("id") == "validated-plan"
            ), None)
            if plan_artifact is not None:
                plan_path = _path(root, plan_artifact.get("path"), "manifest validated-plan path", errors)
                if plan_path is not None:
                    try:
                        if plan_path.stat().st_size > MAX_VALIDATED_PLAN_BYTES:
                            raise ValueError("validated-plan exceeds its size limit")
                        plan_bytes = plan_path.read_bytes()
                        plan_errors = []
                        waves = _topological_waves(config.get("stages", []), plan_errors)
                        if plan_errors:
                            raise ValueError("current pipeline graph is invalid")
                        _verify_validated_plan(plan_bytes, config, config_path, waves)
                    except (OSError, ValueError) as exc:
                        errors.append(f"manifest validated-plan does not match current project semantics: {exc}")
        scene_entry = stage_entries.get("02-blender-scenes")
        if productive_scenes and isinstance(scene_entry, dict) and scene_entry.get("status") == "completed":
            try:
                artifact = next(item for item in scene_entry["artifacts"] if item["id"] == "scene-package")
                version_text = scene_entry["tool_versions"]["blender"]
                runtime_record = STORYBOARD_VALIDATOR.parse_json(version_text)
                if not isinstance(runtime_record, dict):
                    raise ValueError("Blender tool version must contain runtime identity JSON")
                archive_path = _path(root, artifact["path"], "manifest scene-package path", errors, required_file=True)
                if archive_path is None:
                    raise ValueError("scene-package path is invalid")
                context = _scene_package_context(config, config_path, manifest, artifact["sha256"], runtime_record)
                with archive_path.open("rb") as stream:
                    validate_scene_package(stream, expected_context=context)
            except (KeyError, StopIteration, TypeError, OSError, ValueError) as exc:
                errors.append(f"manifest scene-package does not match current project context: {exc}")
        narration_entry = stage_entries.get(TTS_NARRATION_STAGE_ID)
        if combined_profile and isinstance(narration_entry, dict) and narration_entry.get("status") == "completed":
            try:
                artifacts = {item["id"]: item for item in narration_entry["artifacts"]}
                wav_artifact = artifacts["narration"]
                provenance_artifact = artifacts["narration-provenance"]
                wav_path = _path(root, wav_artifact["path"], "manifest narration WAV path", errors, required_file=True)
                provenance_path = _path(root, provenance_artifact["path"], "manifest narration provenance path", errors, required_file=True)
                if wav_path is None or provenance_path is None:
                    raise ValueError("narration outputs are missing")
                if wav_path.stat().st_size > MAX_KOKORO_WAV_BYTES or provenance_path.stat().st_size > MAX_KOKORO_PROVENANCE_BYTES:
                    raise ValueError("narration output exceeds its size limit")
                _verify_combined_kokoro_checkpoint(
                    config, config_path, manifest, stage_entries, narration_entry,
                    wav_path, provenance_path, wav_artifact,
                )
            except (KeyError, TypeError, OSError, ValueError) as exc:
                errors.append(f"manifest narration does not match Kokoro provenance: {exc}")
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
    productive_tts = any(
        stage.get("id") == TTS_NARRATION_STAGE_ID
        and stage.get("adapter_status") == "implemented"
        and isinstance(stage.get("tts"), dict)
        and stage["tts"].get("provider") == "kokoro"
        for stage in config["stages"]
    )
    if productive_tts:
        print("Productive local TTS runs only via run-productive-tts; plan and dry-run remain read-only and do not probe runtime.")
    else:
        print("Production adapters are not executable; the TTS contract is declarative only.")


def _validated_scene_spec_for_package(config, config_path):
    root = Path(config_path).resolve().parent
    resource = next(item for item in config["project"]["resources"] if item.get("kind") == "blender-scene-spec")
    spec_path = root.joinpath(*PurePosixPath(resource["path"]).parts)
    spec_data = _read_bounded_regular_file(spec_path, MAX_BLENDER_SCENE_SPEC_BYTES, "blender-scene-spec")
    storyboard_path = root.joinpath(*PurePosixPath(config["project"]["storyboard"]).parts)
    storyboard_data = _read_bounded_regular_file(storyboard_path, MAX_PRODUCTIVE_STORYBOARD_BYTES, "storyboard")
    spec = STORYBOARD_VALIDATOR.parse_json(spec_data.decode("utf-8", errors="strict"))
    storyboard = STORYBOARD_VALIDATOR.parse_json(storyboard_data.decode("utf-8", errors="strict"))
    errors = _validate_blender_scene_spec(spec, config["project"]["id"], storyboard)
    if errors:
        raise ValueError("scene spec changed during execution: " + "; ".join(errors))
    return spec


def _unexpected_scene_warnings(config, config_path, warnings):
    """Allow only current warnings for declared optional resources that are absent."""
    root = Path(config_path).resolve().parent
    allowed = set()
    for resource in config.get("project", {}).get("resources", []):
        if not isinstance(resource, dict) or resource.get("required") is not False:
            continue
        relative = resource.get("path")
        if not isinstance(relative, str):
            continue
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            allowed.add(f"optional resource is not present: {relative}")
    return [warning for warning in warnings if warning not in allowed]


def _scene_package_context(config, config_path, manifest, archive_digest, runtime_record):
    stage01 = manifest["stages"]["01-validate-project"]
    stage02 = manifest["stages"]["02-blender-scenes"]
    plan_artifact = next(item for item in stage01["artifacts"] if item["id"] == "validated-plan")
    input_hashes = _project_input_hashes(config, config_path)
    input_hashes = {key: input_hashes[key] if key in input_hashes else plan_artifact["sha256"]
                    for key in _productive_stage(config, "02-blender-scenes")["inputs"]}
    spec = _validated_scene_spec_for_package(config, config_path)
    return ScenePackageContext(
        project_id=config["project"]["id"], scene_id=spec["scene_id"], run_id=manifest["run_id"],
        stage_01_fingerprint=stage01["stage_fingerprint"],
        stage_02_fingerprint=stage02["stage_fingerprint"],
        input_hashes=input_hashes, runtime=runtime_record,
        expected_render_seed=spec["render"]["seed"],
        manifest_scene_package_sha256=archive_digest,
    )


def _copy_exclusive_archive(source, destination):
    owned = {}
    source_entry = _inspect_path(source)
    if source_entry is None or not stat.S_ISREG(source_entry.st_mode) or source_entry.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("staged scene package is absent, unsafe, or oversized")
    source_identity = _file_identity(source_entry)
    if source_identity is None:
        raise ValueError("staged scene package identity cannot be verified")
    destination_parent = _inspect_path(Path(destination).parent)
    if destination_parent is None or not stat.S_ISDIR(destination_parent.st_mode):
        raise ValueError("scene package destination parent is unsafe")
    with Path(source).open("rb") as reader, Path(destination).open("xb") as writer:
        if _file_identity(os.fstat(reader.fileno())) != source_identity:
            raise ValueError("staged scene package identity changed")
        destination_identity = _file_identity(os.fstat(writer.fileno()))
        if destination_identity is None:
            raise ValueError("published scene package identity cannot be verified")
        owned[Path(destination)] = destination_identity
        copied = 0
        for chunk in iter(lambda: reader.read(64 * 1024), b""):
            copied += len(chunk)
            if copied > MAX_ARCHIVE_BYTES:
                raise ValueError("scene package exceeds ZIP size limit during publication")
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    source_after = _inspect_path(source)
    if source_after is None or _file_identity(source_after) != source_identity:
        raise ValueError("staged scene package identity changed during publication")
    return owned


def _write_scene_checkpoint(path, manifest, expected_identity):
    current = _inspect_path(path)
    if expected_identity is None:
        if current is not None:
            raise ValueError("manifest destination already exists")
    elif current is None or not stat.S_ISREG(current.st_mode) or _file_identity(current) != expected_identity:
        raise ValueError("manifest identity changed before checkpoint")
    _atomic_manifest_write(path, manifest)
    written = _inspect_path(path)
    identity = _file_identity(written) if written is not None and stat.S_ISREG(written.st_mode) else None
    if identity is None:
        raise ValueError("manifest checkpoint identity could not be verified")
    return identity


def run_productive_scenes(config_path, *, blender_path):
    """Execute Stage 01 and Stage 02 with verified exclusive ZIP publication."""
    config, errors, warnings, waves = load_and_validate_config(config_path)
    unexpected_warnings = _unexpected_scene_warnings(config, config_path, warnings) if config is not None else warnings
    if errors or unexpected_warnings:
        raise ValueError("invalid productive scenes profile: " + "; ".join(errors + unexpected_warnings))
    if not _is_productive_scenes_config(config) or _productive_stage(config, "02-blender-scenes")["adapter_status"] != "implemented":
        raise ValueError("run-productive-scenes requires the implemented productive Stage 01 + Stage 02 profile")
    root = Path(config_path).resolve().parent
    stage01 = _productive_stage(config, "01-validate-project")
    stage02 = _productive_stage(config, "02-blender-scenes")
    plan_path = _productive_paths(root, stage01)["validated-plan"]
    archive_path = _productive_paths(root, stage02)["scene-package"]
    if str(plan_path).casefold() == str(archive_path).casefold():
        raise ValueError("productive outputs collide")
    for path in (plan_path, archive_path):
        _preflight_productive_path(path, "productive output")
    manifest_components = tuple(PurePosixPath(config["workspace"]["manifest_dir"]).parts)
    run_id = str(uuid.uuid4())
    run_dir = root.joinpath(*manifest_components) / f"run-{run_id}"
    manifest_path = run_dir / "manifest.json"
    _preflight_productive_path(manifest_path, "manifest destination")
    runtime = _preflight_blender_runtime(blender_path)
    input_hashes, input_entries = _productive_snapshot(config, config_path)
    plan = _validated_plan(config, config_path, waves, input_entries, input_hashes)
    plan_bytes = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(plan_bytes) > MAX_VALIDATED_PLAN_BYTES:
        raise ValueError("validated-plan exceeds its size limit")
    _ensure_project_output_directory(root, manifest_components)
    try:
        os.mkdir(run_dir, 0o700)
    except FileExistsError as exc:
        raise ValueError(f"manifest run directory already exists: {run_dir}") from exc
    manifest = new_pending_manifest(config, config_path)
    manifest["run_id"] = run_id
    plan_owned = {}
    archive_owned = {}
    worker_started = False
    manifest_identity = None
    try:
        manifest_identity = _write_scene_checkpoint(manifest_path, manifest, manifest_identity)
        _ensure_project_output_directory(root, tuple(plan_path.relative_to(root).parts[:-1]))
        _write_exclusive_file(plan_path, plan_bytes, plan_owned)
        verified_plan = _read_owned_file(plan_path, MAX_VALIDATED_PLAN_BYTES, plan_owned[plan_path])
        _verify_validated_plan(verified_plan, config, config_path, waves)
        plan_hash = hashlib.sha256(verified_plan).hexdigest()
        stage01_hashes = {key: input_hashes[key] for key in stage01["inputs"]}
        manifest["stages"][stage01["id"]].update(
            status="completed", attempts=1,
            artifacts=[{"id": "validated-plan", "path": stage01["outputs"][0]["path"], "sha256": plan_hash}],
            stage_fingerprint=stage_fingerprint(config, stage01, stage01_hashes, {}),
        )
        manifest_identity = _write_scene_checkpoint(manifest_path, manifest, manifest_identity)
        _assert_productive_config_unchanged(config, config_path)
        if _productive_snapshot(config, config_path)[0] != input_hashes:
            raise ValueError("project inputs changed before Blender execution")
        worker_started = True
        worker = _run_blender_scenes_worker(config_path, runtime)
        _assert_productive_config_unchanged(config, config_path)
        if _productive_snapshot(config, config_path)[0] != input_hashes:
            raise ValueError("project inputs changed while Blender was running")
        spec = _validated_scene_spec_for_package(config, config_path)
        response = worker["response"]
        if response["scene_id"] != spec["scene_id"]:
            raise ValueError("Blender worker scene ID differs from current scene spec")
        worker_path, worker_hash, _ = _blender_worker_path()
        runtime_record = {
            "blender_version": list(response["blender_version"]),
            "blender_version_string": response["blender_version_string"],
            "blender_version_cycle": "release", "blender_executable_sha256": runtime["sha256"],
            "worker_version": response["worker_version"], "worker_sha256": worker_hash,
        }
        tool_version = json.dumps(runtime_record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stage02_hashes = {key: input_hashes[key] for key in stage02["inputs"] if key != "validated-plan"}
        stage02_hashes["validated-plan"] = plan_hash
        stage02_fingerprint = stage_fingerprint(config, stage02, stage02_hashes, {"blender": tool_version})
        observed = {key: response[key] for key in (
            "frames", "minimum_camera_target_distance_m", "render_seconds", "worker_seconds",
        )}
        staged_archive = Path(worker["staging_root"]) / "scene-package.zip"
        archive_digest = build_scene_package(
            worker["staging_root"], staged_archive, project_id=config["project"]["id"],
            scene_id=spec["scene_id"], run_id=run_id, runtime=runtime_record,
            input_hashes=stage02_hashes, stage_01_fingerprint=manifest["stages"][stage01["id"]]["stage_fingerprint"],
            stage_02_fingerprint=stage02_fingerprint, render_seed=spec["render"]["seed"], observed=observed,
        )
        candidate = {"status": "completed", "attempts": 1, "error": None,
                     "depends_on": stage02["depends_on"], "artifacts": [
                         {"id": "scene-package", "path": stage02["outputs"][0]["path"], "sha256": archive_digest}],
                     "stage_fingerprint": stage02_fingerprint, "tool_versions": {"blender": tool_version}}
        candidate_manifest = dict(manifest, stages={**manifest["stages"], stage02["id"]: candidate})
        context = _scene_package_context(config, config_path, candidate_manifest, archive_digest, runtime_record)
        with staged_archive.open("rb") as stream:
            validate_scene_package(stream, expected_context=context)
        _assert_productive_config_unchanged(config, config_path)
        if _productive_snapshot(config, config_path)[0] != input_hashes:
            raise ValueError("project inputs changed before scene package publication")
        if _hash_local_runtime_file(runtime["path"], "Blender executable", 1024 * 1024 * 1024)[1] != runtime["sha256"]:
            raise ValueError("Blender executable changed before scene package publication")
        if _hash_local_runtime_file(worker_path, "Blender scenes worker", 4 * 1024 * 1024)[1] != worker_hash:
            raise ValueError("Blender scenes worker changed before scene package publication")
        _ensure_project_output_directory(root, tuple(archive_path.relative_to(root).parts[:-1]))
        _preflight_productive_path(archive_path, "scene package output")
        archive_owned = _copy_exclusive_archive(staged_archive, archive_path)
        if _file_identity(_inspect_path(archive_path)) != archive_owned[archive_path]:
            raise ValueError("published scene package identity changed")
        with archive_path.open("rb") as stream:
            validate_scene_package(stream, expected_context=context)
        if _file_identity(_inspect_path(archive_path)) != archive_owned[archive_path]:
            raise ValueError("published scene package identity changed during validation")
        _assert_productive_config_unchanged(config, config_path)
        if _productive_snapshot(config, config_path)[0] != input_hashes:
            raise ValueError("project inputs changed before manifest checkpoint")
        manifest["stages"][stage02["id"]] = candidate
        manifest_identity = _write_scene_checkpoint(manifest_path, manifest, manifest_identity)
        final_errors, final_warnings = validate_manifest_file(config_path, manifest_path)
        unexpected_warnings = _unexpected_scene_warnings(config, config_path, final_warnings)
        if final_errors or unexpected_warnings:
            raise ValueError("final scene manifest validation failed: " + "; ".join(final_errors + unexpected_warnings))
        return {"manifest_path": str(manifest_path), "run_id": run_id,
                "stages": {key: value["status"] for key, value in manifest["stages"].items()}}
    except (Exception, KeyboardInterrupt) as exc:
        scene_entry = manifest["stages"][stage02["id"]]
        validation_entry = manifest["stages"][stage01["id"]]
        reason = str(exc).strip() or type(exc).__name__
        changed_inputs = "project inputs changed" in str(exc) or "pipeline config changed" in str(exc)
        if validation_entry["status"] == "pending" or changed_inputs:
            for owned_path, identity in plan_owned.items():
                _remove_owned_file(owned_path, identity)
            validation_entry.update(status="failed", attempts=1, error=reason[:500],
                                    artifacts=[], stage_fingerprint=None, tool_versions={})
            scene_entry.update(status="failed" if worker_started else "blocked",
                               attempts=1 if worker_started else 0, error=reason[:500],
                               artifacts=[], stage_fingerprint=None, tool_versions={})
        else:
            scene_entry.update(status="failed" if worker_started else "blocked",
                               attempts=1 if worker_started else 0, error=reason[:500],
                               artifacts=[], stage_fingerprint=None, tool_versions={})
        try:
            _write_scene_checkpoint(manifest_path, manifest, manifest_identity)
        except (OSError, ValueError) as persist_exc:
            raise ValueError(f"productive scenes failed and failure manifest could not be persisted: {persist_exc}") from exc
        raise


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
    productive_parser = commands.add_parser(
        "run-productive-tts", help="execute validated-plan and productive local Kokoro stages"
    )
    productive_parser.add_argument("config", help="pipeline JSON file; its directory is the project root")
    productive_parser.add_argument("--python", required=True, help="absolute path to the trusted Python interpreter")
    productive_parser.add_argument("--model", required=True, help="absolute path to the local Kokoro ONNX model")
    productive_parser.add_argument("--voices", required=True, help="absolute path to the local Kokoro voices file")
    scenes_parser = commands.add_parser(
        "run-productive-scenes", help="run productive Blender scene generation when the adapter is available"
    )
    scenes_parser.add_argument("config", help="pipeline JSON file; its directory is the project root")
    scenes_parser.add_argument("--blender", required=True, help="absolute path to the trusted Blender executable")
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

    if args.command == "run-productive-tts":
        try:
            result = run_productive_tts(
                args.config, python_path=args.python, model_path=args.model, voices_path=args.voices,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc), "simulation": False}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    if args.command == "run-productive-scenes":
        try:
            result = run_productive_scenes(args.config, blender_path=args.blender)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc), "simulation": False}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
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
