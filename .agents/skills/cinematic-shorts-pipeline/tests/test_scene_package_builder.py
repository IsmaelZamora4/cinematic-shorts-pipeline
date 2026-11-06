import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from test_pipeline import ROOT
from test_png_rgb8_decoder import make_blender_style_png

sys.path.insert(0, str(ROOT / "scripts"))
import scene_package_builder as builder
import scene_package_validator as validator


class ScenePackageBuilderTests(unittest.TestCase):
    def test_builds_and_validates_27_member_package_with_blender_style_pngs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene_id = "shot-01"
            frame_data = make_blender_style_png()
            blend = root / "scenes" / scene_id / "scene.blend"
            blend.parent.mkdir(parents=True)
            blend.write_bytes(b"BLENDER17-01v0502" + bytes(128))
            frames = blend.parent / "frames"
            frames.mkdir()
            for frame in range(24):
                (frames / f"frame-{frame:06d}.png").write_bytes(frame_data)
            archive = root / "package.zip"
            inputs = {name: hashlib.sha256(name.encode()).hexdigest() for name in
                      ("storyboard", "validated-plan", "resource-scene-spec")}
            runtime = {
                "blender_version": [5, 2, 3], "blender_version_string": "5.2.3",
                "blender_version_cycle": "release",
                "blender_executable_sha256": hashlib.sha256(b"blender").hexdigest(),
                "worker_version": 1, "worker_sha256": hashlib.sha256(b"worker").hexdigest(),
            }
            observed = {
                "frames": list(range(24)), "minimum_camera_target_distance_m": 4.0,
                "render_seconds": 1.0, "worker_seconds": 1.2,
            }
            digest = builder.build_scene_package(
                root, archive, project_id="test-project", scene_id=scene_id,
                run_id="26fd6247-1a97-4dbf-b76b-f1bb9670b7b2", runtime=runtime,
                input_hashes=inputs, stage_01_fingerprint=hashlib.sha256(b"stage01").hexdigest(),
                stage_02_fingerprint=hashlib.sha256(b"stage02").hexdigest(),
                render_seed=17, observed=observed,
            )
            self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())
            context = validator.ScenePackageContext(
                project_id="test-project", scene_id=scene_id,
                run_id="26fd6247-1a97-4dbf-b76b-f1bb9670b7b2",
                stage_01_fingerprint=hashlib.sha256(b"stage01").hexdigest(),
                stage_02_fingerprint=hashlib.sha256(b"stage02").hexdigest(),
                input_hashes=inputs, runtime=runtime, expected_render_seed=17,
                manifest_scene_package_sha256=digest,
            )
            with archive.open("rb") as stream:
                result = validator.validate_scene_package(stream, expected_context=context)
            self.assertTrue(result.context_verified)
            self.assertEqual(27, result.member_count)
            self.assertEqual(25, result.payload_count)

    def test_rejects_staged_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.write_bytes(b"BLENDER17-01v0502" + bytes(128))
            blend = root / "scenes" / "shot-01" / "scene.blend"
            blend.parent.mkdir(parents=True)
            try:
                blend.symlink_to(source)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "link|reparse"):
                builder._staged_regular_file(root, "scenes/shot-01/scene.blend", 512 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
