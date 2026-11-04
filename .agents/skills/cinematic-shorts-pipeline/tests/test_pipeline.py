import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_pipeline():
    spec = importlib.util.spec_from_file_location("pipeline", ROOT / "scripts" / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pipeline = load_pipeline()


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

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")

    def test_accepts_pending_manifest_tied_to_current_project(self):
        errors, warnings = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_validate_manifest_cli_reports_success_without_writing(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main(["validate-manifest", str(self.config_path), str(self.manifest_path)])
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(0, result)
        self.assertIn("PASS: run manifest", output.getvalue())
        self.assertEqual(before, after)

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
        artifact_path.write_text("changed output", encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path)
        self.assertTrue(any("artifact hash" in error for error in errors))

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
