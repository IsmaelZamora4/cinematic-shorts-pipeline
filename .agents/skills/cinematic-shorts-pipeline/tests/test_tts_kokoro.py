import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import socket
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import urllib.request

from test_pipeline import ROOT, load_pipeline, make_tts_config


pipeline = load_pipeline()
WORKER_PATH = ROOT / "scripts" / "tts-kokoro-worker.py"
_worker_spec = importlib.util.spec_from_file_location("tts_kokoro_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(_worker_spec)
_worker_spec.loader.exec_module(worker)


def make_kokoro_config(root):
    config = make_tts_config(root, provider="kokoro")
    stage = next(stage for stage in config["stages"] if stage["id"] == "03-tts-narration")
    stage["tts"].update(voice="em_alex", language="es")
    stage["tts"]["audio"].update(sample_rate_hz=24000, channels=1, sample_format="pcm_s16le")
    return config


def make_wav(samples=None, sample_rate=24000):
    if samples is None:
        samples = [1200, -900] * 120
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return stream.getvalue()


def worker_record(wav_bytes, **overrides):
    audio = pipeline._inspect_kokoro_wav(wav_bytes)
    record = {
        "provider": "kokoro-onnx",
        "onnx_execution_providers": ["CPUExecutionProvider"],
        "voice": "em_alex",
        "language": "es",
        "audio": {
            "container": "wav",
            "sample_rate_hz": 24000,
            "channels": 1,
            "sample_format": "pcm_s16le",
            "frames": audio["frames"],
            "duration_seconds": audio["duration_seconds"],
            "size_bytes": len(wav_bytes),
            "sha256": hashlib.sha256(wav_bytes).hexdigest(),
        },
        "versions": {
            "kokoro-onnx": "0.6.1",
            "onnxruntime": "1.31.1",
            "numpy": "2.5.4",
            "phonemizer": "3.6.0",
            "espeakng-loader": "0.2.4",
            "soundfile": "0.14.0",
        },
        "python_version": "3.12.10",
        "timing_seconds": {"model_initialization": 1.0, "inference": 2.0},
        "peak_working_set_bytes": 123456789,
    }
    record.update(overrides)
    return record


class FakeSession:
    def __init__(self, providers=("CPUExecutionProvider",)):
        self.providers = list(providers)

    def get_providers(self):
        return self.providers


class FakeKokoro:
    providers = ("CPUExecutionProvider",)
    samples = [0.25, -0.2] * 120
    sample_rate = 24000
    calls = []

    def __init__(self, model_path, voices_path):
        self.model_path = model_path
        self.voices_path = voices_path
        self.sess = FakeSession(self.providers)

    def get_voices(self):
        return ["em_alex"]

    def create(self, text, *, voice, lang, speed):
        type(self).calls.append((text, voice, lang, speed))
        return self.samples, self.sample_rate


class FakeSoundfile:
    @staticmethod
    def write(file_object, samples, sample_rate, *, format, subtype):
        if (format, subtype) != ("WAV", "PCM_16"):
            raise AssertionError("unexpected writer settings")
        pcm = [max(-32768, min(32767, round(float(value) * 32767))) for value in samples]
        file_object.write(make_wav(pcm, sample_rate))


class FakeRuntime:
    @staticmethod
    def get_available_providers():
        return ["AzureExecutionProvider", "CPUExecutionProvider"]


class TtsKokoroTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config = make_kokoro_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.write_config()
        self.python_path = self.root / ("python.exe" if os.name == "nt" else "python")
        self.python_path.write_bytes(b"trusted test interpreter marker")
        if os.name != "nt":
            self.python_path.chmod(0o700)
        self.model_path = self.root / "model.onnx"
        self.model_path.write_bytes(b"model fixture bytes")
        self.voices_path = self.root / "voices.bin"
        self.voices_path.write_bytes(b"voices fixture bytes")
        FakeKokoro.calls = []
        FakeKokoro.providers = ("CPUExecutionProvider",)
        FakeKokoro.samples = [0.25, -0.2] * 120
        FakeKokoro.sample_rate = 24000

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def run_adapter(self):
        return pipeline.run_tts_kokoro(
            self.config_path,
            python_path=self.python_path,
            model_path=self.model_path,
            voices_path=self.voices_path,
        )

    def patch_worker(self, wav_bytes=None, record=None):
        wav_bytes = make_wav() if wav_bytes is None else wav_bytes
        record = worker_record(wav_bytes) if record is None else record

        def run(command, **kwargs):
            self.assertEqual(str(self.python_path), command[0])
            self.assertEqual(str(WORKER_PATH), command[1])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(pipeline.KOKORO_TIMEOUT_SECONDS, kwargs["timeout"])
            self.assertEqual("CPUExecutionProvider", kwargs["env"]["ONNX_PROVIDER"])
            self.assertEqual((self.root / "voiceover-script.txt").read_bytes(), kwargs["input"])
            self.assertNotIn(b"A test voiceover script.", b" ".join(arg.encode() for arg in command))
            return SimpleNamespace(
                returncode=0,
                stdout=wav_bytes,
                stderr=b"KOKORO_RESULT=" + json.dumps(record).encode("utf-8") + b"\n",
            )

        return mock.patch.object(pipeline.subprocess, "run", side_effect=run)

    def test_worker_uses_fixed_voice_language_cpu_and_binary_wav_writer(self):
        FakeKokoro.calls = []
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": "CPUExecutionProvider"}), \
             mock.patch.object(worker, "_distribution_version", return_value="0.6.1"):
            result = worker.synthesize(
                b"Hola, mundo.", self.model_path, self.voices_path,
                kokoro_class=FakeKokoro, runtime=FakeRuntime, soundfile_module=FakeSoundfile,
            )
        self.assertEqual([("Hola, mundo.", "em_alex", "es", 1.0)], FakeKokoro.calls)
        self.assertEqual(["CPUExecutionProvider"], result["onnx_execution_providers"])
        self.assertEqual("pcm_s16le", result["audio"]["sample_format"])
        self.assertEqual(24000, result["audio"]["sample_rate_hz"])
        self.assertEqual(hashlib.sha256(result["wav_bytes"]).hexdigest(), result["audio"]["sha256"])
        self.assertEqual("kokoro-onnx", result["provider"])

    def test_worker_rejects_invalid_utf8_before_loading_runtime(self):
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": "CPUExecutionProvider"}), \
             mock.patch.object(worker, "_distribution_version", side_effect=AssertionError("runtime loaded")):
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                worker.synthesize(b"\xff", self.model_path, self.voices_path)

    def test_worker_enforces_pilot_script_byte_limit(self):
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": "CPUExecutionProvider"}):
            with self.assertRaisesRegex(ValueError, "8192 UTF-8 bytes"):
                worker.synthesize(b"x" * (worker.MAX_SCRIPT_BYTES + 1), self.model_path, self.voices_path)

    def test_worker_rejects_missing_kokoro_runtime_without_writing_output(self):
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": "CPUExecutionProvider"}), \
             mock.patch.object(worker, "_distribution_version", side_effect=RuntimeError("missing kokoro-onnx")):
            with self.assertRaisesRegex(RuntimeError, "missing kokoro-onnx"):
                worker.synthesize(
                    b"hello", self.model_path, self.voices_path,
                    kokoro_class=FakeKokoro, runtime=FakeRuntime, soundfile_module=FakeSoundfile,
                )

    def test_worker_rejects_wrong_effective_provider_rate_and_non_finite_samples(self):
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": "CPUExecutionProvider"}), \
             mock.patch.object(worker, "_distribution_version", return_value="0.6.1"):
            FakeKokoro.providers = ("CUDAExecutionProvider",)
            with self.assertRaisesRegex(RuntimeError, "effective ONNX"):
                worker.synthesize(b"hello", self.model_path, self.voices_path, kokoro_class=FakeKokoro, runtime=FakeRuntime, soundfile_module=FakeSoundfile)
            FakeKokoro.providers = ("CPUExecutionProvider",)
            FakeKokoro.sample_rate = 22050
            with self.assertRaisesRegex(ValueError, "sample rate"):
                worker.synthesize(b"hello", self.model_path, self.voices_path, kokoro_class=FakeKokoro, runtime=FakeRuntime, soundfile_module=FakeSoundfile)
            FakeKokoro.sample_rate = 24000
            FakeKokoro.samples = [float("nan")]
            with self.assertRaisesRegex(ValueError, "non-finite"):
                worker.synthesize(b"hello", self.model_path, self.voices_path, kokoro_class=FakeKokoro, runtime=FakeRuntime, soundfile_module=FakeSoundfile)
        FakeKokoro.samples = [0.25, -0.2] * 120
        FakeKokoro.sample_rate = 24000
        FakeKokoro.providers = ("CPUExecutionProvider",)

    def test_wav_inspector_rejects_corrupt_silence_wrong_rate_and_severe_clipping(self):
        with self.assertRaisesRegex(ValueError, "complete WAV"):
            pipeline._inspect_kokoro_wav(b"not a wave")
        with self.assertRaisesRegex(ValueError, "mono PCM16 at 24000"):
            pipeline._inspect_kokoro_wav(make_wav(sample_rate=22050))
        with self.assertRaisesRegex(ValueError, "non-silent"):
            pipeline._inspect_kokoro_wav(make_wav([0] * 120))
        with self.assertRaisesRegex(ValueError, "clipping"):
            pipeline._inspect_kokoro_wav(make_wav([32767] * 120))

    def test_rejects_non_kokoro_voice_language_and_audio_before_spawning(self):
        original = json.loads(self.config_path.read_text(encoding="utf-8"))
        for field, value in (("provider", "piper"), ("voice", "em_santa"), ("language", "en-US")):
            with self.subTest(field=field):
                config = json.loads(json.dumps(original))
                stage = next(stage for stage in config["stages"] if stage["id"] == "03-tts-narration")
                stage["tts"][field] = value
                self.config = config
                self.write_config()
                with mock.patch.object(subprocess, "run") as run:
                    with self.assertRaises(ValueError):
                        self.run_adapter()
                run.assert_not_called()
        for field, value in (("sample_rate_hz", 44100), ("channels", 2), ("sample_format", "pcm_s24le")):
            with self.subTest(audio_field=field):
                config = json.loads(json.dumps(original))
                stage = next(stage for stage in config["stages"] if stage["id"] == "03-tts-narration")
                stage["tts"]["audio"][field] = value
                self.config = config
                self.write_config()
                with mock.patch.object(subprocess, "run") as run:
                    with self.assertRaisesRegex(ValueError, "WAV mono 24000 Hz pcm_s16le"):
                        self.run_adapter()
                run.assert_not_called()

    def test_missing_runtime_assets_fail_before_publication_or_process(self):
        with mock.patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "does not exist"):
                pipeline.run_tts_kokoro(
                    self.config_path,
                    python_path=self.root / "missing.exe",
                    model_path=self.model_path,
                    voices_path=self.voices_path,
                )
        run.assert_not_called()
        self.assertFalse((self.root / ".pipeline").exists())

    def test_success_publishes_verified_real_audio_without_changing_dag_or_manifest(self):
        manifest_path = self.root / "run-manifest.json"
        manifest_before = json.dumps(pipeline.new_pending_manifest(self.config, self.config_path), sort_keys=True)
        manifest_path.write_text(manifest_before, encoding="utf-8")
        output_paths = [self.root / ".pipeline" / "work" / name for name in ("narration.wav", "narration-provenance.json")]
        for path in output_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"productive output must remain untouched")
        prior_outputs = [path.read_bytes() for path in output_paths]
        with self.patch_worker():
            result = self.run_adapter()
        run_dir = Path(result["publication_dir"])
        self.assertEqual((self.root / ".pipeline" / "tts-local-runs").resolve(), run_dir.parent.resolve())
        self.assertEqual({"narration.wav", "narration-provenance.json"}, {path.name for path in run_dir.iterdir()})
        wav_path = run_dir / "narration.wav"
        provenance_path = run_dir / "narration-provenance.json"
        wav_bytes = wav_path.read_bytes()
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(wav_bytes).hexdigest(), provenance["hashes"]["wav_sha256"])
        self.assertEqual(hashlib.sha256((self.root / "voiceover-script.txt").read_bytes()).hexdigest(), provenance["hashes"]["script_sha256"])
        self.assertEqual(hashlib.sha256(self.model_path.read_bytes()).hexdigest(), provenance["hashes"]["model_sha256"])
        self.assertEqual(hashlib.sha256(self.voices_path.read_bytes()).hexdigest(), provenance["hashes"]["voices_sha256"])
        self.assertFalse(provenance["simulation"])
        self.assertEqual("local-standalone", provenance["execution_mode"])
        self.assertFalse(provenance["productive_stage_completed"])
        self.assertFalse(provenance["manifest_updated"])
        self.assertEqual(["CPUExecutionProvider"], provenance["onnx_execution_providers"])
        self.assertEqual("pending", next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")["adapter_status"])
        self.assertEqual(manifest_before, manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(prior_outputs, [path.read_bytes() for path in output_paths])
        self.assertEqual(result["provenance"], provenance)

    def test_cli_runs_the_explicit_command_and_keeps_script_out_of_summary(self):
        output = io.StringIO()
        with self.patch_worker(), mock.patch("sys.stdout", output):
            result = pipeline.main([
                "run-tts-kokoro", str(self.config_path),
                "--python", str(self.python_path),
                "--model", str(self.model_path),
                "--voices", str(self.voices_path),
            ])
        self.assertEqual(0, result)
        summary = json.loads(output.getvalue())
        self.assertFalse(summary["simulation"])
        self.assertEqual("local-standalone", summary["execution_mode"])
        self.assertNotIn("A test voiceover script", output.getvalue())

    def test_runtime_path_makes_no_network_or_arbitrary_process_calls(self):
        with self.patch_worker(), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network socket opened")), \
             mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network request made")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected Popen")), \
             mock.patch.object(os, "system", side_effect=AssertionError("shell command invoked")):
            self.run_adapter()

    def test_timeout_does_not_publish_or_retry(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["python"], 300)) as run:
            with self.assertRaisesRegex(ValueError, "timeout; no retry"):
                self.run_adapter()
        run.assert_called_once()
        self.assertFalse((self.root / ".pipeline").exists())

    def test_rejects_corrupt_silent_or_hash_mismatched_worker_output_before_publication(self):
        cases = (
            (b"broken", worker_record(make_wav())),
            (make_wav([0] * 120), worker_record(make_wav())),
        )
        for wav_bytes, record in cases:
            with self.subTest(wav_size=len(wav_bytes)):
                with self.patch_worker(wav_bytes, record):
                    with self.assertRaises(ValueError):
                        self.run_adapter()
                self.assertFalse((self.root / ".pipeline").exists())
        wav_bytes = make_wav()
        record = worker_record(wav_bytes)
        record["audio"]["sha256"] = "0" * 64
        with self.patch_worker(wav_bytes, record):
            with self.assertRaisesRegex(ValueError, "report does not match"):
                self.run_adapter()
        self.assertFalse((self.root / ".pipeline").exists())

    def test_rejects_run_collision_without_overwriting_existing_files(self):
        output_root = self.root / ".pipeline" / "tts-local-runs"
        output_root.mkdir(parents=True)
        run_dir = output_root / "run-fixed"
        run_dir.mkdir()
        sentinel = run_dir / "keep.txt"
        sentinel.write_text("previous", encoding="utf-8")
        with mock.patch.object(pipeline.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed")), self.patch_worker():
            with self.assertRaises(FileExistsError):
                self.run_adapter()
        self.assertEqual("previous", sentinel.read_text(encoding="utf-8"))

    def test_cleans_only_owned_partial_publication_after_write_error(self):
        original = pipeline._write_exclusive_file

        def fail_provenance(path, payload, owned_files):
            if path.name == "narration-provenance.json":
                raise OSError("injected provenance write failure")
            return original(path, payload, owned_files)

        with self.patch_worker(), mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_provenance):
            with self.assertRaisesRegex(OSError, "injected provenance write failure"):
                self.run_adapter()
        output_root = self.root / ".pipeline" / "tts-local-runs"
        self.assertEqual([], list(output_root.iterdir()))

    def test_keeps_unowned_partial_file_and_reports_run_path(self):
        original = pipeline._write_exclusive_file
        retained = {}

        def add_unowned_then_fail(path, payload, owned_files):
            if path.name == "narration-provenance.json":
                sentinel = path.parent / "keep.txt"
                sentinel.write_text("foreign", encoding="utf-8")
                retained["sentinel"] = sentinel
                raise OSError("injected failure")
            return original(path, payload, owned_files)

        with self.patch_worker(), mock.patch.object(pipeline, "_write_exclusive_file", side_effect=add_unowned_then_fail):
            with self.assertRaisesRegex(ValueError, "partial run retained") as caught:
                self.run_adapter()
        sentinel = retained["sentinel"]
        self.assertIn(str(sentinel.parent), str(caught.exception))
        self.assertEqual("foreign", sentinel.read_text(encoding="utf-8"))
        self.assertEqual({"keep.txt"}, {path.name for path in sentinel.parent.iterdir()})

    def test_worker_command_is_fixed_and_never_uses_pipeline_executable(self):
        stage = next(stage for stage in self.config["stages"] if stage["id"] == "03-tts-narration")
        config_tool = next(tool for tool in self.config["tools"] if tool["id"] == "kokoro")
        config_tool["executable"] = "attacker-controlled-command"
        self.write_config()
        wav_bytes = make_wav()
        record = worker_record(wav_bytes)
        with self.patch_worker(wav_bytes, record):
            result = self.run_adapter()
        self.assertTrue(Path(result["summary"]["wav_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
