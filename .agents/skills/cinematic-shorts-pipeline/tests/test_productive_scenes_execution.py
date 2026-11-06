import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_pipeline import load_pipeline
from test_png_rgb8_decoder import make_png
from test_productive_scenes_profile import make_scenes_config


pipeline = load_pipeline()
VALID_PNG, _ = make_png()


class ProductiveScenesExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_scenes_config(self.root)
        self.scene_stage = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        self.scene_stage["adapter_status"] = "implemented"
        self.blender_tool = next(tool for tool in self.config["tools"] if tool["id"] == "blender")
        self.blender_tool.update(adapter_status="implemented", availability="verified")
        self.config_path = self.root / "pipeline.json"
        self.write_config()
        self.blender_path = self.root / ("blender.exe" if os.name == "nt" else "blender")
        self.blender_path.write_bytes(b"mock blender binary")
        if os.name != "nt":
            self.blender_path.chmod(self.blender_path.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def fake_probe(self, command, cwd):
        return type("Probe", (), {"returncode": 0, "stdout": "Blender 5.2.3\n", "stderr": ""})()

    def fake_worker(self, command, cwd):
        request_path = Path(command[command.index("--request") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        root = Path(request["staging_root"])
        scene_id = request["scene_spec"]["scene_id"]
        blend = root / "scenes" / scene_id / "scene.blend"
        blend.parent.mkdir(parents=True)
        blend.write_bytes(b"BLENDER_v502" + bytes(128))
        frames = blend.parent / "frames"
        frames.mkdir()
        outputs = [f"scenes/{scene_id}/scene.blend"]
        for frame in range(24):
            name = f"scenes/{scene_id}/frames/frame-{frame:06d}.png"
            (root / name).write_bytes(VALID_PNG)
            outputs.append(name)
        response = {
            "record_type": "blender-scenes-worker-result", "worker_version": 1,
            "blender_version": [5, 2, 3], "blender_version_string": "5.2.3",
            "project_id": self.config["project"]["id"], "scene_id": scene_id,
            "frames": list(range(24)), "outputs": outputs,
            "minimum_camera_target_distance_m": 4.0,
            "render_seconds": 1.0, "worker_seconds": 1.2,
        }
        (root / "response.json").write_text(json.dumps(response), encoding="utf-8")

    def run_scenes(self, worker=None):
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=self.fake_probe), \
             mock.patch.object(pipeline, "_run_blender_process", side_effect=worker or self.fake_worker):
            return pipeline.run_productive_scenes(self.config_path, blender_path=self.blender_path)

    def test_publishes_verified_zip_and_only_completes_stages_01_02(self):
        result = self.run_scenes()
        manifest_path = Path(result["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        statuses = {key: value["status"] for key, value in manifest["stages"].items()}
        self.assertEqual("completed", statuses["01-validate-project"])
        self.assertEqual("completed", statuses["02-blender-scenes"])
        self.assertEqual({"pending"}, set(statuses.values()) - {"completed"})
        archive = self.root / ".pipeline" / "work" / "scenes.zip"
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), manifest["stages"]["02-blender-scenes"]["artifacts"][0]["sha256"])
        before = sorted(str(path) for path in self.root.rglob("*"))
        self.assertEqual(([], []), pipeline.validate_manifest_file(self.config_path, manifest_path))
        self.assertEqual(before, sorted(str(path) for path in self.root.rglob("*")))

        original_zip = archive.read_bytes()
        archive.write_bytes(original_zip[:-1] + bytes([original_zip[-1] ^ 1]))
        manifest["stages"]["02-blender-scenes"]["artifacts"][0]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("scene-package" in error for error in errors), errors)

        archive.write_bytes(original_zip)
        rebuilt = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original_zip)) as source, zipfile.ZipFile(rebuilt, "w", allowZip64=False) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "provenance.json":
                    provenance = json.loads(data)
                    provenance["render"]["seed"] = 18
                    data = (json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                target.writestr(info, data)
        archive.write_bytes(rebuilt.getvalue())
        manifest["stages"]["02-blender-scenes"]["artifacts"][0]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("scene-package" in error and "seed" in error for error in errors), errors)

    def test_optional_resources_without_kind_and_missing_are_compatible(self):
        self.config["project"]["resources"] = [
            {"id": "optional-data", "path": "optional.json", "required": False},
            {"id": "optional-missing", "path": "missing.json", "required": False},
            *self.config["project"]["resources"],
        ]
        (self.root / "optional.json").write_text("{}\n", encoding="utf-8")
        self.write_config()
        loaded, errors, warnings, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual(["optional resource is not present: missing.json"], warnings)
        self.assertTrue(pipeline._is_productive_scenes_config(loaded))

        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=self.fake_probe) as probe, \
             mock.patch.object(pipeline, "_run_blender_process", side_effect=self.fake_worker) as worker:
            result = pipeline.run_productive_scenes(self.config_path, blender_path=self.blender_path)
        self.assertEqual(1, probe.call_count)
        self.assertEqual(1, worker.call_count)
        manifest_path = Path(result["manifest_path"])
        errors, warnings = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertEqual([], errors)
        self.assertEqual(["optional resource is not present: missing.json"], warnings)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["02-blender-scenes"]["status"])

    def test_scene_warning_policy_rejects_unexpected_and_stale_warnings(self):
        config = self.config
        expected = "optional resource is not present: missing.json"
        config["project"]["resources"].append(
            {"id": "optional-missing", "path": "missing.json", "required": False}
        )
        self.assertEqual([], pipeline._unexpected_scene_warnings(config, self.config_path, [expected]))
        self.assertEqual(
            ["pipeline config changed since this run"],
            pipeline._unexpected_scene_warnings(config, self.config_path, [expected, "pipeline config changed since this run"]),
        )
        (self.root / "missing.json").write_text("{}\n", encoding="utf-8")
        self.assertEqual([expected], pipeline._unexpected_scene_warnings(config, self.config_path, [expected]))

    def test_worker_failure_never_completes_stage_02(self):
        def fail_worker(command, cwd):
            raise ValueError("synthetic worker failure")
        with self.assertRaisesRegex(ValueError, "synthetic worker failure"):
            self.run_scenes(fail_worker)
        manifests = list(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        self.assertEqual(1, len(manifests))
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertNotEqual("completed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())

    def test_rejects_existing_output_before_blender_probe(self):
        archive = self.root / ".pipeline/work/scenes.zip"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"existing")
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=AssertionError("probe must not run")):
            with self.assertRaisesRegex(ValueError, "already exists"):
                pipeline.run_productive_scenes(self.config_path, blender_path=self.blender_path)
        self.assertEqual(b"existing", archive.read_bytes())

    def test_invalid_png_never_publishes_or_completes_stage_02(self):
        def bad_png_worker(command, cwd):
            self.fake_worker(command, cwd)
            request_path = Path(command[command.index("--request") + 1])
            staging = Path(json.loads(request_path.read_text(encoding="utf-8"))["staging_root"])
            (staging / "scenes/shot-01/frames/frame-000000.png").write_bytes(b"invalid PNG")
        with self.assertRaises(ValueError):
            self.run_scenes(bad_png_worker)
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual([], manifest["stages"]["02-blender-scenes"]["artifacts"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())

    def test_changed_inputs_fail_both_stages_without_zip(self):
        def mutating_worker(command, cwd):
            self.fake_worker(command, cwd)
            spec = self.root / "scene-spec.json"
            spec.write_text(spec.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "project inputs changed"):
            self.run_scenes(mutating_worker)
        manifest = json.loads(next(self.root.glob(".pipeline/runs/run-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())

    def test_cancelled_worker_records_failure(self):
        def cancel_worker(command, cwd):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_scenes(cancel_worker)
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual("KeyboardInterrupt", manifest["stages"]["02-blender-scenes"]["error"])
        self.assertEqual(([], []), pipeline.validate_manifest_file(self.config_path, manifest_path))

    def test_timeout_records_failure_without_zip(self):
        def timeout_worker(command, cwd):
            raise TimeoutError("synthetic Blender timeout")
        with self.assertRaisesRegex(ValueError, "synthetic Blender timeout"):
            self.run_scenes(timeout_worker)
        manifest = json.loads(next(self.root.glob(".pipeline/runs/run-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())

    def test_partial_publication_remains_untrusted(self):
        def fail_copy(source, destination):
            Path(destination).write_bytes(b"partial")
            raise OSError("synthetic publication failure")
        with mock.patch.object(pipeline, "_copy_exclusive_archive", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "synthetic publication failure"):
                self.run_scenes()
        manifest = json.loads(next(self.root.glob(".pipeline/runs/run-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual([], manifest["stages"]["02-blender-scenes"]["artifacts"])
        self.assertEqual(b"partial", (self.root / ".pipeline/work/scenes.zip").read_bytes())

    def test_rejects_output_symlink_before_blender_probe(self):
        archive = self.root / ".pipeline/work/scenes.zip"
        archive.parent.mkdir(parents=True)
        target = archive.parent / "target.zip"
        target.write_bytes(b"safe")
        try:
            archive.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=AssertionError("probe must not run")):
            with self.assertRaisesRegex(ValueError, "link|reparse"):
                pipeline.run_productive_scenes(self.config_path, blender_path=self.blender_path)
        self.assertEqual(b"safe", target.read_bytes())

    def test_checkpoint_failure_leaves_unadopted_orphan(self):
        original = pipeline._write_scene_checkpoint

        def fail_final_checkpoint(path, manifest, identity):
            if manifest["stages"]["02-blender-scenes"]["status"] == "completed":
                raise OSError("synthetic checkpoint crash")
            return original(path, manifest, identity)

        with mock.patch.object(pipeline, "_write_scene_checkpoint", side_effect=fail_final_checkpoint):
            with self.assertRaisesRegex(OSError, "synthetic checkpoint crash"):
                self.run_scenes()
        archive = self.root / ".pipeline/work/scenes.zip"
        self.assertTrue(archive.is_file())
        manifest = json.loads(next(self.root.glob(".pipeline/runs/run-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_scenes()


if __name__ == "__main__":
    unittest.main()
