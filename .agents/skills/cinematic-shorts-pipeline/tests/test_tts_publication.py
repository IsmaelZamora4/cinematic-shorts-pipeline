import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import urllib.request

from test_pipeline import load_pipeline, make_tts_config
from test_tts_simulation import parse_riff_chunks


ROOT = Path(__file__).resolve().parents[1]
pipeline = load_pipeline()


def make_junction(link, target):
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"mklink /J failed: {result.stdout} {result.stderr}")


class TtsPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config = make_tts_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @property
    def publication_root(self):
        return self.root / ".pipeline" / "simulations" / "tts"

    def publish(self):
        return pipeline.save_tts_simulation(self.config_path)

    def test_publishes_verified_wav_and_provenance_to_fixed_run_directory(self):
        result = self.publish()
        run_dir = Path(result["publication_dir"])
        self.assertEqual(self.publication_root, run_dir.parent)
        self.assertTrue(run_dir.name.startswith("run-"))
        wav_path = run_dir / "mock-narration.wav"
        provenance_path = run_dir / "mock-provenance.json"
        wav_bytes = wav_path.read_bytes()
        provenance_bytes = provenance_path.read_bytes()
        provenance = json.loads(provenance_bytes)
        self.assertEqual({"mock-narration.wav", "mock-provenance.json"}, {p.name for p in run_dir.iterdir()})
        chunks = parse_riff_chunks(wav_bytes)
        self.assertEqual(b"WAVE", wav_bytes[8:12])
        self.assertIn(b"SIMULATED MOCK SILENCE; NOT SPEECH", chunks[b"LIST"][0])
        self.assertTrue(provenance["simulation"])
        self.assertEqual("mock", provenance["backend"])
        self.assertIsNone(provenance["provider_executed"])
        self.assertEqual(hashlib.sha256(wav_bytes).hexdigest(), provenance["wav_sha256"])
        self.assertEqual(hashlib.sha256((self.root / "voiceover-script.txt").read_bytes()).hexdigest(), provenance["script_sha256"])
        self.assertEqual(provenance, result["provenance"])
        self.assertEqual(provenance["wav_sha256"], result["summary"]["wav_sha256"])
        self.assertNotIn("script", provenance)

    def test_cli_publishes_json_summary_without_exposing_script(self):
        private_script = "PRIVATE NARRATION MUST NOT APPEAR"
        (self.root / "voiceover-script.txt").write_text(private_script, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, pipeline.main(["save-tts-simulation", str(self.config_path)]))
        summary = json.loads(output.getvalue())
        self.assertTrue(summary["simulation"])
        self.assertIn("publication_dir", summary)
        self.assertNotIn(private_script, output.getvalue())
        self.assertNotIn("wav_bytes", summary)

    def test_concurrent_publications_create_distinct_complete_directories(self):
        barrier = threading.Barrier(2)

        def publish_after_barrier():
            barrier.wait()
            return self.publish()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: publish_after_barrier(), range(2)))
        paths = [Path(result["publication_dir"]) for result in results]
        self.assertEqual(2, len(set(paths)))
        for path in paths:
            self.assertEqual({"mock-narration.wav", "mock-provenance.json"}, {p.name for p in path.iterdir()})

    def test_run_directory_collision_fails_without_replacing_existing_files(self):
        self.publication_root.mkdir(parents=True)
        run_dir = self.publication_root / "run-fixed"
        run_dir.mkdir()
        sentinel = run_dir / "keep.txt"
        sentinel.write_text("prior result", encoding="utf-8")
        fake_uuid = SimpleNamespace(hex="fixed")
        with mock.patch.object(pipeline.uuid, "uuid4", return_value=fake_uuid):
            with self.assertRaisesRegex(FileExistsError, "run-fixed"):
                self.publish()
        self.assertEqual("prior result", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(run_dir.iterdir()))

    def test_rejects_existing_symlink_or_junction_in_destination_chain(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / ".pipeline"
        if os.name == "nt":
            try:
                make_junction(link, outside)
            except (OSError, AssertionError) as exc:
                self.fail(f"junction fixture must be available on Windows: {exc}")
        else:
            link.symlink_to(outside, target_is_directory=True)
        sentinel = outside / "keep.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reparse point|symbolic link"):
            self.publish()
        self.assertEqual("untouched", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(outside.iterdir()))
        if os.name == "nt":
            os.rmdir(link)

    def test_windows_generic_reparse_attribute_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows st_file_attributes are only exposed on Windows")
        synthetic = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        self.assertTrue(pipeline._is_reparse_point(synthetic))

    def test_symlink_real_fixture_is_run_when_permissions_allow(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / ".pipeline"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink creation omitted: runner lacks the required privilege (WinError 1314)")
            raise
        sentinel = outside / "keep.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reparse point|symbolic link"):
            self.publish()
        self.assertEqual("untouched", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(outside.iterdir()))

    def test_cleans_owned_wav_if_provenance_write_fails(self):
        original = pipeline._write_exclusive_file

        def fail_on_provenance(path, payload, owned_files):
            if path.name == "mock-provenance.json":
                raise OSError("injected provenance write failure")
            return original(path, payload, owned_files)

        with mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_on_provenance):
            with self.assertRaisesRegex(OSError, "injected provenance write failure"):
                self.publish()
        self.assertEqual([], list(self.publication_root.iterdir()))

    def test_cleans_own_partial_provenance_after_write_failure(self):
        original = pipeline._write_exclusive_file

        def fail_after_partial(path, payload, owned_files):
            if path.name == "mock-provenance.json":
                with path.open("xb") as stream:
                    owned_files[path] = pipeline._file_identity(os.fstat(stream.fileno()))
                    stream.write(payload[:24])
                raise OSError("injected partial provenance write failure")
            return original(path, payload, owned_files)

        with mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_after_partial):
            with self.assertRaisesRegex(OSError, "injected partial provenance write failure"):
                self.publish()
        self.assertEqual([], list(self.publication_root.iterdir()))

    def test_cleans_own_partial_file_after_wav_write_failure(self):
        def fail_after_partial(path, payload, owned_files):
            if path.name == "mock-narration.wav":
                with path.open("xb") as stream:
                    owned_files[path] = pipeline._file_identity(os.fstat(stream.fileno()))
                    stream.write(payload[:32])
                raise OSError("injected WAV write failure")
            return pipeline._write_exclusive_file(path, payload, owned_files)

        with mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_after_partial):
            with self.assertRaisesRegex(OSError, "injected WAV write failure"):
                self.publish()
        self.assertEqual([], list(self.publication_root.iterdir()))

    def test_preserves_partial_if_file_identity_cannot_be_proven(self):
        original = pipeline._write_exclusive_file
        retained = {}

        def fail_and_hide_identity(path, payload, owned_files):
            if path.name == "mock-provenance.json":
                with path.open("xb") as stream:
                    stream.write(payload[:16])
                retained["path"] = path
                raise OSError("injected identity uncertainty")
            return original(path, payload, owned_files)

        with mock.patch.object(pipeline, "_write_exclusive_file", side_effect=fail_and_hide_identity):
            with self.assertRaisesRegex(ValueError, "partial publication retained") as caught:
                self.publish()
        self.assertIn(str(retained["path"].parent), str(caught.exception))
        self.assertTrue(retained["path"].exists())
        self.assertFalse((retained["path"].parent / "mock-narration.wav").exists())

    def test_cleanup_never_removes_unowned_file_from_partial_directory(self):
        original = pipeline._write_exclusive_file
        retained = {}

        def add_unowned_file_then_fail(path, payload, owned_files):
            if path.name == "mock-provenance.json":
                sentinel = path.parent / "keep.txt"
                sentinel.write_text("not owned by publisher", encoding="utf-8")
                retained["sentinel"] = sentinel
                raise OSError("injected failure after unowned file appeared")
            return original(path, payload, owned_files)

        with mock.patch.object(pipeline, "_write_exclusive_file", side_effect=add_unowned_file_then_fail):
            with self.assertRaisesRegex(ValueError, "partial publication retained"):
                self.publish()
        self.assertEqual("not owned by publisher", retained["sentinel"].read_text(encoding="utf-8"))
        self.assertEqual({"keep.txt"}, {path.name for path in retained["sentinel"].parent.iterdir()})

    def test_existing_productive_outputs_and_manifest_are_not_modified(self):
        manifest = self.root / "run-manifest.json"
        pending = pipeline.new_pending_manifest(self.config, self.config_path)
        manifest.write_text(json.dumps(pending, sort_keys=True), encoding="utf-8")
        output_paths = [self.root / ".pipeline" / "work" / name for name in ("narration.wav", "narration-provenance.json")]
        for path in output_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"existing productive artifact")
        before = {path: path.read_bytes() for path in [manifest, *output_paths]}
        self.publish()
        self.assertEqual(before, {path: path.read_bytes() for path in [manifest, *output_paths]})

    def test_save_command_rejects_destination_fields_and_keeps_planning_commands_read_only(self):
        self.config["destination"] = "../../outside"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown field"):
            self.publish()
        self.assertFalse((self.root / ".pipeline").exists())

    def test_publication_uses_no_processes_or_network(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("external process invoked")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("external process invoked")), \
             mock.patch.object(os, "system", side_effect=AssertionError("shell command invoked")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network socket opened")), \
             mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network request made")):
            self.publish()


if __name__ == "__main__":
    unittest.main()
