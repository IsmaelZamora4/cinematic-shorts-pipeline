import contextlib
import builtins
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
import urllib.request
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_pipeline():
    spec = importlib.util.spec_from_file_location("pipeline", ROOT / "scripts" / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pipeline = load_pipeline()


def make_tts_config(root, provider="piper"):
    config = make_config(root)
    legacy_stage = next(stage for stage in config["stages"] if stage["id"] == "03-kokoro-narration")
    legacy_stage.update(
        id="03-tts-narration",
        adapter="tts-narration",
        inputs=["resource-voiceover-script"],
        outputs=[
            {"id": "narration", "path": ".pipeline/work/narration.wav"},
            {"id": "narration-provenance", "path": ".pipeline/work/narration-provenance.json"},
        ],
        tools=[provider],
        tts={
            "provider": provider,
            "voice": "en_US-lessac-medium",
            "language": "en-US",
            "audio": {
                "container": "wav",
                "sample_rate_hz": 22050,
                "channels": 1,
                "sample_format": "pcm_s16le",
            },
            "script_input": "resource-voiceover-script",
        },
    )
    compose = next(stage for stage in config["stages"] if stage["id"] == "04-remotion-compose")
    compose["depends_on"] = ["02-blender-scenes", "03-tts-narration"]
    if provider not in {tool["id"] for tool in config["tools"]}:
        config["tools"].append(
            {
                "id": provider,
                "executable": provider,
                "version_requirement": "not verified",
                "availability": "not_checked",
                "adapter_status": "pending",
            }
        )
    config["project"]["resources"] = [
        {
            "id": "voiceover-script",
            "path": "voiceover-script.txt",
            "required": True,
            "kind": "voiceover-script",
        }
    ]
    (Path(root) / "voiceover-script.txt").write_text("A test voiceover script.\n", encoding="utf-8")
    return config


def make_config(root, storyboard="storyboard.json"):
    stages = [
        {"id": "01-validate-project", "adapter": "builtin-validation", "adapter_status": "implemented", "depends_on": [], "inputs": ["storyboard"], "outputs": [{"id": "validated-plan", "path": ".pipeline/work/validated-plan.json"}], "tools": []},
        {"id": "02-blender-scenes", "adapter": "blender-scenes", "adapter_status": "pending", "depends_on": ["01-validate-project"], "inputs": ["storyboard", "validated-plan"], "outputs": [{"id": "scene-package", "path": ".pipeline/work/scenes.json"}], "tools": ["blender"]},
        {"id": "03-kokoro-narration", "adapter": "kokoro-narration", "adapter_status": "pending", "depends_on": ["01-validate-project"], "inputs": ["storyboard", "validated-plan"], "outputs": [{"id": "narration", "path": ".pipeline/work/narration.wav"}], "tools": ["kokoro"]},
        {"id": "04-remotion-compose", "adapter": "remotion-compose", "adapter_status": "pending", "depends_on": ["02-blender-scenes", "03-kokoro-narration"], "inputs": ["scene-package", "narration"], "outputs": [{"id": "composite", "path": ".pipeline/work/composite.mp4"}], "tools": ["remotion"]},
        {"id": "05-ffmpeg-finalize", "adapter": "ffmpeg-finalize", "adapter_status": "pending", "depends_on": ["04-remotion-compose"], "inputs": ["composite"], "outputs": [{"id": "final-video", "path": ".pipeline/artifacts/final.mp4"}], "tools": ["ffmpeg"]},
        {"id": "06-quality-control", "adapter": "quality-control", "adapter_status": "pending", "depends_on": ["05-ffmpeg-finalize"], "inputs": ["final-video"], "outputs": [{"id": "quality-report", "path": ".pipeline/artifacts/quality-report.json"}], "tools": []},
    ]
    return {
        "pipeline_version": 1,
        "project": {"id": "test-project", "storyboard": storyboard, "resources": []},
        "profile": {
            "width": 540,
            "height": 960,
            "fps": 30,
            "format": "mp4",
            "limits": {"cpu_threads": 2, "memory_mb": 8192, "vram_mb": 3072, "max_parallel_stages": 1},
        },
        "workspace": {"intermediate_dir": ".pipeline/work", "artifact_dir": ".pipeline/artifacts", "manifest_dir": ".pipeline/runs"},
        "retry_policy": {"max_attempts": 2, "retryable_errors": ["transient", "timeout"], "resume_policy": "fingerprint"},
        "tools": [
            {"id": "blender", "executable": "blender", "version_requirement": ">=4.0,<6.0 (compatibility pending verification)", "availability": "not_checked", "adapter_status": "pending"},
            {"id": "kokoro", "executable": "kokoro", "version_requirement": "unverified", "availability": "not_checked", "adapter_status": "pending"},
            {"id": "remotion", "executable": "remotion", "version_requirement": "unverified", "availability": "not_checked", "adapter_status": "pending"},
            {"id": "ffmpeg", "executable": "ffmpeg", "version_requirement": "unverified", "availability": "not_checked", "adapter_status": "pending"},
        ],
        "stages": stages,
    }


class PipelineValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config = make_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def errors(self):
        config, errors, warnings, _ = pipeline.load_and_validate_config(self.config_path)
        return config, errors, warnings

    def test_accepts_versioned_pipeline_and_existing_storyboard_schema(self):
        config, errors, warnings = self.errors()
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertEqual(1, config["pipeline_version"])

    def test_accepts_generic_tts_contract_with_declared_utf8_script(self):
        self.config = make_tts_config(self.root)
        self.write_config()
        config, errors, warnings = self.errors()
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        stage = next(stage for stage in config["stages"] if stage["id"] == "03-tts-narration")
        self.assertEqual("piper", stage["tts"]["provider"])
        self.assertIn("01-validate-project", stage["depends_on"])
        self.assertEqual("not_checked", next(tool for tool in config["tools"] if tool["id"] == "piper")["availability"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, pipeline.main(["plan", str(self.config_path)]))
        self.assertIn("TTS contract (declarative; runtime not probed)", output.getvalue())

    def test_legacy_pipeline_does_not_apply_tts_utf8_rules_or_dependency(self):
        script = self.root / "legacy-resource.bin"
        script.write_bytes(b"\xfflegacy binary resource")
        self.config["project"]["resources"] = [
            {"id": "voiceover-script", "path": "legacy-resource.bin", "required": False}
        ]
        legacy_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-kokoro-narration")
        legacy_stage["depends_on"] = []
        legacy_stage["inputs"] = ["storyboard"]
        self.write_config()
        _, errors, warnings = self.errors()
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_rejects_both_narration_stage_ids(self):
        self.config = make_tts_config(self.root)
        legacy = make_config(self.root)["stages"][2]
        self.config["stages"].append(legacy)
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("exactly one narration stage" in error for error in errors), errors)

    def test_rejects_missing_narration_stage(self):
        self.config["stages"] = [stage for stage in self.config["stages"] if stage["id"] != "03-kokoro-narration"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("exactly one narration stage" in error for error in errors), errors)

    def test_rejects_generic_tts_stage_with_incorrect_composition_dependency(self):
        self.config = make_tts_config(self.root)
        compose = next(stage for stage in self.config["stages"] if stage["id"] == "04-remotion-compose")
        compose["depends_on"] = ["02-blender-scenes", "03-kokoro-narration"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("unknown dependency" in error for error in errors), errors)

    def test_generic_tts_requires_direct_validation_dependency(self):
        for dependencies in ([], ["02-blender-scenes"]):
            with self.subTest(dependencies=dependencies):
                self.config = make_tts_config(self.root)
                tts_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
                tts_stage["depends_on"] = dependencies
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any("must directly depend on 01-validate-project" in error for error in errors), errors)

    def test_generic_tts_does_not_require_direct_dependency_for_legacy_stage(self):
        legacy_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-kokoro-narration")
        legacy_stage["depends_on"] = []
        legacy_stage["inputs"] = ["storyboard"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertEqual([], errors)

    def test_generic_tts_rejects_tools_other_than_registered_provider(self):
        self.config = make_tts_config(self.root)
        tts_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts_stage["tools"] = ["piper", "ffmpeg"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("must contain exactly the registered provider tool piper" in error for error in errors), errors)

    def test_rejects_unregistered_tts_provider_and_executable_fields(self):
        self.config = make_tts_config(self.root)
        tts_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts_stage["tts"]["provider"] = "unknown-provider"
        for field in (
            "command", "shell_args", "module", "import_path", "script_path",
            "install_url", "download_url", "code",
        ):
            tts_stage["tts"][field] = "untrusted"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("provider" in error and "unsupported" in error for error in errors), errors)
        for field in (
            "command", "shell_args", "module", "import_path", "script_path",
            "install_url", "download_url", "code",
        ):
            self.assertTrue(any(f"unknown field: {field}" in error for error in errors), errors)

    def test_rejects_invalid_tts_audio_contract(self):
        self.config = make_tts_config(self.root)
        tts_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts_stage["tts"]["audio"]["sample_rate_hz"] = 999999
        tts_stage["tts"]["audio"]["unexpected"] = True
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("sample_rate_hz" in error for error in errors), errors)
        self.assertTrue(any("unknown field: unexpected" in error for error in errors), errors)

    def test_rejects_missing_empty_invalid_or_oversized_voiceover_script(self):
        self.config = make_tts_config(self.root)
        script = self.root / "voiceover-script.txt"
        cases = (
            (None, "required resource does not exist"),
            (b" \r\n\t", "must not be empty"),
            (b"invalid utf-8: \xff", "valid UTF-8"),
            (b"x" * (pipeline.MAX_VOICEOVER_SCRIPT_BYTES + 1), "exceeds maximum size"),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                if value is None:
                    script.unlink(missing_ok=True)
                else:
                    script.write_bytes(value)
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any(expected in error for error in errors), errors)
                script.write_text("A test voiceover script.\n", encoding="utf-8")

    def test_rejects_unsafe_voiceover_script_path(self):
        self.config = make_tts_config(self.root)
        self.config["project"]["resources"][0]["path"] = "../voiceover-script.txt"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("parent path segments" in error for error in errors), errors)

    def test_generic_tts_requires_the_script_resource_contract(self):
        for field, value, message in (
            ("required", False, ".required must be true"),
            ("kind", "text", ".kind must be 'voiceover-script'"),
        ):
            with self.subTest(field=field):
                self.config = make_tts_config(self.root)
                self.config["project"]["resources"][0][field] = value
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any(message in error for error in errors), errors)

    def test_generic_tts_requires_declared_wav_and_provenance_outputs(self):
        self.config = make_tts_config(self.root)
        tts_stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts_stage["outputs"][1]["path"] = ".pipeline/work/narration-provenance.txt"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("narration-provenance path must end in .json" in error for error in errors), errors)

    def test_voiceover_script_is_in_project_and_stage_fingerprints(self):
        self.config = make_tts_config(self.root)
        self.write_config()
        stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        initial_input_fingerprint = pipeline._input_fingerprint(self.config, self.config_path)
        input_hashes = pipeline._project_input_hashes(self.config, self.config_path)
        self.assertIn("resource-voiceover-script", input_hashes)
        initial_stage_fingerprint = pipeline.stage_fingerprint(
            self.config, stage, {"resource-voiceover-script": input_hashes["resource-voiceover-script"]}, {}
        )
        (self.root / "voiceover-script.txt").write_text("Changed script.\n", encoding="utf-8")
        changed_input_fingerprint = pipeline._input_fingerprint(self.config, self.config_path)
        changed_hashes = pipeline._project_input_hashes(self.config, self.config_path)
        changed_stage_fingerprint = pipeline.stage_fingerprint(
            self.config, stage, {"resource-voiceover-script": changed_hashes["resource-voiceover-script"]}, {}
        )
        self.assertNotEqual(initial_input_fingerprint, changed_input_fingerprint)
        self.assertNotEqual(initial_stage_fingerprint, changed_stage_fingerprint)

    def test_rejects_invalid_pipeline_version_and_unknown_fields(self):
        self.config["pipeline_version"] = True
        self.config["unexpected"] = "ignored?"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("pipeline_version" in error for error in errors))
        self.assertTrue(any("unexpected" in error for error in errors))

    def test_rejects_missing_dependencies_and_cycles(self):
        self.config["stages"][1]["depends_on"] = ["missing-stage"]
        self.config["stages"][0]["depends_on"] = ["06-quality-control"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("unknown dependency" in error for error in errors))
        self.assertTrue(any("cycle" in error for error in errors))

    def test_execution_waves_are_deterministic_and_expose_parallel_stages(self):
        _, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual(
            [
                ["01-validate-project"],
                ["02-blender-scenes", "03-kokoro-narration"],
                ["04-remotion-compose"],
                ["05-ffmpeg-finalize"],
                ["06-quality-control"],
            ],
            waves,
        )

    def test_rejects_escaping_absolute_and_windows_command_paths(self):
        self.config["project"]["storyboard"] = "../outside.json"
        self.config["workspace"]["intermediate_dir"] = "C:/outside"
        self.config["stages"][1]["outputs"][0]["path"] = "work\\..\\outside.mp4"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("project.storyboard" in error and "parent path segments" in error for error in errors))
        self.assertTrue(any("intermediate_dir" in error for error in errors))
        self.assertTrue(any("outputs" in error for error in errors))

    def test_rejects_posix_unc_and_malformed_relative_paths(self):
        for path in ("/etc/passwd", "//server/share/storyboard.json", "share//storyboard.json", "share/./storyboard.json"):
            with self.subTest(path=path):
                self.config["project"]["storyboard"] = path
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any("project.storyboard" in error for error in errors), errors)

    def test_rejects_output_collision_with_project_resource(self):
        asset_dir = self.root / "assets"
        asset_dir.mkdir()
        asset = asset_dir / "character.glb"
        asset.write_bytes(b"fixture")
        self.config["project"]["resources"] = [
            {"id": "character", "path": "assets/character.glb", "required": True}
        ]
        self.config["workspace"]["intermediate_dir"] = "assets"
        self.config["stages"][0]["outputs"][0]["path"] = "assets/character.glb"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("overwrite a project input" in error for error in errors), errors)

    def test_rejects_duplicate_tool_and_stage_ids(self):
        self.config["tools"][1]["id"] = self.config["tools"][0]["id"]
        self.config["stages"][1]["id"] = self.config["stages"][0]["id"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("tools[1].id duplicates" in error for error in errors), errors)
        self.assertTrue(any("duplicate stage id" in error for error in errors), errors)

    def test_rejects_wrong_json_types_for_profile_and_workspace_fields(self):
        invalid_values = (None, [], False, 1.5, {}, 0)
        for value in invalid_values:
            with self.subTest(value=value):
                self.config = make_config(self.root)
                self.config["profile"]["width"] = value
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any("profile.width" in error for error in errors), errors)
        for value in (None, [], True, 42, {}):
            with self.subTest(workspace_value=value):
                self.config = make_config(self.root)
                self.config["workspace"]["manifest_dir"] = value
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any("workspace.manifest_dir" in error for error in errors), errors)

    def test_extreme_declared_aspect_ratio_returns_validation_error_not_traceback(self):
        storyboard = json.loads((self.root / "storyboard.json").read_text(encoding="utf-8"))
        storyboard["aspect_ratio"] = "9" * 5000 + ":16"
        (self.root / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["validate", str(self.config_path)])
        self.assertEqual(1, result)
        self.assertIn("aspect_ratio", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_pipeline_cli_reports_excessive_json_nesting_without_traceback(self):
        deep_config = self.root / "deep-pipeline.json"
        deep_config.write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["validate", str(deep_config)])
        self.assertEqual(1, result)
        self.assertIn("nesting", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_rejects_nul_in_storyboard_and_other_path_fields_without_traceback(self):
        for field in ("storyboard", "resource", "workspace", "output"):
            with self.subTest(field=field):
                self.config = make_config(self.root)
                if field == "storyboard":
                    self.config["project"]["storyboard"] = "\x00"
                elif field == "resource":
                    self.config["project"]["resources"] = [{"id": "asset", "path": "\x00", "required": False}]
                elif field == "workspace":
                    self.config["workspace"]["intermediate_dir"] = "\x00"
                else:
                    self.config["stages"][0]["outputs"][0]["path"] = ".pipeline/work/\x00"
                self.write_config()
                _, errors, _ = self.errors()
                self.assertTrue(any("must not contain a NUL character" in error for error in errors), errors)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = pipeline.main(["validate", str(self.config_path)])
                self.assertEqual(1, result)
                self.assertIn("must not contain a NUL character", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())

    def test_requires_pipeline_and_storyboard_fps_to_match(self):
        storyboard = json.loads((self.root / "storyboard.json").read_text(encoding="utf-8"))
        storyboard["fps"] = self.config["profile"]["fps"]
        (self.root / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
        _, errors, _ = self.errors()
        self.assertFalse(any("fps" in error.lower() for error in errors), errors)
        self.config["profile"]["fps"] = storyboard["fps"] + 1
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("profile.fps must match storyboard.fps" in error for error in errors), errors)

    def test_requires_profile_dimensions_to_match_declared_storyboard_aspect_ratio(self):
        _, errors, _ = self.errors()
        self.assertFalse(any("aspect_ratio" in error for error in errors), errors)
        self.config["profile"]["width"] = 960
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("profile dimensions must match storyboard.aspect_ratio" in error for error in errors), errors)

    def test_allows_storyboards_without_declared_aspect_ratio(self):
        storyboard = json.loads((self.root / "storyboard.json").read_text(encoding="utf-8"))
        del storyboard["aspect_ratio"]
        (self.root / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
        self.config["profile"]["width"] = 960
        self.write_config()
        _, errors, _ = self.errors()
        self.assertFalse(any("aspect_ratio" in error for error in errors), errors)

    def test_rejects_external_tool_arguments_and_invalid_resource_budgets(self):
        self.config["tools"][0]["executable"] = "blender --background"
        self.config["profile"]["limits"]["max_parallel_stages"] = 0
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("executable" in error for error in errors))
        self.assertTrue(any("max_parallel_stages" in error for error in errors))

    def test_rejects_output_path_outside_declared_workspace(self):
        self.config["stages"][1]["outputs"][0]["path"] = "public/generated.mp4"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("must be under workspace" in error for error in errors))

    def test_rejects_duplicate_output_paths(self):
        self.config["stages"][2]["outputs"][0]["path"] = self.config["stages"][1]["outputs"][0]["path"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("duplicate output path" in error for error in errors))

    def test_rejects_pipeline_output_that_would_overwrite_storyboard(self):
        input_dir = self.root / "input"
        input_dir.mkdir()
        shutil.copyfile(self.root / "storyboard.json", input_dir / "storyboard.json")
        self.config["project"]["storyboard"] = "input/storyboard.json"
        self.config["workspace"]["intermediate_dir"] = "input"
        self.config["stages"][0]["outputs"][0]["path"] = "input/storyboard.json"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("overwrite a project input" in error for error in errors))

    def test_rejects_final_output_extension_different_from_profile(self):
        self.config["profile"]["format"] = "webm"
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("extension must match profile.format" in error for error in errors))

    def test_rejects_missing_required_resource(self):
        self.config["project"]["resources"] = [{"id": "character", "path": "assets/character.glb", "required": True}]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("required resource" in error for error in errors))

    def test_rejects_artifact_input_without_dependency_on_its_producer(self):
        self.config["stages"][3]["depends_on"] = ["02-blender-scenes"]
        self.write_config()
        _, errors, _ = self.errors()
        self.assertTrue(any("must depend on producer of input narration" in error for error in errors))

    def test_rejects_symbolic_link_escape_when_platform_allows_links(self):
        outside = self.root.parent / f"{self.root.name}-outside.json"
        outside.write_text("{}", encoding="utf-8")
        link = self.root / "linked-storyboard.json"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            outside.unlink(missing_ok=True)
            self.skipTest("symbolic links are unavailable in this environment")
        try:
            self.config["project"]["storyboard"] = "linked-storyboard.json"
            self.write_config()
            _, errors, _ = self.errors()
            self.assertTrue(any("symbolic link" in error for error in errors))
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_output_symlink_alias_of_pipeline_config_when_available(self):
        alias = self.root / "project-root"
        try:
            os.symlink(self.root, alias, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable in this environment")
        try:
            self.config["workspace"]["intermediate_dir"] = "project-root"
            self.config["stages"][0]["outputs"][0]["path"] = "project-root/pipeline.json"
            self.write_config()
            _, errors, _ = self.errors()
            self.assertTrue(any("overwrite a project input or pipeline configuration" in error for error in errors), errors)
        finally:
            alias.unlink(missing_ok=True)

    def test_validates_existing_v03_and_v04_storyboards_through_shared_validator(self):
        for relative in (
            "examples/cave-exploration/storyboard.json",
            "examples/forest-storm/storyboard.json",
            "examples/abandoned-research-station/storyboard.json",
        ):
            with self.subTest(relative=relative):
                shutil.copyfile(ROOT / relative, self.root / "storyboard.json")
                self.config["project"]["storyboard"] = "storyboard.json"
                self.write_config()
                _, errors, _ = self.errors()
                self.assertFalse(any("storyboard" in error for error in errors), errors)

    def test_dry_run_is_read_only_and_never_invokes_external_commands(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        output = io.StringIO()
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("external command invoked")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("external command invoked")), \
             mock.patch.object(os, "system", side_effect=AssertionError("shell command invoked")), \
             contextlib.redirect_stdout(output):
            result = pipeline.main(["dry-run", str(self.config_path)])
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(0, result)
        self.assertEqual(before, after)
        self.assertIn("no stages executed", output.getvalue().lower())
        self.assertIn("not_checked", output.getvalue())
        self.assertIn("adapter=pending", output.getvalue())
        self.assertIn("blocked (adapter pending;", output.getvalue())
        self.assertNotIn("completed", output.getvalue().lower())

    def test_every_cli_mode_is_read_only_and_does_not_launch_commands_or_network(self):
        manifest_path = self.root / "run-manifest.json"
        manifest_path.write_text(
            json.dumps(pipeline.new_pending_manifest(self.config, self.config_path)), encoding="utf-8"
        )
        generic_root = self.root / "generic"
        generic_root.mkdir()
        shutil.copyfile(ROOT / "templates" / "storyboard.json", generic_root / "storyboard.json")
        generic_config = make_tts_config(generic_root)
        generic_config_path = generic_root / "pipeline.json"
        generic_config_path.write_text(json.dumps(generic_config, indent=2), encoding="utf-8")
        generic_manifest_path = generic_root / "run-manifest.json"
        generic_manifest_path.write_text(
            json.dumps(pipeline.new_pending_manifest(generic_config, generic_config_path)), encoding="utf-8"
        )
        def snapshot():
            return {
                str(path.relative_to(self.root)): (
                    "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file",
                    os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None,
                )
                for path in self.root.rglob("*")
            }
        before = snapshot()
        commands = (
            ["validate", str(self.config_path)],
            ["plan", str(self.config_path)],
            ["dry-run", str(self.config_path)],
            ["validate-manifest", str(self.config_path), str(manifest_path)],
            ["validate", str(generic_config_path)],
            ["plan", str(generic_config_path)],
            ["dry-run", str(generic_config_path)],
            ["validate-manifest", str(generic_config_path), str(generic_manifest_path)],
        )
        original_builtin_open = builtins.open
        original_path_open = Path.open
        def read_only_open(open_function):
            def guarded(path, mode="r", *args, **kwargs):
                if any(flag in mode for flag in ("w", "a", "x", "+")):
                    raise AssertionError("write-mode file open attempted")
                return open_function(path, mode, *args, **kwargs)
            return guarded
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("external command invoked")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("external command invoked")), \
             mock.patch.object(os, "system", side_effect=AssertionError("shell command invoked")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network socket opened")), \
             mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network request made")), \
             mock.patch.object(builtins, "open", new=read_only_open(original_builtin_open)), \
             mock.patch.object(Path, "open", new=read_only_open(original_path_open)), \
             mock.patch.object(os, "unlink", side_effect=AssertionError("file deletion attempted")), \
             mock.patch.object(os, "remove", side_effect=AssertionError("file removal attempted")), \
             mock.patch.object(os, "replace", side_effect=AssertionError("file replacement attempted")), \
             mock.patch.object(os, "rename", side_effect=AssertionError("file rename attempted")), \
             mock.patch.object(os, "mkdir", side_effect=AssertionError("directory creation attempted")), \
             mock.patch.object(os, "rmdir", side_effect=AssertionError("directory removal attempted")):
            for command in commands:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, pipeline.main(command), command)
        self.assertEqual(before, snapshot())

    def test_configured_stage_order_does_not_change_topological_waves(self):
        _, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.config["stages"] = list(reversed(self.config["stages"]))
        self.write_config()
        _, errors, _, reversed_waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual(waves, reversed_waves)

    def test_dry_run_blocks_stages_on_unverified_tools_and_propagates(self):
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.config["tools"][0]["adapter_status"] = "implemented"
        self.config["tools"][0]["availability"] = "not_checked"
        self.config["stages"][2]["adapter_status"] = "implemented"
        self.config["tools"][1]["adapter_status"] = "implemented"
        self.config["tools"][1]["availability"] = "verified"
        self.config["stages"][3]["adapter_status"] = "implemented"
        self.config["tools"][2]["adapter_status"] = "implemented"
        self.config["tools"][2]["availability"] = "verified"
        self.write_config()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["dry-run", str(self.config_path)])
        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertIn("02-blender-scenes: blocked (tool blender availability not verified)", rendered)
        self.assertIn("04-remotion-compose: blocked (dependencies blocked: 02-blender-scenes)", rendered)
        self.assertIn("planned (not executed)", rendered)
        self.assertNotIn("completed", rendered.lower())

    def test_dry_run_blocks_tool_with_pending_adapter_even_if_available(self):
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.config["tools"][0]["availability"] = "verified"
        self.write_config()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["dry-run", str(self.config_path)])
        self.assertEqual(0, result)
        self.assertIn("blocked (tool blender adapter pending)", output.getvalue())


class RunManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config_path = self.root / "pipeline.json"
        self.config = make_config(self.root)
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        self.manifest_path = self.root / "run-manifest.json"
        self.manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        self.write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")

    def completed_blender_manifest(self):
        validation_output = self.root / ".pipeline" / "work" / "validated-plan.json"
        blender_output = self.root / ".pipeline" / "work" / "scenes.json"
        validation_output.parent.mkdir(parents=True, exist_ok=True)
        validation_output.write_text("validated", encoding="utf-8")
        blender_output.write_text("scene", encoding="utf-8")
        self.manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        validation_stage, blender_stage = self.config["stages"][:2]
        storyboard_hash = pipeline.sha256_file(self.root / "storyboard.json")
        validation_hash = pipeline.sha256_file(validation_output)
        self.manifest["stages"][validation_stage["id"]].update(
            status="completed", attempts=1,
            stage_fingerprint=pipeline.stage_fingerprint(
                self.config, validation_stage, {"storyboard": storyboard_hash}, {}
            ),
            artifacts=[{"id": "validated-plan", "path": ".pipeline/work/validated-plan.json", "sha256": validation_hash}],
        )
        tool_versions = {"blender": "5.2.3"}
        blender_entry = self.manifest["stages"][blender_stage["id"]]
        blender_entry.update(
            status="completed", attempts=1, tool_versions=tool_versions,
            stage_fingerprint=pipeline.stage_fingerprint(
                self.config, blender_stage,
                {"storyboard": storyboard_hash, "validated-plan": validation_hash}, tool_versions,
            ),
            artifacts=[{"id": "scene-package", "path": ".pipeline/work/scenes.json", "sha256": pipeline.sha256_file(blender_output)}],
        )
        self.write_manifest()

    def test_accepts_pending_manifest_tied_to_current_project(self):
        errors, warnings = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_legacy_manifest_v1_keeps_legacy_stage_id_and_fingerprint(self):
        self.assertEqual(1, self.manifest["manifest_version"])
        self.assertIn("03-kokoro-narration", self.manifest["stages"])
        errors, warnings = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_generic_pending_manifest_v1_validates_without_claiming_execution(self):
        generic_root = self.root / "generic"
        generic_root.mkdir()
        shutil.copyfile(ROOT / "templates" / "storyboard.json", generic_root / "storyboard.json")
        generic_config = make_tts_config(generic_root)
        generic_config_path = generic_root / "pipeline.json"
        generic_config_path.write_text(json.dumps(generic_config, indent=2), encoding="utf-8")
        generic_manifest_path = generic_root / "run-manifest.json"
        generic_manifest = pipeline.new_pending_manifest(generic_config, generic_config_path)
        generic_manifest_path.write_text(json.dumps(generic_manifest, indent=2), encoding="utf-8")
        errors, warnings = pipeline.validate_manifest_file(generic_config_path, generic_manifest_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertEqual(1, generic_manifest["manifest_version"])
        self.assertIn("03-tts-narration", generic_manifest["stages"])

    def test_validate_manifest_cli_reports_success_without_writing(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["validate-manifest", str(self.config_path), str(self.manifest_path)])
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(0, result)
        self.assertIn("PASS: run manifest", output.getvalue())
        self.assertEqual(before, after)

    def test_manifest_cli_reports_excessive_json_nesting_without_traceback(self):
        deep_manifest = self.root / "deep-manifest.json"
        deep_manifest.write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["validate-manifest", str(self.config_path), str(deep_manifest)])
        self.assertEqual(1, result)
        self.assertIn("nesting", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_validate_manifest_cli_rejects_unpaired_surrogate_in_tool_version_without_traceback(self):
        self.config["tools"][0]["version_requirement"] = "\ud800"
        self.write_config()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["validate-manifest", str(self.config_path), str(self.manifest_path)])
        self.assertEqual(1, result)
        self.assertIn("unpaired Unicode surrogate", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_rejects_invalid_status_attempt_count_and_dependency_reference(self):
        entry = self.manifest["stages"]["02-blender-scenes"]
        entry["status"] = "running"
        entry["attempts"] = 99
        entry["depends_on"] = ["unknown"]
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("status" in error for error in errors))
        self.assertTrue(any("attempts" in error for error in errors))
        self.assertTrue(any("depends_on" in error for error in errors))

    def test_failed_stage_requires_error_and_attempt(self):
        entry = self.manifest["stages"]["02-blender-scenes"]
        entry.update(status="failed", attempts=0)
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("failed" in error for error in errors))

    def test_completed_stage_requires_matching_artifact_hash(self):
        artifact_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("planned fixture", encoding="utf-8")
        digest = pipeline.sha256_file(artifact_path)
        entry = self.manifest["stages"]["01-validate-project"]
        input_hashes = {"storyboard": pipeline.sha256_file(self.root / "storyboard.json")}
        entry.update(
            status="completed",
            attempts=1,
            stage_fingerprint=pipeline.stage_fingerprint(
                self.config, self.config["stages"][0], input_hashes, {}
            ),
            tool_versions={},
            artifacts=[{"id": "validated-plan", "path": ".pipeline/work/validated-plan.json", "sha256": digest}],
        )
        self.manifest["stages"]["02-blender-scenes"]["status"] = "blocked"
        self.manifest["stages"]["02-blender-scenes"]["error"] = "Blender adapter pending."
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertEqual([], errors)
        artifact_path.unlink()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("does not exist as a file" in error for error in errors), errors)
        artifact_path.write_text("planned fixture", encoding="utf-8")
        artifact_path.write_text("changed output", encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("artifact hash" in error for error in errors))

    def test_rejects_completed_stage_when_adapter_is_still_pending(self):
        blender_tool = self.config["tools"][0]
        blender_tool.update(availability="verified", adapter_status="implemented")
        self.config["stages"][1]["adapter_status"] = "pending"
        self.write_config()
        self.completed_blender_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("adapter" in error and "pending" in error for error in errors), errors)

    def test_rejects_completed_stage_when_required_tool_is_unverified(self):
        blender_tool = self.config["tools"][0]
        blender_tool.update(availability="not_checked", adapter_status="implemented")
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.write_config()
        self.completed_blender_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("availability" in error and "blender" in error for error in errors), errors)

    def test_rejects_completed_stage_when_tool_adapter_is_pending(self):
        blender_tool = self.config["tools"][0]
        blender_tool.update(availability="verified", adapter_status="pending")
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.write_config()
        self.completed_blender_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("tool" in error and "adapter" in error and "pending" in error for error in errors), errors)

    def test_rejects_completed_stage_until_dependencies_are_completed(self):
        self.config["tools"][0].update(availability="verified", adapter_status="implemented")
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.write_config()
        self.completed_blender_manifest()
        validation_entry = self.manifest["stages"]["01-validate-project"]
        validation_entry.update(
            status="pending", attempts=0, error=None, artifacts=[], stage_fingerprint=None, tool_versions={}
        )
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("until all dependencies are completed" in error for error in errors), errors)

    def test_completed_stage_requires_each_artifact_exactly_once(self):
        self.config["tools"][0].update(availability="verified", adapter_status="implemented")
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.write_config()
        self.completed_blender_manifest()
        stage_entry = self.manifest["stages"]["02-blender-scenes"]
        stage_entry["artifacts"] = []
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("every configured output exactly once" in error for error in errors), errors)
        self.completed_blender_manifest()
        stage_entry = self.manifest["stages"]["02-blender-scenes"]
        stage_entry["artifacts"].append(dict(stage_entry["artifacts"][0]))
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("unknown or duplicated" in error for error in errors), errors)

    def test_completed_fingerprint_binds_effective_tool_version(self):
        self.config["tools"][0].update(availability="verified", adapter_status="implemented")
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.write_config()
        self.completed_blender_manifest()
        self.manifest["stages"]["02-blender-scenes"]["tool_versions"]["blender"] = "5.2.4"
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("stage_fingerprint is stale" in error for error in errors), errors)

    def test_completed_stage_rejects_missing_or_empty_tool_version(self):
        self.config["tools"][0].update(availability="verified", adapter_status="implemented")
        self.config["stages"][1]["adapter_status"] = "implemented"
        self.write_config()
        self.completed_blender_manifest()
        self.manifest["stages"]["02-blender-scenes"]["tool_versions"] = {}
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("exactly the tools used" in error for error in errors), errors)
        self.completed_blender_manifest()
        self.manifest["stages"]["02-blender-scenes"]["tool_versions"]["blender"] = "  "
        self.write_manifest()
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("non-empty version strings" in error for error in errors), errors)

    def test_config_changes_invalidate_completed_stage_fingerprint(self):
        artifact_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("planned fixture", encoding="utf-8")
        stage = self.config["stages"][0]
        input_hashes = {"storyboard": pipeline.sha256_file(self.root / "storyboard.json")}
        self.manifest["stages"][stage["id"]].update(
            status="completed", attempts=1,
            stage_fingerprint=pipeline.stage_fingerprint(self.config, stage, input_hashes, {}),
            artifacts=[{"id": "validated-plan", "path": ".pipeline/work/validated-plan.json", "sha256": pipeline.sha256_file(artifact_path)}],
        )
        self.write_manifest()
        self.config["profile"]["limits"]["cpu_threads"] = 3
        self.write_config()
        errors, warnings = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("stage_fingerprint is stale" in error for error in errors), errors)
        self.assertTrue(any("pipeline config changed" in warning for warning in warnings), warnings)

    def test_changed_project_input_invalidates_completed_stage_for_resume(self):
        artifact_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("planned fixture", encoding="utf-8")
        stage = self.config["stages"][0]
        entry = self.manifest["stages"][stage["id"]]
        input_hashes = {"storyboard": pipeline.sha256_file(self.root / "storyboard.json")}
        entry.update(
            status="completed",
            attempts=1,
            stage_fingerprint=pipeline.stage_fingerprint(self.config, stage, input_hashes, {}),
            artifacts=[{
                "id": "validated-plan",
                "path": ".pipeline/work/validated-plan.json",
                "sha256": pipeline.sha256_file(artifact_path),
            }],
        )
        self.write_manifest()
        storyboard = json.loads((self.root / "storyboard.json").read_text(encoding="utf-8"))
        storyboard["title"] = "Changed input"
        (self.root / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
        errors, warnings = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("stage_fingerprint is stale" in error for error in errors))
        self.assertTrue(any("inputs changed" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
