import hashlib
import builtins
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_pipeline import ROOT, load_pipeline
from test_png_rgb8_decoder import make_png
from test_productive_scenes_profile import make_combined_config
from test_tts_kokoro import make_wav

pipeline = load_pipeline()


class ProductiveCombinedManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_combined_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def make_manifest(self, completed_through=1):
        generated_root = self.root / ".pipeline"
        if generated_root.exists():
            shutil.rmtree(generated_root)
        staging_root = self.root / "synthetic-staging"
        if staging_root.exists():
            shutil.rmtree(staging_root)
        config, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        manifest = pipeline.new_pending_manifest(config, self.config_path)
        source_hashes, input_entries = pipeline._productive_snapshot(config, self.config_path)
        plan = pipeline._validated_plan(config, self.config_path, waves, input_entries, source_hashes)
        plan_bytes = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        plan_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_bytes(plan_bytes)
        plan_hash = hashlib.sha256(plan_bytes).hexdigest()
        stage01 = next(item for item in config["stages"] if item["id"] == "01-validate-project")
        stage01_hashes = {key: source_hashes[key] for key in stage01["inputs"]}
        manifest["stages"][stage01["id"]].update(
            status="completed", attempts=1,
            artifacts=[{"id": "validated-plan", "path": stage01["outputs"][0]["path"], "sha256": plan_hash}],
            stage_fingerprint=pipeline.stage_fingerprint(config, stage01, stage01_hashes, {}),
        )
        if completed_through >= 2:
            self._complete_scenes(config, manifest, source_hashes, plan_hash)
        if completed_through >= 3:
            self._complete_narration(config, manifest, source_hashes, plan_hash)
        return config, manifest

    def _complete_scenes(self, config, manifest, source_hashes, plan_hash):
        stage = next(item for item in config["stages"] if item["id"] == "02-blender-scenes")
        runtime = {
            "blender_version": [5, 2, 3], "blender_version_string": "5.2.3",
            "blender_version_cycle": "release", "blender_executable_sha256": hashlib.sha256(b"blender").hexdigest(),
            "worker_version": 1, "worker_sha256": hashlib.sha256(b"worker").hexdigest(),
        }
        version = json.dumps(runtime, sort_keys=True, separators=(",", ":"))
        input_hashes = {key: plan_hash if key == "validated-plan" else source_hashes[key] for key in stage["inputs"]}
        stage_digest = pipeline.stage_fingerprint(config, stage, input_hashes, {"blender": version})
        staging = self.root / "synthetic-staging"
        scene_id = "shot-01"
        scene_root = staging / "scenes" / scene_id
        (scene_root / "frames").mkdir(parents=True)
        (scene_root / "scene.blend").write_bytes(b"BLENDER17-01v0502" + bytes(128))
        png, _ = make_png(split_idat=65536)
        for frame in range(24):
            (scene_root / "frames" / f"frame-{frame:06d}.png").write_bytes(png)
        archive_path = self.root / ".pipeline" / "work" / "scenes.zip"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        from scene_package_builder import build_scene_package
        archive_hash = build_scene_package(
            staging, archive_path, project_id=config["project"]["id"], scene_id=scene_id,
            run_id=manifest["run_id"], runtime=runtime, input_hashes=input_hashes,
            stage_01_fingerprint=manifest["stages"]["01-validate-project"]["stage_fingerprint"],
            stage_02_fingerprint=stage_digest, render_seed=17,
            observed={"frames": list(range(24)), "minimum_camera_target_distance_m": 0.12,
                      "render_seconds": 1.0, "worker_seconds": 2.0},
        )
        manifest["stages"][stage["id"]].update(
            status="completed", attempts=1, tool_versions={"blender": version},
            stage_fingerprint=stage_digest,
            artifacts=[{"id": "scene-package", "path": stage["outputs"][0]["path"], "sha256": archive_hash}],
        )

    def _complete_narration(self, config, manifest, source_hashes, plan_hash):
        stage = next(item for item in config["stages"] if item["id"] == "03-tts-narration")
        wav_bytes = make_wav()
        audio = pipeline._inspect_kokoro_wav(wav_bytes)
        wav_hash = hashlib.sha256(wav_bytes).hexdigest()
        script_hash = source_hashes["resource-voiceover-script"]
        runtime = {
            "python_version": "3.12.10", "worker_sha256": hashlib.sha256(b"kokoro worker").hexdigest(),
            "dependencies": {"kokoro-onnx": "0.6.1", "onnxruntime": "1.31.1", "numpy": "2.5.4",
                             "phonemizer": "3.6.0", "espeakng-loader": "0.2.4", "soundfile": "0.14.0"},
            "onnx_execution_providers": ["CPUExecutionProvider"],
        }
        assets = {
            asset_id: {"file_name": f"{asset_id}.bin", "size_bytes": size,
                       "sha256": hashlib.sha256(asset_id.encode()).hexdigest(),
                       "version_identity": f"sha256:{hashlib.sha256(asset_id.encode()).hexdigest()}"}
            for asset_id, size in (("model", 100), ("voices", 20))
        }
        provenance = {
            "provenance_version": 1, "record_type": "productive-local-kokoro-tts",
            "run_id": manifest["run_id"], "project_id": config["project"]["id"],
            "dag_stage_id": "03-tts-narration", "provider": "kokoro-onnx",
            "voice": "em_alex", "language": "es", "requested_audio": dict(pipeline.KOKORO_AUDIO),
            "audio": audio, "runtime": runtime, "assets": assets,
            "hashes": {"script_sha256": script_hash, "validated_plan_sha256": plan_hash, "wav_sha256": wav_hash},
            "timing_seconds": {"model_initialization": 1.0, "inference": 2.0},
            "timeout_seconds": pipeline.KOKORO_TIMEOUT_SECONDS, "attempts": 1, "automatic_retries": False,
        }
        tool_identity = {
            "runtime": runtime,
            "assets": {key: {"sha256": value["sha256"]} for key, value in assets.items()},
        }
        tool_version = json.dumps(tool_identity, sort_keys=True, separators=(",", ":"))
        wav_path = self.root / ".pipeline" / "work" / "narration.wav"
        provenance_path = self.root / ".pipeline" / "work" / "narration-provenance.json"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(wav_bytes)
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        input_hashes = {"validated-plan": plan_hash, "resource-voiceover-script": script_hash}
        manifest["stages"][stage["id"]].update(
            status="completed", attempts=1, tool_versions={"kokoro": tool_version},
            stage_fingerprint=pipeline.stage_fingerprint(config, stage, input_hashes, {"kokoro": tool_version}),
            artifacts=[
                {"id": "narration", "path": stage["outputs"][0]["path"], "sha256": wav_hash},
                {"id": "narration-provenance", "path": stage["outputs"][1]["path"], "sha256": hashlib.sha256(provenance_path.read_bytes()).hexdigest()},
            ],
        )

    def validate(self, manifest):
        errors, warnings = pipeline.validate_manifest(manifest, self.config, self.config_path, self.root / "manifest.json")
        return errors, warnings

    def test_valid_manifests_pass_after_each_combined_stage_checkpoint(self):
        for level in (1, 2, 3):
            with self.subTest(completed_through=level):
                self.config = make_combined_config(self.root)
                self.write_config()
                config, manifest = self.make_manifest(level)
                self.config = config
                self.assertEqual(([], []), self.validate(manifest))

    def test_rejects_rehashed_provenance_detached_from_script_and_plan(self):
        for field, replacement in (("script_sha256", "a" * 64), ("validated_plan_sha256", "b" * 64)):
            with self.subTest(field=field):
                self.config = make_combined_config(self.root)
                self.write_config()
                config, manifest = self.make_manifest(3)
                self.config = config
                provenance_path = self.root / ".pipeline" / "work" / "narration-provenance.json"
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                provenance["hashes"][field] = replacement
                provenance_path.write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                entry = manifest["stages"]["03-tts-narration"]
                next(item for item in entry["artifacts"] if item["id"] == "narration-provenance")["sha256"] = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
                errors, _ = self.validate(manifest)
                self.assertTrue(any("provenance" in error and ("script" in error or "validated-plan" in error) for error in errors), errors)

    def test_rejects_inconsistent_runtime_and_incomplete_provenance(self):
        for mutation in ("runtime", "missing-hashes"):
            with self.subTest(mutation=mutation):
                self.config = make_combined_config(self.root)
                self.write_config()
                config, manifest = self.make_manifest(3)
                self.config = config
                entry = manifest["stages"]["03-tts-narration"]
                provenance_path = self.root / ".pipeline" / "work" / "narration-provenance.json"
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                if mutation == "runtime":
                    provenance["runtime"]["onnx_execution_providers"] = ["CUDAExecutionProvider"]
                    version_record = json.loads(entry["tool_versions"]["kokoro"])
                    version_record["runtime"] = provenance["runtime"]
                    entry["tool_versions"]["kokoro"] = json.dumps(version_record, sort_keys=True, separators=(",", ":"))
                    hashes = {"validated-plan": manifest["stages"]["01-validate-project"]["artifacts"][0]["sha256"],
                              "resource-voiceover-script": hashlib.sha256((self.root / "voiceover-script.txt").read_bytes()).hexdigest()}
                    stage = next(item for item in config["stages"] if item["id"] == "03-tts-narration")
                    entry["stage_fingerprint"] = pipeline.stage_fingerprint(config, stage, hashes, entry["tool_versions"])
                else:
                    provenance.pop("hashes")
                provenance_path.write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                next(item for item in entry["artifacts"] if item["id"] == "narration-provenance")["sha256"] = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
                errors, _ = self.validate(manifest)
                self.assertTrue(any("provenance" in error for error in errors), errors)

    def test_rejects_rehashed_wav_and_scene_archive_tampering(self):
        self.config = make_combined_config(self.root)
        self.write_config()
        config, manifest = self.make_manifest(3)
        self.config = config
        wav_path = self.root / ".pipeline" / "work" / "narration.wav"
        wav_path.write_bytes(wav_path.read_bytes() + b"tamper")
        next(item for item in manifest["stages"]["03-tts-narration"]["artifacts"] if item["id"] == "narration")["sha256"] = hashlib.sha256(wav_path.read_bytes()).hexdigest()
        errors, _ = self.validate(manifest)
        self.assertTrue(any("WAV" in error or "WAV" in error.upper() for error in errors), errors)

        self.config = make_combined_config(self.root)
        self.write_config()
        config, manifest = self.make_manifest(2)
        self.config = config
        archive = self.root / ".pipeline" / "work" / "scenes.zip"
        archive.write_bytes(b"not a scene package")
        manifest["stages"]["02-blender-scenes"]["artifacts"][0]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        errors, _ = self.validate(manifest)
        self.assertTrue(any("scene-package" in error for error in errors), errors)

    def test_rejects_stage_04_implemented_and_completed(self):
        self.config = make_combined_config(self.root)
        self.config["stages"][3]["adapter_status"] = "implemented"
        self.write_config()
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertTrue(errors)

        self.config = make_combined_config(self.root)
        self.write_config()
        config, manifest = self.make_manifest(1)
        self.config = config
        stage04 = manifest["stages"]["04-remotion-compose"]
        stage04.update(status="completed", attempts=1, error=None, stage_fingerprint="a" * 64,
                       tool_versions={"remotion": "test"}, artifacts=[])
        errors, _ = self.validate(manifest)
        self.assertTrue(any("cannot be completed while its adapter is pending" in error for error in errors), errors)

    def test_validate_manifest_is_read_only_and_launches_no_processes(self):
        self.config = make_combined_config(self.root)
        self.write_config()
        config, manifest = self.make_manifest(3)
        self.config = config
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        original_builtin_open, original_path_open = builtins.open, Path.open

        def read_only_open(open_function):
            def checked_open(file, mode="r", *args, **kwargs):
                if any(flag in mode for flag in "wax+"):
                    raise AssertionError(f"write attempted during validate-manifest: {file}")
                return open_function(file, mode, *args, **kwargs)
            return checked_open

        with mock.patch.object(subprocess, "run", side_effect=AssertionError("process launch")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process launch")), \
             mock.patch.object(tempfile, "mkstemp", side_effect=AssertionError("temporary file")), \
             mock.patch.object(tempfile, "mkdtemp", side_effect=AssertionError("temporary directory")), \
             mock.patch.object(builtins, "open", new=read_only_open(original_builtin_open)), \
             mock.patch.object(Path, "open", new=read_only_open(original_path_open)):
            self.assertEqual(([], []), pipeline.validate_manifest_file(self.config_path, manifest_path))
        self.assertEqual(before, sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
