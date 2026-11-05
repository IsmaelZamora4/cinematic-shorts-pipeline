import contextlib
import hashlib
import io
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_pipeline import load_pipeline, make_config, make_tts_config


ROOT = Path(__file__).resolve().parents[1]
pipeline = load_pipeline()


def parse_riff_chunks(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AssertionError("not a RIFF/WAVE file")
    if struct.unpack_from("<I", data, 4)[0] != len(data) - 8:
        raise AssertionError("RIFF size does not match file size")
    chunks = {}
    offset = 12
    while offset < len(data):
        chunk_id = data[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        end = start + chunk_size
        if end > len(data):
            raise AssertionError(f"truncated {chunk_id!r} chunk")
        chunks.setdefault(chunk_id, []).append(data[start:end])
        offset = end + (chunk_size & 1)
    if offset != len(data):
        raise AssertionError("invalid RIFF chunk padding")
    return chunks


class TtsSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config = make_tts_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def simulate(self, backend="mock"):
        return pipeline.simulate_tts(self.config_path, backend=backend)

    def test_generates_valid_one_second_silence_for_all_contract_formats(self):
        cases = (
            ("pcm_s16le", 16000, 1, 1),
            ("pcm_s24le", 44100, 2, 1),
            ("pcm_f32le", 48000, 2, 3),
        )
        for sample_format, sample_rate, channels, wave_format in cases:
            with self.subTest(sample_format=sample_format, channels=channels):
                tts = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")["tts"]
                tts["audio"].update(
                    sample_format=sample_format,
                    sample_rate_hz=sample_rate,
                    channels=channels,
                )
                self.write_config()
                result = self.simulate()
                chunks = parse_riff_chunks(result["wav_bytes"])
                fmt = struct.unpack("<HHIIHH", chunks[b"fmt "][0])
                _, actual_channels, actual_rate, byte_rate, block_align, bits = fmt
                bytes_per_sample = {"pcm_s16le": 2, "pcm_s24le": 3, "pcm_f32le": 4}[sample_format]
                frames = sample_rate
                self.assertEqual(wave_format, fmt[0])
                self.assertEqual(channels, actual_channels)
                self.assertEqual(sample_rate, actual_rate)
                self.assertEqual(channels * bytes_per_sample, block_align)
                self.assertEqual(sample_rate * block_align, byte_rate)
                self.assertEqual(bytes_per_sample * 8, bits)
                self.assertEqual(frames * block_align, len(chunks[b"data"][0]))
                self.assertEqual(bytes(frames * block_align), chunks[b"data"][0])
                self.assertEqual(1000, result["provenance"]["duration_ms"])
                self.assertEqual(frames, result["provenance"]["frames"])
                self.assertLessEqual(len(result["wav_bytes"]), pipeline.MAX_MOCK_WAV_BYTES)
                self.assertEqual(len(result["wav_bytes"]), result["summary"]["wav_size_bytes"])
                self.assertEqual(len(result["wav_bytes"]) - 8, struct.unpack_from("<I", result["wav_bytes"], 4)[0])
                self.assertIn(b"SIMULATED MOCK SILENCE; NOT SPEECH", chunks[b"LIST"][0])
                if sample_format == "pcm_f32le":
                    self.assertEqual(struct.pack("<I", frames), chunks[b"fact"][0])
                else:
                    import wave
                    with wave.open(io.BytesIO(result["wav_bytes"]), "rb") as wav_file:
                        self.assertEqual((channels, bytes_per_sample, sample_rate, frames), wav_file.getparams()[:4])

    def test_provenance_hashes_the_exact_script_and_wav_bytes(self):
        script_bytes = b"\xef\xbb\xbfA private script with caf\xc3\xa9.\n"
        (self.root / "voiceover-script.txt").write_bytes(script_bytes)
        result = self.simulate()
        provenance = result["provenance"]
        self.assertEqual(hashlib.sha256(script_bytes).hexdigest(), provenance["script_sha256"])
        self.assertEqual(hashlib.sha256(result["wav_bytes"]).hexdigest(), provenance["wav_sha256"])
        self.assertIsNone(provenance["provider_executed"])
        self.assertEqual("piper", provenance["provider_requested"])
        self.assertEqual("mock", provenance["backend"])
        self.assertTrue(provenance["simulation"])
        self.assertEqual("pcm_s16le", provenance["audio"]["sample_format"])
        self.assertNotIn("script", provenance)
        self.assertNotIn("text", provenance)

    def test_rejects_unknown_backend_and_never_accepts_mock_as_provider(self):
        with self.assertRaisesRegex(ValueError, "unsupported simulation backend"):
            self.simulate("piper")
        stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        stage["tts"]["provider"] = "mock"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "provider is unsupported"):
            self.simulate()

    def test_rejects_invalid_dependency_or_script(self):
        stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        stage["depends_on"] = ["02-blender-scenes"]
        self.write_config()
        with self.assertRaisesRegex(ValueError, "must directly depend on 01-validate-project"):
            self.simulate()

        stage["depends_on"] = ["01-validate-project"]
        self.write_config()
        script = self.root / "voiceover-script.txt"
        for content, message in ((b"", "must not be empty"), (b"\xff", "valid UTF-8")):
            with self.subTest(message=message):
                script.write_bytes(content)
                with self.assertRaisesRegex(ValueError, message):
                    self.simulate()

    def test_rejects_invalid_storyboard_and_malformed_pipeline_json(self):
        storyboard_path = self.root / "storyboard.json"
        storyboard_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "storyboard"):
            self.simulate()

        storyboard_path.write_text(
            (ROOT / "templates" / "storyboard.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.config_path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pipeline validation failed"):
            self.simulate()

    def test_enforces_script_limit_on_bytes_and_accepts_exact_limit(self):
        script = self.root / "voiceover-script.txt"
        script.write_bytes(b"x" * pipeline.MAX_VOICEOVER_SCRIPT_BYTES)
        result = self.simulate()
        self.assertEqual(
            hashlib.sha256(script.read_bytes()).hexdigest(),
            result["provenance"]["script_sha256"],
        )
        script.write_bytes(b"x" * (pipeline.MAX_VOICEOVER_SCRIPT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "exceeds maximum size"):
            self.simulate()

    def test_rejects_backend_result_over_wav_size_limit(self):
        with mock.patch.dict(
            pipeline.SIMULATION_BACKENDS,
            {"mock": lambda _audio: b"x" * (pipeline.MAX_MOCK_WAV_BYTES + 1)},
        ):
            with self.assertRaisesRegex(ValueError, "exceeds maximum size"):
                self.simulate()

    def test_cli_prints_json_summary_without_script_content_or_writing_files(self):
        private_script = "DO NOT PRINT THIS PRIVATE SCRIPT"
        (self.root / "voiceover-script.txt").write_text(private_script, encoding="utf-8")
        before = {
            str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
            for path in self.root.rglob("*")
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                0,
                pipeline.main(["simulate-tts", str(self.config_path), "--backend", "mock"]),
            )
        summary = json.loads(output.getvalue())
        after = {
            str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
            for path in self.root.rglob("*")
        }
        self.assertEqual(before, after)
        self.assertTrue(summary["simulation"])
        self.assertEqual("mock", summary["backend"])
        self.assertEqual("piper", summary["provider_requested"])
        self.assertNotIn(private_script, output.getvalue())
        self.assertNotIn("wav_bytes", summary)

    def test_pipeline_versions_and_production_adapter_states_remain_unchanged(self):
        pending_manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        manifest_before = json.dumps(pending_manifest, sort_keys=True)
        result = self.simulate()
        self.assertEqual(1, self.config["pipeline_version"])
        self.assertEqual(1, pending_manifest["manifest_version"])
        self.assertEqual(manifest_before, json.dumps(pending_manifest, sort_keys=True))
        stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        tool = next(tool for tool in self.config["tools"] if tool["id"] == "piper")
        self.assertEqual("pending", stage["adapter_status"])
        self.assertEqual("pending", tool["adapter_status"])
        self.assertTrue(result["provenance"]["simulation"])

    def test_manifest_validator_rejects_mock_as_a_completed_production_stage(self):
        manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        self.simulate()
        entry = manifest["stages"]["03-tts-narration"]
        entry.update(
            status="completed",
            attempts=1,
            stage_fingerprint="0" * 64,
            tool_versions={"piper": "mock"},
            artifacts=[
                {"id": "narration", "path": ".pipeline/work/narration.wav", "sha256": "0" * 64},
                {
                    "id": "narration-provenance",
                    "path": ".pipeline/work/narration-provenance.json",
                    "sha256": "0" * 64,
                },
            ],
        )
        errors, _ = pipeline.validate_manifest(
            manifest, self.config, self.config_path, self.root / "run-manifest.json"
        )
        self.assertTrue(
            any("03-tts-narration cannot be completed while its adapter is pending" in error for error in errors),
            errors,
        )

    def test_legacy_pipeline_remains_valid_but_cannot_be_simulated_as_generic_tts(self):
        legacy = make_config(self.root)
        (self.root / "legacy.bin").write_bytes(b"\xfflegacy binary")
        legacy["project"]["resources"] = [
            {"id": "voiceover-script", "path": "legacy.bin", "required": False}
        ]
        legacy_path = self.root / "legacy-pipeline.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        _, errors, _, _ = pipeline.load_and_validate_config(legacy_path)
        self.assertEqual([], errors)
        with self.assertRaisesRegex(ValueError, "requires 03-tts-narration"):
            pipeline.simulate_tts(legacy_path, backend="mock")


if __name__ == "__main__":
    unittest.main()
