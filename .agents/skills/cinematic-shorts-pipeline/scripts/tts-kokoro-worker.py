#!/usr/bin/env python3
"""Run one fixed local Kokoro inference and return the WAV bytes on stdout."""
import argparse
import ctypes
import hashlib
import importlib.metadata
import inspect
import io
import json
import math
import os
import sys
import time
from pathlib import Path


VOICE = "em_alex"
LANGUAGE = "es"
SAMPLE_RATE_HZ = 24000
MAX_SCRIPT_BYTES = 8192
MAX_WAV_BYTES = 16 * 1024 * 1024
MAX_DURATION_SECONDS = 300
RUNTIME_VERSION = "0.6.1"


def _peak_working_set_bytes():
    """Return the process peak working set when the OS exposes it, otherwise None."""
    if os.name == "nt":
        try:
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_process = kernel32.GetCurrentProcess
            get_process.restype = wintypes.HANDLE
            get_info = psapi.GetProcessMemoryInfo
            get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            get_info.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if get_info(get_process(), ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
            return None
        except (AttributeError, OSError, TypeError):
            return None

    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    except (ImportError, AttributeError, OSError, ValueError):
        return None


def _distribution_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"required runtime distribution is missing: {name}") from exc


def synthesize(script_bytes, model_path, voices_path, *, kokoro_class=None, runtime=None, soundfile_module=None):
    """Synthesize one validated script using only Kokoro's fixed Python API."""
    if not ((3, 10) <= sys.version_info[:2] < (3, 14)):
        raise RuntimeError("Kokoro worker requires Python >=3.10 and <3.14")
    if not isinstance(script_bytes, bytes) or not script_bytes or len(script_bytes) > MAX_SCRIPT_BYTES:
        raise ValueError(f"script must contain 1 to {MAX_SCRIPT_BYTES} UTF-8 bytes")
    if b"\x00" in script_bytes:
        raise ValueError("script must not contain NUL bytes")
    try:
        text = script_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("script must contain valid UTF-8") from exc
    if not text.strip():
        raise ValueError("script must not be empty or whitespace-only")
    if os.environ.get("ONNX_PROVIDER") != "CPUExecutionProvider":
        raise RuntimeError("worker requires ONNX_PROVIDER=CPUExecutionProvider")

    if runtime is None:
        import onnxruntime as runtime
    if kokoro_class is None:
        from kokoro_onnx import Kokoro as kokoro_class
    if soundfile_module is None:
        import soundfile as soundfile_module

    if "CPUExecutionProvider" not in runtime.get_available_providers():
        raise RuntimeError("ONNX Runtime does not provide CPUExecutionProvider")
    if _distribution_version("kokoro-onnx") != RUNTIME_VERSION:
        raise RuntimeError(f"kokoro-onnx {RUNTIME_VERSION} is required")
    if not {"model_path", "voices_path"}.issubset(inspect.signature(kokoro_class.__init__).parameters):
        raise RuntimeError("installed Kokoro constructor signature is incompatible")
    if not {"text", "voice", "lang", "speed"}.issubset(inspect.signature(kokoro_class.create).parameters):
        raise RuntimeError("installed Kokoro.create signature is incompatible")

    init_started = time.perf_counter()
    model = kokoro_class(str(model_path), str(voices_path))
    model_init_seconds = time.perf_counter() - init_started
    providers = list(model.sess.get_providers())
    if providers != ["CPUExecutionProvider"]:
        raise RuntimeError("effective ONNX provider is not CPUExecutionProvider")
    if VOICE not in model.get_voices():
        raise RuntimeError(f"required local voice is missing: {VOICE}")

    inference_started = time.perf_counter()
    samples, sample_rate = model.create(text, voice=VOICE, lang=LANGUAGE, speed=1.0)
    inference_seconds = time.perf_counter() - inference_started
    shape = getattr(samples, "shape", None)
    if shape is not None and len(shape) != 1:
        raise ValueError("Kokoro returned audio that is not mono")
    if int(sample_rate) != SAMPLE_RATE_HZ:
        raise ValueError(f"Kokoro returned unsupported sample rate: {sample_rate}")
    try:
        sample_count = len(samples)
    except TypeError as exc:
        raise ValueError("Kokoro returned an invalid sample array") from exc
    if sample_count <= 0:
        raise ValueError("Kokoro returned no audio samples")
    if not all(math.isfinite(float(sample)) for sample in samples):
        raise ValueError("Kokoro returned non-finite audio samples")
    duration_seconds = sample_count / SAMPLE_RATE_HZ
    if duration_seconds > MAX_DURATION_SECONDS:
        raise ValueError(f"audio exceeds pilot duration limit of {MAX_DURATION_SECONDS} seconds")
    predicted_size = 44 + sample_count * 2
    if predicted_size > MAX_WAV_BYTES:
        raise ValueError(f"audio exceeds pilot WAV limit of {MAX_WAV_BYTES} bytes")

    wav_stream = io.BytesIO()
    soundfile_module.write(wav_stream, samples, SAMPLE_RATE_HZ, format="WAV", subtype="PCM_16")
    wav_bytes = wav_stream.getvalue()
    if len(wav_bytes) > MAX_WAV_BYTES:
        raise ValueError(f"audio exceeds pilot WAV limit of {MAX_WAV_BYTES} bytes")
    digest = hashlib.sha256(wav_bytes).hexdigest()
    versions = {
        name: _distribution_version(name)
        for name in ("kokoro-onnx", "onnxruntime", "numpy", "phonemizer", "espeakng-loader", "soundfile")
    }
    return {
        "provider": "kokoro-onnx",
        "onnx_execution_providers": providers,
        "voice": VOICE,
        "language": LANGUAGE,
        "audio": {
            "container": "wav",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": 1,
            "sample_format": "pcm_s16le",
            "frames": sample_count,
            "duration_seconds": duration_seconds,
            "size_bytes": len(wav_bytes),
            "sha256": digest,
        },
        "versions": versions,
        "python_version": sys.version.split()[0],
        "timing_seconds": {
            "model_initialization": model_init_seconds,
            "inference": inference_seconds,
        },
        "peak_working_set_bytes": _peak_working_set_bytes(),
        "wav_bytes": wav_bytes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument("--voice", choices=(VOICE,), required=True)
    parser.add_argument("--language", choices=(LANGUAGE,), required=True)
    args = parser.parse_args(argv)
    try:
        script_bytes = sys.stdin.buffer.read(MAX_SCRIPT_BYTES + 1)
        result = synthesize(script_bytes, args.model, args.voices)
    except Exception as exc:
        print("KOKORO_RESULT=" + json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1
    wav_bytes = result.pop("wav_bytes")
    sys.stdout.buffer.write(wav_bytes)
    sys.stdout.buffer.flush()
    print("KOKORO_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
