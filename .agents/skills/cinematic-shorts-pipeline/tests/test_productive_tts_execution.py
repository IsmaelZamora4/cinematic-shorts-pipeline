import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_pipeline import load_pipeline, make_tts_config
from test_tts_kokoro import make_wav, worker_record


ROOT = Path(__file__).resolve().parents[1]
pipeline = load_pipeline()


def make_productive_config(root):
    config = make_tts_config(root, provider="kokoro")
    validation = next(stage for stage in config["stages"] if stage["id"] == "01-validate-project")
    validation["inputs"] = ["pipeline-config", "storyboard", "resource-voiceover-script"]
    tts = next(stage for stage in config["stages"] if stage["id"] == "03-tts-narration")
    tts.update(adapter_status="implemented", inputs=["validated-plan", "resource-voiceover-script"])
    tts["tts"].update(voice="em_alex", language="es")
    tts["tts"]["audio"].update(sample_rate_hz=24000, channels=1, sample_format="pcm_s16le")
    tool = next(tool for tool in config["tools"] if tool["id"] == "kokoro")
    tool.update(
        availability="verified",
        adapter_status="implemented",
        version_requirement="kokoro-onnx==0.6.1; Python>=3.10,<3.14; CPUExecutionProvider",
    )
    return config


class ProductiveTtsContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config_path = self.root / "pipeline.json"
        self.config = make_productive_config(self.root)
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def test_accepts_only_productive_kokoro_contract_and_validates_pending_legacy_contracts(self):
        loaded, errors, warnings, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertEqual("implemented", next(s for s in loaded["stages"] if s["id"] == "03-tts-narration")["adapter_status"])

        piper_root = self.root / "piper"
        piper_root.mkdir()
        shutil.copyfile(ROOT / "templates" / "storyboard.json", piper_root / "storyboard.json")
        piper = make_tts_config(piper_root, provider="piper")
        piper_path = piper_root / "pipeline.json"
        piper_path.write_text(json.dumps(piper), encoding="utf-8")
        _, errors, _, _ = pipeline.load_and_validate_config(piper_path)
        self.assertEqual([], errors)

    def test_rejects_productive_piper_and_missing_validation_artifact_input(self):
        tts = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts["tts"]["provider"] = "piper"
        tts["tools"] = ["piper"]
        tts["tts"].update(voice="en_US-lessac-medium", language="en-US")
        tts["tts"]["audio"]["sample_rate_hz"] = 22050
        self.config["tools"].append({
            "id": "piper", "executable": "piper", "version_requirement": "unverified",
            "availability": "not_checked", "adapter_status": "pending",
        })
        tts_tool = next(tool for tool in self.config["tools"] if tool["id"] == "piper")
        tts_tool.update(availability="verified", adapter_status="implemented")
        self.write_config()
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertTrue(any("Piper" in error or "piper" in error for error in errors), errors)

        self.config = make_productive_config(self.root)
        tts = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts["inputs"] = ["resource-voiceover-script"]
        self.write_config()
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertTrue(any("validated-plan" in error for error in errors), errors)

    def test_pipeline_config_is_semantic_pseudo_input_and_only_allowed_on_stage_one(self):
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        input_hashes = pipeline._project_input_hashes(loaded, self.config_path)
        self.assertEqual(pipeline.fingerprint(loaded), input_hashes["pipeline-config"])
        initial = pipeline.stage_fingerprint(
            loaded,
            loaded["stages"][0],
            {key: input_hashes[key] for key in loaded["stages"][0]["inputs"]},
            {},
        )

        self.config_path.write_text(json.dumps(loaded, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        reformatted, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        reformatted_hashes = pipeline._project_input_hashes(reformatted, self.config_path)
        self.assertEqual(input_hashes["pipeline-config"], reformatted_hashes["pipeline-config"])
        self.assertEqual(
            initial,
            pipeline.stage_fingerprint(
                reformatted,
                reformatted["stages"][0],
                {key: reformatted_hashes[key] for key in reformatted["stages"][0]["inputs"]},
                {},
            ),
        )

        self.config = reformatted
        tts = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tts["inputs"].append("pipeline-config")
        self.write_config()
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertTrue(any("pipeline-config" in error for error in errors), errors)

    def test_plan_fingerprint_invalidates_tts_dependency(self):
        hashes = pipeline._project_input_hashes(self.config, self.config_path)
        tts = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        script_hash = hashes["resource-voiceover-script"]
        before = pipeline.stage_fingerprint(
            self.config, tts, {"resource-voiceover-script": script_hash, "validated-plan": "a" * 64}, {"kokoro": "{}"}
        )
        after = pipeline.stage_fingerprint(
            self.config, tts, {"resource-voiceover-script": script_hash, "validated-plan": "b" * 64}, {"kokoro": "{}"}
        )
        self.assertNotEqual(before, after)

    def test_semantic_pipeline_change_changes_stage_one_and_downstream_plan_hash(self):
        before, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        before_inputs = pipeline._project_input_hashes(before, self.config_path)
        stage = before["stages"][0]
        before_fp = pipeline.stage_fingerprint(before, stage, {key: before_inputs[key] for key in stage["inputs"]}, {})
        before_plan = pipeline._validated_plan(before, self.config_path, pipeline._topological_waves(before["stages"], []), [], before_inputs)

        self.config["profile"]["width"] = 720
        self.config["profile"]["height"] = 1280
        self.write_config()
        after, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        after_inputs = pipeline._project_input_hashes(after, self.config_path)
        after_fp = pipeline.stage_fingerprint(after, after["stages"][0], {key: after_inputs[key] for key in after["stages"][0]["inputs"]}, {})
        after_plan = pipeline._validated_plan(after, self.config_path, waves, [], after_inputs)
        self.assertNotEqual(before_fp, after_fp)
        self.assertNotEqual(before_plan["config_fingerprint"], after_plan["config_fingerprint"])
        self.assertNotEqual(before_plan["stage_fingerprint"], after_plan["stage_fingerprint"])


class ProductiveTtsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config_path = self.root / "pipeline.json"
        self.config = make_productive_config(self.root)
        self.write_config()
        self.model_path = self.root / "model.onnx"
        self.voices_path = self.root / "voices.bin"
        self.model_path.write_bytes(b"fake model bytes")
        self.voices_path.write_bytes(b"fake voices bytes")
        self.wav_bytes = make_wav()
        self.record = worker_record(self.wav_bytes)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def args(self):
        return {
            "python_path": str(Path(sys.executable).resolve()),
            "model_path": str(self.model_path.resolve()),
            "voices_path": str(self.voices_path.resolve()),
        }

    def run_execution(self, **kwargs):
        return pipeline.run_productive_tts(self.config_path, **(kwargs or self.args()))

    def fake_worker(self, *, returncode=0, wav_bytes=None, record=None):
        wav_bytes = self.wav_bytes if wav_bytes is None else wav_bytes
        record = self.record if record is None else record
        stderr = b"KOKORO_RESULT=" + json.dumps(record, sort_keys=True).encode("utf-8") + b"\n"

        def run(command, *, input, **options):
            self.assertEqual(self.config["project"]["resources"][0]["path"], "voiceover-script.txt")
            self.assertEqual((self.root / "voiceover-script.txt").read_bytes(), input)
            self.assertIs(False, options["shell"])
            self.assertEqual(pipeline.KOKORO_TIMEOUT_SECONDS, options["timeout"])
            self.assertIn(str(self.model_path.resolve()), command)
            self.assertIn(str(self.voices_path.resolve()), command)
            self.assertTrue(any(item.endswith("tts-kokoro-worker.py") for item in command))
            return subprocess.CompletedProcess(command, returncode, stdout=wav_bytes, stderr=stderr)

        return run

    def manifest_path(self):
        paths = list((self.root / ".pipeline" / "runs").glob("run-*/manifest.json"))
        self.assertEqual(1, len(paths))
        return paths[0]

    def test_executes_only_validation_and_kokoro_and_persists_verified_manifest(self):
        with mock.patch.object(subprocess, "run", side_effect=self.fake_worker()) as run:
            result = self.run_execution()
        run.assert_called_once()

        manifest_path = Path(result["manifest_path"])
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["manifest_version"])
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("completed", manifest["stages"]["03-tts-narration"]["status"])
        for stage_id in ("02-blender-scenes", "04-remotion-compose", "05-ffmpeg-finalize", "06-quality-control"):
            self.assertEqual("pending", manifest["stages"][stage_id]["status"])
        errors, warnings = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

        plan_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        plan_bytes = plan_path.read_bytes()
        plan = json.loads(plan_bytes)
        self.assertEqual(1, plan["validated_plan_version"])
        self.assertEqual(pipeline.fingerprint(self.config), plan["config_fingerprint"])
        self.assertNotIn("A test voiceover script.", plan_bytes.decode("utf-8"))
        self.assertNotIn(str(self.root), plan_bytes.decode("utf-8"))
        self.assertNotIn("generated_at", plan)
        self.assertEqual(hashlib.sha256(self.wav_bytes).hexdigest(), result["provenance"]["hashes"]["wav_sha256"])
        provenance = json.loads((self.root / ".pipeline" / "work" / "narration-provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(self.model_path.read_bytes()).hexdigest(), provenance["assets"]["model"]["sha256"])
        self.assertEqual(hashlib.sha256(self.voices_path.read_bytes()).hexdigest(), provenance["assets"]["voices"]["sha256"])
        self.assertEqual("sha256:" + provenance["assets"]["model"]["sha256"], provenance["assets"]["model"]["version_identity"])
        self.assertEqual(result["tool_versions"]["kokoro"], manifest["stages"]["03-tts-narration"]["tool_versions"]["kokoro"])
        tool_identity = json.loads(result["tool_versions"]["kokoro"])
        self.assertEqual({"runtime", "assets"}, set(tool_identity))
        self.assertEqual(self.record["versions"], provenance["runtime"]["dependencies"])
        provenance_bytes = json.dumps(provenance, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.assertNotIn(str(self.root).encode(), provenance_bytes)
        self.assertEqual([], list((self.root / ".pipeline" / "tts-local-runs").glob("**/*")) if (self.root / ".pipeline" / "tts-local-runs").exists() else [])

    def test_worker_timeout_records_failed_stage_without_completion_or_retry(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["worker"], 300)) as run:
            with self.assertRaisesRegex(ValueError, "timeout"):
                self.run_execution()
        run.assert_called_once()
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual(1, manifest["stages"]["03-tts-narration"]["attempts"])
        self.assertEqual([], manifest["stages"]["03-tts-narration"]["artifacts"])
        errors, _ = pipeline.validate_manifest_file(self.config_path, self.manifest_path())
        self.assertEqual([], errors)

    def test_rejects_invalid_worker_audio_before_productive_publication(self):
        with mock.patch.object(subprocess, "run", side_effect=self.fake_worker(wav_bytes=b"not a wav")):
            with self.assertRaises(ValueError):
                self.run_execution()
        self.assertFalse((self.root / ".pipeline" / "work" / "narration.wav").exists())
        self.assertFalse((self.root / ".pipeline" / "work" / "narration-provenance.json").exists())
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual([], manifest["stages"]["03-tts-narration"]["artifacts"])

    def test_rejects_existing_productive_output_without_overwrite_or_worker(self):
        output = self.root / ".pipeline" / "work" / "narration.wav"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"keep me")
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("worker must not start")):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.run_execution()
        self.assertEqual(b"keep me", output.read_bytes())
        self.assertFalse((self.root / ".pipeline" / "runs").exists())

    def test_rejects_script_change_during_inference(self):
        fake = self.fake_worker()

        def mutate_script(*args, **kwargs):
            result = fake(*args, **kwargs)
            (self.root / "voiceover-script.txt").write_text("changed during synthesis", encoding="utf-8")
            return result

        with mock.patch.object(subprocess, "run", side_effect=mutate_script):
            with self.assertRaisesRegex(ValueError, "changed during execution"):
                self.run_execution()
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertNotEqual("completed", manifest["stages"]["03-tts-narration"]["status"])

    def test_failed_worker_does_not_publish_and_validates_failed_manifest(self):
        def fail(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"worker failed")

        with mock.patch.object(subprocess, "run", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "worker failed"):
                self.run_execution()
        manifest_path = self.manifest_path()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual([], manifest["stages"]["03-tts-narration"]["artifacts"])
        self.assertFalse((self.root / ".pipeline" / "work" / "narration.wav").exists())
        self.assertEqual(([], []), pipeline.validate_manifest_file(self.config_path, manifest_path))

    def test_second_output_write_failure_cleans_only_owned_partial(self):
        original = pipeline._write_exclusive_file

        def fail_second(path, payload, owned):
            if Path(path).name == "narration-provenance.json":
                raise OSError("injected provenance write failure")
            return original(path, payload, owned)

        with mock.patch.object(subprocess, "run", side_effect=self.fake_worker()), mock.patch.object(
            pipeline, "_write_exclusive_file", side_effect=fail_second
        ):
            with self.assertRaisesRegex(OSError, "injected"):
                self.run_execution()
        self.assertFalse((self.root / ".pipeline" / "work" / "narration.wav").exists())
        self.assertFalse((self.root / ".pipeline" / "work" / "narration-provenance.json").exists())
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual([], manifest["stages"]["03-tts-narration"]["artifacts"])

    def test_config_change_during_worker_invalidates_both_stages(self):
        fake = self.fake_worker()

        def mutate_config(*args, **kwargs):
            result = fake(*args, **kwargs)
            self.config["profile"]["width"] = 720
            self.config["profile"]["height"] = 1280
            self.write_config()
            return result

        with mock.patch.object(subprocess, "run", side_effect=mutate_config):
            with self.assertRaisesRegex(ValueError, "pipeline config changed"):
                self.run_execution()
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual([], manifest["stages"]["01-validate-project"]["artifacts"])
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])

    def test_failed_run_recovery_requires_new_outputs_and_never_resumes(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["worker"], 300)):
            with self.assertRaises(ValueError):
                self.run_execution()
        with mock.patch.object(subprocess, "run", side_effect=self.fake_worker()):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.run_execution()
        for stage in self.config["stages"]:
            for output in stage["outputs"]:
                output["path"] = output["path"].replace(".pipeline/work/", ".pipeline/work-retry/")
        self.config["workspace"]["intermediate_dir"] = ".pipeline/work-retry"
        self.write_config()
        with mock.patch.object(subprocess, "run", side_effect=self.fake_worker()):
            result = self.run_execution()
        recovered_manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual("completed", recovered_manifest["stages"]["03-tts-narration"]["status"])

    def test_run_productive_tts_cli_does_not_expose_script(self):
        output = io.StringIO()
        with mock.patch.object(subprocess, "run", side_effect=self.fake_worker()), contextlib.redirect_stdout(output):
            result = pipeline.main([
                "run-productive-tts", str(self.config_path),
                "--python", self.args()["python_path"],
                "--model", str(self.model_path.resolve()),
                "--voices", str(self.voices_path.resolve()),
            ])
        self.assertEqual(0, result)
        self.assertNotIn("A test voiceover script.", output.getvalue())
        self.assertIn("manifest_path", json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
