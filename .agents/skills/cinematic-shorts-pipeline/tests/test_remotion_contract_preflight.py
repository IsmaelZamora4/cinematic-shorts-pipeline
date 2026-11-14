import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_pipeline import ROOT, load_pipeline
from test_productive_scenes_profile import make_combined_config, make_scene_storyboard
from test_tts_kokoro import make_wav


pipeline = load_pipeline()


class RemotionContractPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_combined_config(self.root)
        self.config["profile"].update(width=540, height=960, fps=12, format="mp4")
        self.storyboard = make_scene_storyboard()
        self.storyboard["voiceover"]["duration_ms"] = 1800
        self.storyboard["narrative_timing"] = {
            "clock_source": "recording",
            "anchors": [{"id": "beat-1", "scene_id": "shot-01", "kind": "beat",
                         "start_ms": 0, "end_ms": 500, "basis": "measured"}],
        }
        self.wav = make_wav(samples=[1200] * 43_200)
        self.provenance = {
            "provenance_version": 1,
            "record_type": "productive-local-kokoro-tts",
            "run_id": "run-test",
            "project_id": "test-project",
            "dag_stage_id": "03-tts-narration",
            "provider": "kokoro-onnx",
            "audio": {"container": "wav", "sample_rate_hz": 24000, "channels": 1,
                      "sample_format": "pcm_s16le"},
            "hashes": {
                "wav_sha256": hashlib.sha256(self.wav).hexdigest(),
                "validated_plan_sha256": "a" * 64,
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def timeline(self):
        return pipeline._validate_remotion_timeline(
            self.config, self.storyboard, self.wav, self.provenance,
            run_id="run-test", project_id="test-project",
            validated_plan_sha256="a" * 64,
        )

    def test_audio_under_two_seconds_declares_final_silence_without_changing_wav(self):
        original = self.wav
        result = self.timeline()
        self.assertEqual(43_200, result["source_samples"])
        self.assertEqual(4_800, result["silence_samples"])
        self.assertEqual(original, self.wav)

    def test_exactly_48000_samples_has_no_padding(self):
        self.wav = make_wav(samples=[1200] * 48_000)
        self.storyboard["voiceover"]["duration_ms"] = 2000
        self.provenance["hashes"]["wav_sha256"] = hashlib.sha256(self.wav).hexdigest()
        result = self.timeline()
        self.assertEqual(48_000, result["source_samples"])
        self.assertEqual(0, result["silence_samples"])

    def test_audio_over_48000_samples_is_rejected(self):
        self.wav = make_wav(samples=[1200] * 48_001)
        self.storyboard["voiceover"]["duration_ms"] = 2000
        self.provenance["hashes"]["wav_sha256"] = hashlib.sha256(self.wav).hexdigest()
        with self.assertRaisesRegex(ValueError, "48,000"):
            self.timeline()

    def test_contract_rejects_nonmatching_profile_or_storyboard_timing(self):
        self.config["profile"]["fps"] = 24
        with self.assertRaisesRegex(ValueError, "profile"):
            self.timeline()
        self.config["profile"]["fps"] = 12
        self.storyboard["total_frames"] = 23
        with self.assertRaisesRegex(ValueError, "storyboard"):
            self.timeline()

    def test_valid_captions_quantize_outward_using_integer_arithmetic(self):
        self.storyboard["captions"] = [
            {"text": "A", "start_ms": 1, "end_ms": 70},
            {"text": "B", "start_ms": 100, "end_ms": 180},
        ]
        result = self.timeline()
        self.assertEqual([
            {"caption_index": 0, "start_frame": 0, "end_frame_exclusive": 1,
             "start_ms": 1, "end_ms": 70, "text": "A"},
            {"caption_index": 1, "start_frame": 1, "end_frame_exclusive": 3,
             "start_ms": 100, "end_ms": 180, "text": "B"},
        ], result["captions"])

    def test_caption_less_than_one_frame_occupies_one_frame(self):
        self.storyboard["captions"] = [{"text": "A", "start_ms": 101, "end_ms": 102}]
        cue = self.timeline()["captions"][0]
        self.assertEqual((1, 2), (cue["start_frame"], cue["end_frame_exclusive"]))

    def test_consecutive_captions_colliding_after_quantization_are_rejected(self):
        self.storyboard["captions"] = [
            {"text": "A", "start_ms": 1, "end_ms": 20},
            {"text": "B", "start_ms": 21, "end_ms": 60},
        ]
        with self.assertRaisesRegex(ValueError, "quantization collision"):
            self.timeline()

    def test_caption_outside_two_second_timeline_is_rejected(self):
        self.storyboard["captions"] = [{"text": "A", "start_ms": 1900, "end_ms": 2001}]
        with self.assertRaisesRegex(ValueError, "storyboard|timeline"):
            self.timeline()

    def test_caption_overlap_requires_existing_exception(self):
        self.storyboard["captions"] = [
            {"text": "A", "start_ms": 100, "end_ms": 300},
            {"text": "B", "start_ms": 200, "end_ms": 400},
        ]
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.timeline()
        self.storyboard["captions"][1]["overlap_exception"] = "Intentional dialogue overlap"
        self.assertEqual(2, len(self.timeline()["captions"]))

    def test_real_audio_requires_recording_clock_and_reviewed_anchors(self):
        self.storyboard["captions"] = [{"text": "A", "start_ms": 0, "end_ms": 500}]
        self.storyboard["narrative_timing"] = {
            "clock_source": "estimated",
            "anchors": [{"id": "a", "scene_id": "shot-01", "kind": "beat",
                         "start_ms": 0, "end_ms": 500, "basis": "estimated"}],
        }
        with self.assertRaisesRegex(ValueError, "recording"):
            self.timeline()
        self.storyboard["narrative_timing"].update(clock_source="recording")
        self.storyboard["narrative_timing"]["anchors"][0]["basis"] = "measured"
        self.assertTrue(self.timeline()["captions"])

    def test_estimated_anchor_is_rejected_for_recorded_audio(self):
        self.storyboard["narrative_timing"] = {
            "clock_source": "recording",
            "anchors": [{"id": "a", "scene_id": "shot-01", "kind": "pause",
                         "start_ms": 1800, "end_ms": 2100, "basis": "measured"}],
        }
        with self.assertRaisesRegex(ValueError, "storyboard|duration|timeline"):
            self.timeline()

    def test_provenance_must_bind_same_run_project_audio_and_plan(self):
        self.provenance["run_id"] = "another-run"
        with self.assertRaisesRegex(ValueError, "run, project"):
            self.timeline()

    def test_stage_inputs_bind_scene_and_narration_provenance_to_one_run(self):
        scene_provenance = {
            "provenance_version": 1,
            "record_type": "productive-local-blender-scenes",
            "run_id": "run-test",
            "project_id": "test-project",
            "stage_id": "02-blender-scenes",
            "inputs": {
                "validated_plan_sha256": "a" * 64,
                "stage_01_fingerprint": "b" * 64,
                "stage_02_fingerprint": "c" * 64,
                "input_hashes": [
                    {"id": "validated-plan", "sha256": "a" * 64},
                    {"id": "storyboard", "sha256": "e" * 64},
                ],
            },
            "render": {
                "width": 540, "height": 960, "fps_num": 12, "fps_den": 1,
                "source_start_frame": 0, "source_end_frame_exclusive": 24,
                "image_format": "PNG_RGB8_SRGB",
            },
        }
        context = pipeline._validate_remotion_input_context(
            "run-test", "test-project", "a" * 64, "b" * 64, "c" * 64,
            "d" * 64, "e" * 64,
            self.wav, scene_provenance, self.provenance,
        )
        self.assertEqual([
            "validated-plan", "storyboard", "scene-package", "narration", "narration-provenance",
        ], context["input_ids"])
        scene_provenance["run_id"] = "other-run"
        with self.assertRaisesRegex(ValueError, "scene-package provenance"):
            pipeline._validate_remotion_input_context(
                "run-test", "test-project", "a" * 64, "b" * 64, "c" * 64,
                "d" * 64, "e" * 64,
                self.wav, scene_provenance, self.provenance,
            )

    def test_worker_request_has_bounded_strict_schema(self):
        request = {
            "record_type": "remotion-worker-request",
            "request_version": 1,
            "project_id": "test-project",
            "run_id": "run-test",
            "width": 540,
            "height": 960,
            "fps": 12,
            "frames": 24,
            "source_samples": 43_200,
            "silence_samples": 4_800,
            "inputs": {
                "validated-plan": "validated-plan.json", "storyboard": "storyboard.json",
                "scene-package": "scene-package.zip", "narration": "narration.wav",
                "narration-provenance": "narration-provenance.json",
            },
            "captions": [{"caption_index": 0, "start_frame": 0, "end_frame_exclusive": 1,
                          "start_ms": 1, "end_ms": 70, "text": "Texto intacto"}],
            "anchors": [],
        }
        payload = json.dumps(request).encode()
        self.assertEqual(request, pipeline._validate_remotion_worker_request(payload))
        with self.assertRaisesRegex(ValueError, "unknown|fields"):
            pipeline._validate_remotion_worker_request(json.dumps({**request, "command": "node evil.js"}).encode())
        with self.assertRaisesRegex(ValueError, "maximum"):
            pipeline._validate_remotion_worker_request(b" " * (pipeline.REMOTION_MAX_WORKER_REQUEST_BYTES + 1))

    def test_worker_request_rejects_untrusted_anchor_fields(self):
        request = {
            "record_type": "remotion-worker-request", "request_version": 1,
            "project_id": "test-project", "run_id": "run-test", "width": 540,
            "height": 960, "fps": 12, "frames": 24, "source_samples": 43_200,
            "silence_samples": 4_800,
            "inputs": {
                "validated-plan": "validated-plan.json", "storyboard": "storyboard.json",
                "scene-package": "scene-package.zip", "narration": "narration.wav",
                "narration-provenance": "narration-provenance.json",
            },
            "captions": [],
            "anchors": [{"id": "a", "scene_id": "shot-01", "kind": "beat",
                         "start_ms": 0, "end_ms": 500, "basis": "measured", "code": "evil"}],
        }
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            pipeline._validate_remotion_worker_request(json.dumps(request).encode())

    def test_worker_request_rejects_caption_times_outside_or_inconsistent_with_frames(self):
        request = {
            "record_type": "remotion-worker-request", "request_version": 1,
            "project_id": "test-project", "run_id": "run-test", "width": 540,
            "height": 960, "fps": 12, "frames": 24, "source_samples": 43_200,
            "silence_samples": 4_800,
            "inputs": {
                "validated-plan": "validated-plan.json", "storyboard": "storyboard.json",
                "scene-package": "scene-package.zip", "narration": "narration.wav",
                "narration-provenance": "narration-provenance.json",
            },
            "captions": [{"caption_index": 0, "start_frame": 0, "end_frame_exclusive": 1,
                          "start_ms": 1, "end_ms": 70, "text": "Texto"}],
            "anchors": [],
        }
        for start_ms, end_ms, start_frame, end_frame in [
            (-1, 70, 0, 1), (1, 2001, 0, 25), (100, 170, 0, 1),
        ]:
            with self.subTest(start_ms=start_ms, end_ms=end_ms):
                caption = {**request["captions"][0], "start_ms": start_ms, "end_ms": end_ms,
                           "start_frame": start_frame, "end_frame_exclusive": end_frame}
                with self.assertRaisesRegex(ValueError, "malformed"):
                    pipeline._validate_remotion_worker_request(
                        json.dumps({**request, "captions": [caption]}).encode()
                    )

    def test_worker_response_rejects_malformed_and_excessive_payloads(self):
        response = {
            "record_type": "remotion-worker-result",
            "response_version": 1,
            "run_id": "run-test",
            "success": True,
            "frames_rendered": 24,
            "output_path": "composite.mp4",
            "output_size_bytes": 10,
            "output_sha256": "b" * 64,
        }
        parsed = pipeline._validate_remotion_worker_response(json.dumps(response).encode(), "run-test")
        self.assertEqual(24, parsed["frames_rendered"])
        with self.assertRaisesRegex(ValueError, "run/output"):
            pipeline._validate_remotion_worker_response(json.dumps({**response, "run_id": "wrong"}).encode(), "run-test")
        with self.assertRaisesRegex(ValueError, "maximum"):
            pipeline._validate_remotion_worker_response(b" " * (pipeline.REMOTION_MAX_WORKER_RESPONSE_BYTES + 1), "run-test")

    def test_runtime_versions_and_hashes_must_match_fixed_contract(self):
        identity = {
            "node_version": "v24.16.0",
            "remotion_version": "4.0.540",
            "ffprobe_version": "9.0.2",
            "node_sha256": "c" * 64,
            "remotion_lock_sha256": "d" * 64,
            "worker_sha256": "e" * 64,
            "ffprobe_sha256": "f" * 64,
        }
        self.assertEqual(identity, pipeline._validate_remotion_runtime_identity(identity))
        with self.assertRaisesRegex(ValueError, "Node"):
            pipeline._validate_remotion_runtime_identity({**identity, "node_version": "v23.1.0"})
        with self.assertRaisesRegex(ValueError, "Remotion"):
            pipeline._validate_remotion_runtime_identity({**identity, "remotion_version": "4.0.539"})
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            pipeline._validate_remotion_runtime_identity({**identity, "worker_sha256": "nope"})

    def test_worker_dependency_lock_is_exact_and_registry_integrity_checked(self):
        worker = ROOT / "workers" / "remotion"
        package_bytes = (worker / "package.json").read_bytes()
        lock_bytes = (worker / "package-lock.json").read_bytes()
        package, lock = pipeline._validate_remotion_dependency_contract(package_bytes, lock_bytes)
        self.assertEqual("4.0.540", package["dependencies"]["remotion"])
        self.assertEqual(3, lock["lockfileVersion"])
        with self.assertRaisesRegex(ValueError, "fixed dependency contract"):
            pipeline._validate_remotion_dependency_contract(
                package_bytes.replace(b'"remotion": "4.0.540"', b'"remotion": "4.0.539"'), lock_bytes,
            )

    def test_preflight_hashes_fixed_runtime_files_and_probes_only_version_flags(self):
        worker = ROOT / "workers" / "remotion"
        runtime = self.root / "runtime"
        runtime.mkdir()
        paths = {}
        for name, payload in {
            "node.exe": b"node runtime", "ffprobe.exe": b"ffprobe runtime", "worker.mjs": b"fixed worker",
        }.items():
            path = runtime / name
            path.write_bytes(payload)
            paths[name] = path
        with mock.patch.object(pipeline, "_run_remotion_version_probe", side_effect=[
            "v24.16.0\n", "ffprobe version 9.0.2-full_build Copyright\n",
        ]) as probe:
            identity = pipeline._preflight_remotion_runtime(
                paths["node.exe"], paths["ffprobe.exe"], worker / "package.json",
                worker / "package-lock.json", paths["worker.mjs"],
            )
        self.assertEqual("v24.16.0", identity["node_version"])
        self.assertEqual("9.0.2", identity["ffprobe_version"])
        self.assertEqual("4.0.540", identity["remotion_version"])
        self.assertEqual([
            mock.call(paths["node.exe"], "--version", (worker / "package.json").parent),
            mock.call(paths["ffprobe.exe"], "-version", (worker / "package.json").parent),
        ], probe.call_args_list)

    def test_absolute_runtime_path_validator_rejects_relative_and_links(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            pipeline._validate_remotion_runtime_path("node.exe", "Node")
        runtime = self.root / "node.exe"
        runtime.write_bytes(b"runtime")
        self.assertEqual(runtime.resolve(), pipeline._validate_remotion_runtime_path(runtime, "Node"))
        with mock.patch.object(pipeline, "_inspect_path", side_effect=ValueError("reparse")):
            with self.assertRaisesRegex(ValueError, "reparse"):
                pipeline._validate_remotion_runtime_path(runtime, "Node")

    def test_staging_is_unique_and_rejects_existing_run_path(self):
        first = pipeline._create_remotion_staging(self.root, "run-test")
        second = pipeline._create_remotion_staging(self.root, "run-test")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())


if __name__ == "__main__":
    unittest.main()
