import hashlib
import json
import os
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_pipeline import ROOT, load_pipeline
from test_productive_scenes_profile import make_combined_config
from test_productive_scenes_execution import VALID_PNG
from test_tts_kokoro import make_wav, worker_record


pipeline = load_pipeline()


class ProductiveCompositionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_combined_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.write_config()
        self.wav_bytes = make_wav()
        self.model = self.root / "model.onnx"
        self.voices = self.root / "voices.bin"
        self.python = Path(os.sys.executable).resolve()
        self.model.write_bytes(b"synthetic model")
        self.voices.write_bytes(b"synthetic voices")
        self.blender = self.root / ("blender.exe" if os.name == "nt" else "blender")
        self.blender.write_bytes(b"synthetic Blender executable")
        if os.name != "nt":
            self.blender.chmod(self.blender.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def fake_kokoro(self, *args, **kwargs):
        wav = self.wav_bytes
        audio = pipeline._inspect_kokoro_wav(wav)
        record = worker_record(wav)
        return {
            "wav_bytes": wav, "audio": audio, "worker_result": record,
            "versions": record["versions"], "python_version": record["python_version"],
            "worker_sha256": hashlib.sha256(b"fixed kokoro worker").hexdigest(),
            "model": {"file_name": self.model.name, "size_bytes": self.model.stat().st_size,
                      "sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
                      "version_identity": "sha256:" + hashlib.sha256(self.model.read_bytes()).hexdigest()},
            "voices": {"file_name": self.voices.name, "size_bytes": self.voices.stat().st_size,
                       "sha256": hashlib.sha256(self.voices.read_bytes()).hexdigest(),
                       "version_identity": "sha256:" + hashlib.sha256(self.voices.read_bytes()).hexdigest()},
        }

    def fake_blender(self, config_path, runtime):
        staging = self.root / "blender-staging"
        scene_id = "shot-01"
        scene_root = staging / "scenes" / scene_id
        (scene_root / "frames").mkdir(parents=True)
        (scene_root / "scene.blend").write_bytes(b"BLENDER17-01v0502" + bytes(128))
        for frame in range(24):
            (scene_root / "frames" / f"frame-{frame:06d}.png").write_bytes(VALID_PNG)
        response = {
            "scene_id": scene_id, "blender_version": [5, 2, 3],
            "blender_version_string": "5.2.3", "worker_version": 1,
            "frames": list(range(24)), "minimum_camera_target_distance_m": 4.0,
            "render_seconds": 1.0, "worker_seconds": 1.2,
        }
        return {"staging_root": str(staging), "response": response}

    def run_combined(self):
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": [5, 2, 3], "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=self.fake_blender):
            return pipeline.run_productive_composition(
                self.config_path, python_path=self.python, model_path=self.model,
                voices_path=self.voices, blender_path=self.blender,
            )

    def test_audio_over_two_seconds_stops_before_blender_and_keeps_completed_audio(self):
        self.wav_bytes = make_wav(samples=[1200] * 48_001)
        blender_calls = []
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value={
            "path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
            "version": (5, 2, 3), "version_string": "5.2.3",
        }), mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=lambda *args: blender_calls.append(args)):
            with self.assertRaisesRegex(ValueError, "48,000-sample"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )
        self.assertEqual([], blender_calls)
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("completed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("blocked", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual("pending", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertTrue((self.root / ".pipeline/work/narration.wav").is_file())
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertEqual([], errors)

    def test_audio_exactly_48_000_samples_is_eligible_for_stage_02(self):
        self.wav_bytes = make_wav(samples=[1200] * 48_000)
        result = self.run_combined()
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(48_000, result["audio"]["frames"])
        self.assertEqual("completed", manifest["stages"]["02-blender-scenes"]["status"])

    def test_kokoro_failure_keeps_stage_02_unrun_and_records_one_failure(self):
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value={
            "path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
            "version": (5, 2, 3), "version_string": "5.2.3",
        }), mock.patch.object(pipeline, "_productive_worker", side_effect=ValueError("synthetic Kokoro failure")), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=AssertionError("Blender must not run")):
            with self.assertRaisesRegex(ValueError, "synthetic Kokoro failure"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("pending", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual("pending", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertEqual(([], []), pipeline.validate_manifest_file(self.config_path, manifest_path))

    def test_blender_failure_preserves_only_verified_stage_01_and_stage_03(self):
        def fail_blender(*args):
            raise ValueError("synthetic Blender worker failure")
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=fail_blender):
            with self.assertRaisesRegex(ValueError, "synthetic Blender worker failure"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("completed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())
        self.assertEqual(([], []), pipeline.validate_manifest_file(self.config_path, manifest_path))

    def test_rejects_independent_profiles_before_creating_a_run(self):
        stage = next(item for item in self.config["stages"] if item["id"] == "02-blender-scenes")
        stage["adapter_status"] = "pending"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "invalid productive audiovisual profile|complete audiovisual-combined"):
            pipeline.run_productive_composition(
                self.config_path, python_path=self.python, model_path=self.model,
                voices_path=self.voices, blender_path=self.blender,
            )
        self.assertFalse((self.root / ".pipeline/runs").exists())

    def test_stage_01_publication_failure_records_controlled_failure_without_running_workers(self):
        def fail_plan(path, payload, owned):
            if Path(path).name == "validated-plan.json":
                raise OSError("synthetic Stage 01 publication failure")
            return original(path, payload, owned)
        original = pipeline._write_exclusive_file
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_plan), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=AssertionError("Kokoro must not run")), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=AssertionError("Blender must not run")):
            with self.assertRaisesRegex(OSError, "Stage 01 publication failure"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("blocked", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("blocked", manifest["stages"]["02-blender-scenes"]["status"])

    def test_input_change_during_kokoro_invalidates_stage_01_and_never_runs_blender(self):
        def mutate_project(*args, **kwargs):
            result = self.fake_kokoro(*args, **kwargs)
            storyboard = self.root / "storyboard.json"
            storyboard.write_text(storyboard.read_text(encoding="utf-8") + " ", encoding="utf-8")
            return result
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=mutate_project), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=AssertionError("Blender must not run")):
            with self.assertRaisesRegex(ValueError, "project inputs changed"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )
        manifest = json.loads(next(self.root.glob(".pipeline/runs/run-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("blocked", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertFalse((self.root / ".pipeline/work/narration.wav").exists())

    def test_wav_mutation_during_blender_invalidates_stage_03_before_zip_publication(self):
        def mutate_wav_during_blender(config_path, runtime):
            response = self.fake_blender(config_path, runtime)
            wav_path = self.root / ".pipeline/work/narration.wav"
            wav_path.write_bytes(wav_path.read_bytes() + b"tamper")
            return response
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": [5, 2, 3], "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=mutate_wav_during_blender):
            with self.assertRaisesRegex(ValueError, "Kokoro outputs changed during Blender"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual([], manifest["stages"]["03-tts-narration"]["artifacts"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())
        self.assertFalse((self.root / ".pipeline/work/narration.wav").exists())

    def test_runs_combined_stages_in_one_manifest_and_completes_01_03_02(self):
        result = self.run_combined()
        manifest_path = Path(result["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(result["run_id"], manifest["run_id"])
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("completed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("completed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertTrue(all(manifest["stages"][stage]["status"] == "pending"
                            for stage in ("04-remotion-compose", "05-ffmpeg-finalize", "06-quality-control")))
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertEqual([], errors)
        stage01 = manifest["stages"]["01-validate-project"]
        plan_path = self.root / stage01["artifacts"][0]["path"]
        plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        narration_provenance = json.loads((self.root / ".pipeline/work/narration-provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(plan_hash, narration_provenance["hashes"]["validated_plan_sha256"])
        self.assertEqual(manifest["run_id"], narration_provenance["run_id"])
        with zipfile.ZipFile(self.root / ".pipeline/work/scenes.zip") as package:
            scene_provenance = json.loads(package.read("provenance.json"))
        self.assertEqual(manifest["run_id"], scene_provenance["run_id"])
        self.assertEqual(stage01["stage_fingerprint"], scene_provenance["inputs"]["stage_01_fingerprint"])


if __name__ == "__main__":
    unittest.main()
