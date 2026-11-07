import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_pipeline import load_pipeline


pipeline = load_pipeline()


class BoundedArtifactReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_wav_initial_size_above_limit(self):
        path = self.root / "narration.wav"
        path.write_bytes(b"W" * (pipeline.MAX_KOKORO_WAV_BYTES + 1))

        with self.assertRaisesRegex(ValueError, "exceeds maximum size"):
            pipeline._read_bounded_regular_file(
                path, pipeline.MAX_KOKORO_WAV_BYTES, "Kokoro WAV", require_identity=True
            )

    def test_rejects_provenance_initial_size_above_limit(self):
        path = self.root / "narration-provenance.json"
        path.write_bytes(b"P" * (pipeline.MAX_KOKORO_PROVENANCE_BYTES + 1))

        with self.assertRaisesRegex(ValueError, "exceeds maximum size"):
            pipeline._read_bounded_regular_file(
                path, pipeline.MAX_KOKORO_PROVENANCE_BYTES, "Kokoro provenance", require_identity=True
            )

    def test_growth_after_initial_stat_reads_at_most_limit_plus_one(self):
        path = self.root / "growing.wav"
        path.write_bytes(b"A" * 8)
        maximum = 16
        original_open = Path.open
        read_sizes = []

        class ReadSpy:
            def __init__(self, stream, candidate):
                self.stream = stream
                self.candidate = candidate

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def read(self, size=-1):
                read_sizes.append(size)
                if self.candidate == path:
                    with original_open(path, "ab") as stream:
                        stream.write(b"B" * 32)
                return self.stream.read(size)

        def open_with_growth(candidate, mode="r", *args, **kwargs):
            stream = original_open(candidate, mode, *args, **kwargs)
            if candidate == path and mode == "rb":
                return ReadSpy(stream, candidate)
            return stream

        with mock.patch.object(Path, "open", open_with_growth):
            with self.assertRaisesRegex(ValueError, "exceeds maximum size"):
                pipeline._read_bounded_regular_file(path, maximum, "growing WAV", require_identity=True)

        self.assertEqual([maximum + 1], read_sizes)

    def test_rejects_replacement_between_inspection_and_open(self):
        path = self.root / "replaced.wav"
        path.write_bytes(b"original")
        original_open = Path.open

        def replace_before_open(candidate, mode="r", *args, **kwargs):
            if candidate == path and mode == "rb":
                replacement = self.root / "replacement.wav"
                replacement.write_bytes(b"replacement")
                os.replace(replacement, path)
            return original_open(candidate, mode, *args, **kwargs)

        with mock.patch.object(Path, "open", replace_before_open):
            with self.assertRaisesRegex(ValueError, "changed while opening"):
                pipeline._read_bounded_regular_file(path, 64, "Kokoro WAV", require_identity=True)

    def test_rejects_same_inode_modification_during_read(self):
        path = self.root / "modified.wav"
        path.write_bytes(b"A" * 32)
        original_open = Path.open
        changed = False

        class MutatingReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def read(self, size=-1):
                nonlocal changed
                payload = self.stream.read(size)
                if not changed:
                    changed = True
                    with original_open(path, "r+b") as writer:
                        writer.write(b"Z" * 32)
                        writer.flush()
                    os.utime(path, ns=(946684800000000000, 946684801000000000))
                return payload

        def open_with_mutation(candidate, mode="r", *args, **kwargs):
            stream = original_open(candidate, mode, *args, **kwargs)
            if candidate == path and mode == "rb":
                return MutatingReader(stream)
            return stream

        with mock.patch.object(Path, "open", open_with_mutation):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                pipeline._read_bounded_regular_file(path, 64, "Kokoro WAV", require_identity=True)

    def test_accepts_unchanged_file_within_limit(self):
        path = self.root / "valid.wav"
        expected = b"valid synthetic WAV fixture"
        path.write_bytes(expected)

        self.assertEqual(
            expected, pipeline._read_bounded_regular_file(path, 64, "Kokoro WAV", require_identity=True)
        )


if __name__ == "__main__":
    unittest.main()
