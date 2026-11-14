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
from test_productive_scenes_profile import make_combined_config, make_remotion_config
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

    @staticmethod
    def remotion_runtime_identity():
        return {
            "node_version": "v24.16.0", "remotion_version": "4.0.540", "ffprobe_version": "9.0.2",
            "node_sha256": "a" * 64, "remotion_lock_sha256": "b" * 64,
            "worker_sha256": "c" * 64, "ffprobe_sha256": "d" * 64,
        }

    @staticmethod
    def fake_ffprobe_json(size_bytes):
        packets = [{"stream_index": 0, "pts": frame, "duration": 1} for frame in range(24)]
        for index in range(96):
            packet = {"stream_index": 1, "pts": (index - 1) * 1024, "duration": 1024}
            if index == 0:
                packet["side_data_list"] = [{"side_data_type": "Skip Samples", "skip_samples": 1024, "discard_padding": 0}]
            elif index == 95:
                packet["side_data_list"] = [{"side_data_type": "Skip Samples", "skip_samples": 0, "discard_padding": 1280}]
            packets.append(packet)
        return {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "size": str(size_bytes), "duration": "2.02"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                 "width": 540, "height": 960, "r_frame_rate": "12/1", "avg_frame_rate": "12/1",
                 "nb_read_frames": "24", "time_base": "1/12", "duration_ts": 24, "start_time": "0"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "profile": "LC",
                 "sample_rate": "48000", "channels": 1, "time_base": "1/48000",
                 "duration_ts": 96000, "start_time": "0"},
            ],
            "packets": packets,
        }

    def configure_remotion(self):
        self.config = make_remotion_config(self.root)
        self.wav_bytes = make_wav(samples=[1200] * 43_200)
        storyboard_path = self.root / self.config["project"]["storyboard"]
        storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
        storyboard["narrative_timing"] = {
            "clock_source": "recording",
            "anchors": [{"id": "beat-1", "scene_id": "shot-01", "kind": "beat",
                         "start_ms": 0, "end_ms": 500, "basis": "measured"}],
        }
        storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
        self.write_config()

    def mock_remotion_process(self, command, cwd, *, timeout_seconds, max_output_bytes=0):
        if str(command[0]).lower().endswith(("node.exe", "node")):
            request_path = Path(command[2])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            mp4 = b"\x00\x00\x00\x18ftypisom" + bytes(99_988)
            output = request_path.parent / "composite.mp4"
            output.write_bytes(mp4)
            response = {
                "record_type": "remotion-worker-result", "response_version": 1,
                "run_id": request["run_id"], "success": True, "frames_rendered": 24,
                "output_path": "composite.mp4", "output_size_bytes": len(mp4),
                "output_sha256": hashlib.sha256(mp4).hexdigest(),
            }
            (request_path.parent / "response.json").write_text(json.dumps(response), encoding="utf-8")
            return b"", b""
        return json.dumps(self.fake_ffprobe_json(100_000)).encode(), b""

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
            with wav_path.open("ab") as stream:
                stream.write(b"tamper")
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

    def test_combined_checkpoint_does_not_use_unbounded_wav_read(self):
        wav_path = self.root / ".pipeline/work/narration.wav"
        original_read_bytes = Path.read_bytes

        def reject_unbounded_wav_read(path):
            if path == wav_path:
                raise AssertionError("unbounded WAV read")
            return original_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", reject_unbounded_wav_read):
            result = self.run_combined()

        self.assertTrue(Path(result["manifest_path"]).is_file())

    def test_combined_checkpoint_does_not_use_unbounded_provenance_read(self):
        provenance_path = self.root / ".pipeline/work/narration-provenance.json"
        original_read_text = Path.read_text

        def reject_unbounded_provenance_read(path, *args, **kwargs):
            if path == provenance_path:
                raise AssertionError("unbounded provenance read")
            return original_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", reject_unbounded_provenance_read):
            result = self.run_combined()

        self.assertTrue(Path(result["manifest_path"]).is_file())

    def test_remotion_stage_completes_with_mock_worker_and_probe_in_same_manifest(self):
        self.configure_remotion()
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        identity = self.remotion_runtime_identity()
        node = self.root / ("node.exe" if os.name == "nt" else "node")
        ffprobe = self.root / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        node.write_bytes(b"synthetic Node"); ffprobe.write_bytes(b"synthetic ffprobe")
        if os.name != "nt":
            node.chmod(node.stat().st_mode | stat.S_IXUSR)
            ffprobe.chmod(ffprobe.stat().st_mode | stat.S_IXUSR)
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_verify_remotion_installation"), \
             mock.patch.object(pipeline, "_preflight_remotion_runtime", return_value=identity), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=self.fake_blender), \
             mock.patch.object(pipeline, "_run_remotion_process", side_effect=self.mock_remotion_process) as process:
            result = pipeline.run_productive_composition(
                self.config_path, python_path=self.python, model_path=self.model,
                voices_path=self.voices, blender_path=self.blender, node_path=node, ffprobe_path=ffprobe,
            )
            errors, warnings = pipeline.validate_manifest_file(self.config_path, result["manifest_path"])
            self.assertEqual([], errors)
            self.assertEqual([], warnings)
            self.assertEqual(2, process.call_count)
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(result["run_id"], manifest["run_id"])
        self.assertEqual("completed", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertEqual(24, result["composition"]["ffprobe"]["video"]["frames"])
        self.assertTrue((self.root / ".pipeline/work/composite.mp4").is_file())
        self.assertTrue((self.root / ".pipeline/work/composition-provenance.json").is_file())

    def test_remotion_worker_failure_records_failed_stage_without_publication(self):
        self.configure_remotion()
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        identity = self.remotion_runtime_identity()
        node = self.root / "node.exe"; ffprobe = self.root / "ffprobe.exe"
        node.write_bytes(b"node"); ffprobe.write_bytes(b"ffprobe")
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_verify_remotion_installation"), \
             mock.patch.object(pipeline, "_preflight_remotion_runtime", return_value=identity), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=self.fake_blender), \
             mock.patch.object(pipeline, "_run_remotion_process", side_effect=ValueError("synthetic worker failure")):
            with self.assertRaisesRegex(ValueError, "synthetic worker failure"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender, node_path=node, ffprobe_path=ffprobe,
                )
        manifest = json.loads(next(self.root.glob(".pipeline/runs/run-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual("failed", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertFalse((self.root / ".pipeline/work/composite.mp4").exists())
        self.assertEqual([], pipeline.validate_manifest_file(self.config_path, next(self.root.glob(".pipeline/runs/run-*/manifest.json")))[0])

    def test_invalid_ffprobe_response_fails_stage_04_without_publishing(self):
        self.configure_remotion()
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        identity = self.remotion_runtime_identity()
        node = self.root / "node.exe"; ffprobe = self.root / "ffprobe.exe"
        node.write_bytes(b"node"); ffprobe.write_bytes(b"ffprobe")

        def invalid_probe(command, cwd, *, timeout_seconds, max_output_bytes=0):
            if max_output_bytes:
                return b"{}", b""
            return self.mock_remotion_process(command, cwd, timeout_seconds=timeout_seconds,
                                              max_output_bytes=max_output_bytes)

        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_verify_remotion_installation"), \
             mock.patch.object(pipeline, "_preflight_remotion_runtime", return_value=identity), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=self.fake_blender), \
             mock.patch.object(pipeline, "_run_remotion_process", side_effect=invalid_probe):
            with self.assertRaisesRegex(ValueError, "container metadata"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender, node_path=node, ffprobe_path=ffprobe,
                )

        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertEqual([], manifest["stages"]["04-remotion-compose"]["artifacts"])
        self.assertFalse((self.root / ".pipeline/work/composite.mp4").exists())
        self.assertFalse((self.root / ".pipeline/work/composition-provenance.json").exists())
        self.assertEqual([], pipeline.validate_manifest_file(self.config_path, manifest_path)[0])

    def test_existing_stage_04_destination_is_preserved_and_run_is_not_created(self):
        self.configure_remotion()
        output = self.root / ".pipeline/work/composite.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preexisting artifact")
        with self.assertRaisesRegex(ValueError, "already exists|collision|output"):
            pipeline.run_productive_composition(
                self.config_path, python_path=self.python, model_path=self.model,
                voices_path=self.voices, blender_path=self.blender,
                node_path=self.blender, ffprobe_path=self.blender,
            )
        self.assertEqual(b"preexisting artifact", output.read_bytes())
        self.assertFalse((self.root / ".pipeline/runs").exists())

    def test_runtime_change_after_worker_fails_stage_04_without_publication(self):
        self.configure_remotion()
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        identity = self.remotion_runtime_identity()
        changed = {**identity, "worker_sha256": "e" * 64}
        node = self.root / "node.exe"; ffprobe = self.root / "ffprobe.exe"
        node.write_bytes(b"node"); ffprobe.write_bytes(b"ffprobe")
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_verify_remotion_installation"), \
             mock.patch.object(pipeline, "_preflight_remotion_runtime", side_effect=[identity, identity, changed]), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=self.fake_blender), \
             mock.patch.object(pipeline, "_run_remotion_process", side_effect=self.mock_remotion_process):
            with self.assertRaisesRegex(ValueError, "changed during composition"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender, node_path=node, ffprobe_path=ffprobe,
                )

        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertEqual([], manifest["stages"]["04-remotion-compose"]["artifacts"])
        self.assertFalse((self.root / ".pipeline/work/composite.mp4").exists())
        self.assertFalse((self.root / ".pipeline/work/composition-provenance.json").exists())
        self.assertEqual([], pipeline.validate_manifest_file(self.config_path, manifest_path)[0])

    def test_partial_stage_04_publication_removes_only_the_owned_first_output(self):
        self.configure_remotion()
        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        identity = self.remotion_runtime_identity()
        node = self.root / "node.exe"; ffprobe = self.root / "ffprobe.exe"
        node.write_bytes(b"node"); ffprobe.write_bytes(b"ffprobe")
        original_write = pipeline._write_exclusive_file
        provenance_output = self.root / ".pipeline/work/composition-provenance.json"

        def fail_second_publication(path, payload, owned):
            if Path(path) == provenance_output:
                raise OSError("synthetic partial publication failure")
            return original_write(path, payload, owned)

        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_verify_remotion_installation"), \
             mock.patch.object(pipeline, "_preflight_remotion_runtime", return_value=identity), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=self.fake_blender), \
             mock.patch.object(pipeline, "_run_remotion_process", side_effect=self.mock_remotion_process), \
             mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_second_publication):
            with self.assertRaisesRegex(OSError, "partial publication"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender, node_path=node, ffprobe_path=ffprobe,
                )

        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", manifest["stages"]["04-remotion-compose"]["status"])
        self.assertEqual([], manifest["stages"]["04-remotion-compose"]["artifacts"])
        self.assertFalse((self.root / ".pipeline/work/composite.mp4").exists())
        self.assertFalse(provenance_output.exists())
        self.assertEqual([], pipeline.validate_manifest_file(self.config_path, manifest_path)[0])

    def test_oversized_wav_before_blender_fails_without_starting_worker(self):
        original_verify = pipeline._verify_combined_kokoro_checkpoint
        original_read_bytes = Path.read_bytes
        blender_calls = []
        changed = False

        def grow_before_checkpoint(*args):
            nonlocal changed
            wav_path = Path(args[5])
            if not changed:
                changed = True
                with wav_path.open("ab") as stream:
                    stream.write(b"X" * (pipeline.MAX_KOKORO_WAV_BYTES + 1))
            return original_verify(*args)

        def reject_unbounded_wav_read(path):
            if path.name == "narration.wav":
                raise AssertionError("unbounded WAV read")
            return original_read_bytes(path)

        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_verify_combined_kokoro_checkpoint", side_effect=grow_before_checkpoint), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=lambda *args: blender_calls.append(args)), \
             mock.patch.object(Path, "read_bytes", reject_unbounded_wav_read):
            with self.assertRaisesRegex(ValueError, "maximum size"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )

        self.assertEqual([], blender_calls)
        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("pending", manifest["stages"]["02-blender-scenes"]["status"])

    def test_oversized_provenance_after_blender_fails_without_completing_stage_02(self):
        original_read_text = Path.read_text

        def mutate_provenance_during_blender(config_path, runtime):
            result = self.fake_blender(config_path, runtime)
            provenance_path = self.root / ".pipeline/work/narration-provenance.json"
            with provenance_path.open("ab") as stream:
                stream.write(b"X" * (pipeline.MAX_KOKORO_PROVENANCE_BYTES + 1))
            return result

        def reject_unbounded_provenance_read(path, *args, **kwargs):
            if path.name == "narration-provenance.json":
                raise AssertionError("unbounded provenance read")
            return original_read_text(path, *args, **kwargs)

        runtime = {"path": str(self.blender.resolve()), "sha256": hashlib.sha256(self.blender.read_bytes()).hexdigest(),
                   "version": (5, 2, 3), "version_string": "5.2.3"}
        with mock.patch.object(pipeline, "_preflight_blender_runtime", return_value=runtime), \
             mock.patch.object(pipeline, "_productive_worker", side_effect=self.fake_kokoro), \
             mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=mutate_provenance_during_blender), \
             mock.patch.object(Path, "read_text", reject_unbounded_provenance_read):
            with self.assertRaisesRegex(ValueError, "changed during Blender"):
                pipeline.run_productive_composition(
                    self.config_path, python_path=self.python, model_path=self.model,
                    voices_path=self.voices, blender_path=self.blender,
                )

        manifest_path = next(self.root.glob(".pipeline/runs/run-*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["stages"]["01-validate-project"]["status"])
        self.assertEqual("failed", manifest["stages"]["03-tts-narration"]["status"])
        self.assertEqual("failed", manifest["stages"]["02-blender-scenes"]["status"])
        self.assertEqual([], manifest["stages"]["02-blender-scenes"]["artifacts"])
        self.assertFalse((self.root / ".pipeline/work/scenes.zip").exists())

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
