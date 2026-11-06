import importlib.util
import hashlib
import json
import math
import os
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from test_pipeline import ROOT, load_pipeline
from test_productive_scenes_profile import make_scenes_config, make_valid_scene_spec


pipeline = load_pipeline()
WORKER_PATH = ROOT / "scripts" / "blender-scenes-worker.py"
worker_spec = importlib.util.spec_from_file_location("blender_scenes_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class BlenderPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.executable = self.root / ("blender.exe" if os.name == "nt" else "blender")
        self.executable.write_bytes(b"fake Blender executable")
        if os.name != "nt":
            self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.temp.cleanup()

    def test_preflight_probes_explicit_executable_without_shell_and_accepts_5_2_x(self):
        completed = pipeline.subprocess.CompletedProcess(
            [str(self.executable), "--version"], 0, stdout="Blender 5.2.3\n", stderr="",
        )
        with mock.patch.object(pipeline, "_run_blender_version_probe", return_value=completed) as run:
            runtime = pipeline._preflight_blender_runtime(self.executable)
        self.assertEqual((5, 2, 3), runtime["version"])
        self.assertEqual("5.2.3", runtime["version_string"])
        self.assertEqual(str(self.executable), runtime["path"])
        args, kwargs = run.call_args
        self.assertEqual([str(self.executable), "--version"], args[0])
        self.assertEqual(self.executable.parent, args[1])

        completed = pipeline.subprocess.CompletedProcess(
            [str(self.executable), "--version"], 0, stdout="Blender 5.2.4 LTS\n", stderr="",
        )
        with mock.patch.object(pipeline, "_run_blender_version_probe", return_value=completed):
            self.assertEqual((5, 2, 4), pipeline._preflight_blender_runtime(self.executable)["version"])

    def test_preflight_rejects_missing_nonregular_and_nonexecutable_paths(self):
        with self.assertRaisesRegex(ValueError, "absolute path"):
            pipeline._preflight_blender_runtime("blender")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            pipeline._preflight_blender_runtime(self.root / "missing-blender")

        directory = self.root / "blender-dir"
        directory.mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            pipeline._preflight_blender_runtime(directory)

        if os.name != "nt":
            self.executable.chmod(stat.S_IRUSR | stat.S_IWUSR)
            with self.assertRaisesRegex(ValueError, "executable"):
                pipeline._preflight_blender_runtime(self.executable)

    def test_preflight_rejects_links_and_reparse_targets_before_launch(self):
        alias = self.root / "blender-alias"
        try:
            alias.symlink_to(self.executable)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=AssertionError("must not launch")):
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse"):
                pipeline._preflight_blender_runtime(alias)

    def test_preflight_rejects_failed_malformed_unsupported_and_oversized_probe(self):
        cases = (
            (1, "Blender 5.2.3\n", "exit code"),
            (0, "Blender 5.3.0\n", "5.2.x"),
            (0, "Blender 5.1.99\n", "5.2.x"),
            (0, "not Blender\n", "version output"),
            (0, "Blender 5.2.3\n" + "x" * pipeline.MAX_BLENDER_PREFLIGHT_OUTPUT_BYTES, "output exceeds"),
            (0, "Blender 5.2.3 Alpha\n", "prerelease"),
        )
        for returncode, stdout, message in cases:
            with self.subTest(message=message):
                completed = pipeline.subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
                with mock.patch.object(pipeline, "_run_blender_version_probe", return_value=completed):
                    with self.assertRaisesRegex(ValueError, message):
                        pipeline._preflight_blender_runtime(self.executable)

    def test_preflight_converts_timeout_to_controlled_error(self):
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=ValueError("Blender version probe timed out")):
            with self.assertRaisesRegex(ValueError, "version probe timed out"):
                pipeline._preflight_blender_runtime(self.executable)

    def test_real_process_output_is_bounded_and_timeout_is_enforced(self):
        command = [sys.executable, "-c", "import os,time; os.write(1,b'x'*1048576); time.sleep(30)"]
        with self.assertRaisesRegex(ValueError, "output exceeds"):
            pipeline._run_blender_version_probe(command, self.root)
        with mock.patch.object(pipeline, "BLENDER_PREFLIGHT_TIMEOUT_SECONDS", 0.3):
            with self.assertRaisesRegex(ValueError, "timed out"):
                pipeline._run_blender_version_probe([sys.executable, "-c", "import time; time.sleep(30)"], self.root)


class BlenderWorkerCameraTests(unittest.TestCase):
    def test_valid_camera_trajectory_has_exact_24_samples_and_analytic_distance(self):
        samples, minimum_distance = worker._camera_samples(make_valid_scene_spec())
        self.assertEqual(list(range(24)), [sample["frame"] for sample in samples])
        self.assertEqual([0.0, 1.0], [samples[0]["t"], samples[-1]["t"]])
        self.assertGreater(minimum_distance, 0.01)
        self.assertTrue(all(all(math.isfinite(value) for value in sample["direction"]) for sample in samples))

    def test_analytic_distance_rejects_target_crossing_between_sampled_frames(self):
        spec = make_valid_scene_spec()
        spec["camera"]["keyframes"][0].update(location_m=[-2.0, 0.0, 0.0], target_m=[0.0, 0.0, 0.005])
        spec["camera"]["keyframes"][1].update(location_m=[1.0, 0.0, 0.0], target_m=[0.0, 0.0, 0.005])
        self.assertNotIn(2.0 / 3.0, [frame / 23 for frame in range(24)])
        with self.assertRaisesRegex(ValueError, "interpolated camera-target distance"):
            worker._camera_samples(spec)

    def test_rejects_invalid_up_orientation_and_nonfinite_camera_values(self):
        spec = make_valid_scene_spec()
        spec["camera"]["keyframes"][0].update(location_m=[0.0, 0.0, 0.0], target_m=[0.0, 0.0, 1.0])
        spec["camera"]["keyframes"][1].update(location_m=[0.0, 0.0, 0.0], target_m=[0.1, 0.0, 1.0])
        with self.assertRaisesRegex(ValueError, "orientation is invalid"):
            worker._camera_samples(spec)

        spec = make_valid_scene_spec()
        spec["camera"]["keyframes"][0]["location_m"][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            worker._camera_samples(spec)


class BlenderWorkerRuntimeTests(unittest.TestCase):
    def run_main_until_scene_configuration(self, version, version_string, version_cycle):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            request = {
                "request_version": 1, "project_id": "test-project",
                "scene_spec": make_valid_scene_spec(), "expected_blender_version": [5, 2, 3],
                "expected_blender_version_string": "5.2.3", "staging_root": str(root),
            }
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            bpy = types.SimpleNamespace(app=types.SimpleNamespace(
                version=version, version_string=version_string, version_cycle=version_cycle,
                build_options=types.SimpleNamespace(cycles=True),
            ))
            with (
                mock.patch.dict(sys.modules, {"bpy": bpy, "mathutils": types.SimpleNamespace(Vector=object)}),
                mock.patch.object(worker, "_configure_scene", side_effect=RuntimeError("scene configuration reached")) as configure,
            ):
                try:
                    worker.main(["--request", str(request_path)])
                except RuntimeError as error:
                    return configure.call_count, error
                return configure.call_count, None

    def test_main_accepts_numeric_version_when_release_display_string_has_lts_suffix(self):
        calls, error = self.run_main_until_scene_configuration((5, 2, 3), "5.2.3 LTS", "release")
        self.assertEqual(1, calls)
        self.assertRegex(str(error), "scene configuration reached")

    def test_main_rejects_numeric_version_mismatch_and_prerelease_cycle(self):
        calls, error = self.run_main_until_scene_configuration((5, 2, 4), "5.2.4", "release")
        self.assertEqual(0, calls)
        self.assertRegex(str(error), "differs from preflight version")
        calls, error = self.run_main_until_scene_configuration((5, 2, 3), "5.2.3 Alpha", "alpha")
        self.assertEqual(0, calls)
        self.assertRegex(str(error), "prerelease or development")

    def test_blender_52_channelbags_set_camera_and_seed_interpolation(self):
        slot = object()
        def curve(path, index):
            return types.SimpleNamespace(data_path=path, array_index=index, keyframe_points=[
                types.SimpleNamespace(interpolation="BEZIER", co=(frame, 0)) for frame in range(24)
            ])
        camera_curves = [curve("location", i) for i in range(3)] + [curve("rotation_quaternion", i) for i in range(4)]
        seed_curves = [curve("cycles.seed", 0)]
        def owner(curves):
            bag = types.SimpleNamespace(fcurves=curves)
            strip = types.SimpleNamespace(type="KEYFRAME", channelbag=mock.Mock(return_value=bag))
            action = types.SimpleNamespace(layers=[types.SimpleNamespace(strips=[strip])])
            return types.SimpleNamespace(animation_data=types.SimpleNamespace(action=action, action_slot=slot)), strip
        camera, camera_strip = owner(camera_curves)
        scene, seed_strip = owner(seed_curves)
        worker._set_action_interpolation(camera, {"location": 3, "rotation_quaternion": 4}, "LINEAR")
        worker._set_action_interpolation(scene, {"cycles.seed": 1}, "CONSTANT")
        camera_strip.channelbag.assert_called_once_with(slot)
        seed_strip.channelbag.assert_called_once_with(slot)
        self.assertTrue(all(point.interpolation == "LINEAR" for curve in camera_curves for point in curve.keyframe_points))
        self.assertTrue(all(point.interpolation == "CONSTANT" for point in seed_curves[0].keyframe_points))
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            worker._set_action_interpolation(scene, {"missing": 1}, "CONSTANT")

    def test_float32_properties_and_sockets_allow_rounding_but_not_policy_changes(self):
        def f32(value):
            return struct.unpack("f", struct.pack("f", value))[0]
        class Quantized:
            def __setattr__(self, name, value):
                if isinstance(value, float):
                    value = f32(value)
                elif isinstance(value, tuple):
                    value = tuple(f32(x) for x in value)
                object.__setattr__(self, name, value)
        prop = Quantized()
        prop.value = 0.0
        for value in (0.4, 0.6, 0.82, 0.01):
            worker._set_value(prop, "value", value, "test")
        socket = Quantized()
        socket.default_value = (0.0, 0.0, 0.0, 0.0)
        node = types.SimpleNamespace(inputs={"Color": socket})
        worker._set_socket(node, "Color", (0.4, 0.6, 0.82, 1.0))
        self.assertFalse(worker._matches_value(f32(0.41), 0.4))
        self.assertFalse(worker._matches_value(1, True))
        self.assertFalse(worker._matches_value(1.0, 1))


class BlenderWorkerRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def valid_request(self):
        return {
            "request_version": 1,
            "project_id": "test-project",
            "scene_spec": make_valid_scene_spec(),
            "expected_blender_version": [5, 2, 3],
            "expected_blender_version_string": "5.2.3",
            "staging_root": str(self.root.resolve()),
        }

    def test_worker_accepts_only_bounded_strict_request_and_exact_runtime(self):
        normalized = worker._validate_request(self.valid_request())
        self.assertEqual([5, 2, 3], normalized["expected_blender_version"])
        for request in (
            {**self.valid_request(), "command": "touch anything"},
            {**self.valid_request(), "expected_blender_version": [5, 3, 0]},
            {**self.valid_request(), "staging_root": "relative/path"},
        ):
            with self.subTest(request=request.keys()):
                with self.assertRaises(ValueError):
                    worker._validate_request(request)

    def test_isolated_worker_loads_fixed_sibling_validators_and_validates_scene_spec(self):
        scene_spec_path = self.root / "scene-spec.json"
        scene_spec_path.write_text(json.dumps(make_valid_scene_spec()), encoding="utf-8")
        isolated_code = r'''
import importlib.util, json, sys
from pathlib import Path
worker_path, scene_spec_path = map(Path, sys.argv[1:3])
script_dir = worker_path.resolve().parent
assert Path.cwd() != script_dir
assert str(script_dir) not in sys.path
spec = importlib.util.spec_from_file_location("isolated_blender_worker", worker_path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
before = list(sys.path)
validator = worker._load_pipeline_validator()
assert sys.path == before, "worker did not restore sys.path"
for name in ("scene_package_builder", "scene_package_validator", "png_rgb8_decoder"):
    imported = sys.modules[name]
    assert Path(imported.__file__).resolve() == (script_dir / (name + ".py")).resolve()
scene = json.loads(scene_spec_path.read_text(encoding="utf-8"))
storyboard = {"aspect_ratio": "9:16", "fps": 12, "total_frames": 24,
              "scenes": [{"id": scene["scene_id"], "start_frame": 0, "end_frame": 24}]}
errors = validator._validate_blender_scene_spec(scene, scene["project_id"], storyboard)
assert errors == [], errors
print("ISOLATED_VALID_SCENE_SPEC_PASS")
'''
        completed = subprocess.run(
            [sys.executable, "-I", "-c", isolated_code, str(WORKER_PATH), str(scene_spec_path)],
            cwd=self.root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
        self.assertIn("ISOLATED_VALID_SCENE_SPEC_PASS", completed.stdout)

    def test_isolated_worker_reports_controlled_error_when_sibling_dependency_is_absent(self):
        isolated_scripts = self.root / "isolated-scripts"
        isolated_scripts.mkdir()
        isolated_worker = isolated_scripts / WORKER_PATH.name
        isolated_worker.write_bytes(WORKER_PATH.read_bytes())
        (isolated_scripts / "pipeline.py").write_bytes((WORKER_PATH.parent / "pipeline.py").read_bytes())
        isolated_code = r'''
import importlib.util, sys
from pathlib import Path
worker_path = Path(sys.argv[1])
script_dir = worker_path.resolve().parent
assert Path.cwd() != script_dir
assert str(script_dir) not in sys.path
spec = importlib.util.spec_from_file_location("isolated_blender_worker", worker_path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
before = list(sys.path)
try:
    worker._load_pipeline_validator()
except ValueError as exc:
    assert "fixed pipeline scene-spec validator dependencies are unavailable" in str(exc), str(exc)
    assert sys.path == before, "worker did not restore sys.path after import failure"
    print("CONTROLLED_MISSING_DEPENDENCY_ERROR")
else:
    raise AssertionError("missing sibling dependency unexpectedly loaded")
'''
        completed = subprocess.run(
            [sys.executable, "-I", "-c", isolated_code, str(isolated_worker)],
            cwd=self.root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
        self.assertIn("CONTROLLED_MISSING_DEPENDENCY_ERROR", completed.stdout)

    def test_worker_parser_rejects_duplicate_keys_nonstandard_numbers_and_oversize(self):
        valid = json.dumps(self.valid_request())
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            worker._parse_bounded_json((valid[:-1] + ', "request_version":1}').encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "non-standard JSON number"):
            worker._parse_bounded_json(valid.replace('"request_version": 1', '"request_version": NaN').encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "maximum size"):
            worker._parse_bounded_json(b" " * (worker.MAX_REQUEST_BYTES + 1))

    def test_worker_rejects_code_fields_and_version_mismatch(self):
        for change in (
            {"command": "python script.py"},
            {"addons": ["unsafe-addon"]},
            {"expected_blender_version_string": "5.2.4"},
            {"expected_blender_version": [5, 2, True]},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    worker._validate_request({**self.valid_request(), **change})


class BlenderWorkerParentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_scenes_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.blender_path = self.root / ("blender.exe" if os.name == "nt" else "blender")
        self.blender_path.write_bytes(b"mock runtime binary")
        self.runtime = {
            "path": str(self.blender_path.resolve()),
            "version": (5, 2, 3),
            "version_string": "5.2.3",
            "sha256": hashlib.sha256(self.blender_path.read_bytes()).hexdigest(),
            "size_bytes": self.blender_path.stat().st_size,
        }

    def tearDown(self):
        self.temp.cleanup()

    def successful_worker_run(self, command, cwd):
        self.assertEqual(self.root.resolve(), Path(cwd).resolve())
        self.assertEqual(str(self.blender_path.resolve()), command[0])
        self.assertEqual("--background", command[1])
        self.assertEqual("--factory-startup", command[2])
        self.assertEqual("--disable-autoexec", command[3])
        self.assertEqual(["--threads", "2"], command[command.index("--threads"):command.index("--threads") + 2])
        request_path = Path(command[command.index("--request") + 1])
        request_bytes = request_path.read_bytes()
        self.assertLessEqual(len(request_bytes), pipeline.MAX_BLENDER_WORKER_REQUEST_BYTES)
        request = worker._validate_request(json.loads(request_bytes.decode("utf-8")))
        self.assertEqual("test-project", request["project_id"])
        staging_root = Path(request["staging_root"])
        scene_id = request["scene_spec"]["scene_id"]
        outputs = [f"scenes/{scene_id}/scene.blend"] + [
            f"scenes/{scene_id}/frames/frame-{frame:06d}.png" for frame in range(24)
        ]
        for relative in outputs:
            path = staging_root.joinpath(*Path(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"mock staged output")
        response = {
            "record_type": "blender-scenes-worker-result",
            "worker_version": 1,
            "blender_version": [5, 2, 3],
            "blender_version_string": "5.2.3",
            "project_id": "test-project",
            "scene_id": scene_id,
            "frames": list(range(24)),
            "outputs": outputs,
            "minimum_camera_target_distance_m": 4.0,
            "render_seconds": 1.0,
            "worker_seconds": 1.2,
        }
        (staging_root / "response.json").write_text(json.dumps(response), encoding="utf-8")

    def test_parent_accepts_normalized_numeric_version_string_and_verified_staging_receipt(self):
        with mock.patch.object(pipeline, "_run_blender_process", side_effect=self.successful_worker_run) as run:
            result = pipeline._run_blender_scenes_worker(self.config_path, self.runtime)
        self.assertEqual("pending", result["stage_02_status"])
        self.assertEqual("5.2.3", result["response"]["blender_version_string"])
        self.assertEqual(list(range(24)), result["response"]["frames"])
        staging_root = Path(result["staging_root"])
        expected_root = (self.root / ".pipeline" / "work" / "blender-scenes").resolve()
        common_root = os.path.commonpath((str(staging_root.resolve()), str(expected_root)))
        self.assertEqual(os.path.normcase(str(expected_root)), os.path.normcase(common_root))
        self.assertEqual(1, run.call_count)
        self.assertFalse((self.root / ".pipeline" / "work" / "scenes.zip").exists())
        manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        self.assertEqual("pending", manifest["stages"]["02-blender-scenes"]["status"])

    def test_parent_rejects_input_changes_during_worker_and_keeps_stage_pending(self):
        def mutate_input(command, cwd):
            self.successful_worker_run(command, cwd)
            changed = make_valid_scene_spec()
            changed["materials"][0]["roughness"] = 0.65
            (self.root / "scene-spec.json").write_text(json.dumps(changed), encoding="utf-8")

        with mock.patch.object(pipeline, "_run_blender_process", side_effect=mutate_input):
            with self.assertRaisesRegex(ValueError, "inputs changed while Blender worker was running"):
                pipeline._run_blender_scenes_worker(self.config_path, self.runtime)
        manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        self.assertEqual("pending", manifest["stages"]["02-blender-scenes"]["status"])

    def test_parent_revalidates_scene_spec_against_storyboard_before_launch(self):
        original_read = pipeline._read_bounded_regular_file
        changed_spec = make_valid_scene_spec()
        changed_spec["scene_id"] = "different-shot"

        def change_spec_before_read(path, maximum_size, label):
            if label == "blender-scene-spec":
                (self.root / "scene-spec.json").write_text(json.dumps(changed_spec), encoding="utf-8")
            return original_read(path, maximum_size, label)

        with (
            mock.patch.object(pipeline, "_read_bounded_regular_file", side_effect=change_spec_before_read),
            mock.patch.object(pipeline, "_run_blender_process", side_effect=AssertionError("must not launch")),
        ):
            with self.assertRaisesRegex(ValueError, "no longer matches.*storyboard"):
                pipeline._run_blender_scenes_worker(self.config_path, self.runtime)

    def test_parent_rejects_malformed_response_and_incomplete_staging_outputs(self):
        def malformed_worker(command, cwd):
            self.successful_worker_run(command, cwd)
            response_path = Path(command[command.index("--request") + 1]).with_name("response.json")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            response["frames"].pop()
            response_path.write_text(json.dumps(response), encoding="utf-8")

        with mock.patch.object(pipeline, "_run_blender_process", side_effect=malformed_worker):
            with self.assertRaisesRegex(ValueError, "does not match the preflighted request"):
                pipeline._run_blender_scenes_worker(self.config_path, self.runtime)

        def missing_frame(command, cwd):
            self.successful_worker_run(command, cwd)
            request = json.loads(Path(command[command.index("--request") + 1]).read_text(encoding="utf-8"))
            (Path(request["staging_root"]) / "scenes" / "shot-01" / "frames" / "frame-000007.png").unlink()

        with mock.patch.object(pipeline, "_run_blender_process", side_effect=missing_frame):
            with self.assertRaisesRegex(ValueError, "expected output is absent or invalid"):
                pipeline._run_blender_scenes_worker(self.config_path, self.runtime)

    def test_parent_rejects_invalid_staging_output_parent(self):
        def invalid_output_parent(command, cwd):
            request_path = Path(command[command.index("--request") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            root = Path(request["staging_root"])
            output_dir = root / "scenes" / "shot-01"
            output_dir.mkdir(parents=True)
            (output_dir / "frames").write_bytes(b"not a directory")
            (output_dir / "scene.blend").write_bytes(b"blend")
            response = {
                "record_type": "blender-scenes-worker-result", "worker_version": 1,
                "blender_version": [5, 2, 3], "blender_version_string": "5.2.3",
                "project_id": "test-project", "scene_id": "shot-01", "frames": list(range(24)),
                "outputs": ["scenes/shot-01/scene.blend"] + [
                    f"scenes/shot-01/frames/frame-{frame:06d}.png" for frame in range(24)
                ],
                "minimum_camera_target_distance_m": 4.0, "render_seconds": 1.0, "worker_seconds": 1.2,
            }
            (root / "response.json").write_text(json.dumps(response), encoding="utf-8")

        with mock.patch.object(pipeline, "_run_blender_process", side_effect=invalid_output_parent):
            with self.assertRaisesRegex(ValueError, "output parent is absent or unsafe"):
                pipeline._run_blender_scenes_worker(self.config_path, self.runtime)

    def test_worker_process_failure_does_not_complete_stage_02_or_publish_zip(self):
        with mock.patch.object(pipeline, "_run_blender_process", side_effect=ValueError("exit code 1")):
            with self.assertRaisesRegex(ValueError, "staged files remain untrusted"):
                pipeline._run_blender_scenes_worker(self.config_path, self.runtime)
        self.assertFalse((self.root / ".pipeline" / "work" / "scenes.zip").exists())
        manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        self.assertEqual("pending", manifest["stages"]["02-blender-scenes"]["status"])


class BlenderProcessSupervisorTests(unittest.TestCase):
    def windows_job_patch(self):
        if os.name == "nt":
            return mock.patch.object(pipeline, "_attach_windows_job", return_value=None)
        return nullcontext()

    def test_fixed_process_launch_is_shell_free_and_uses_new_process_group(self):
        process = mock.Mock()
        process.wait.return_value = 0
        with self.windows_job_patch(), mock.patch.object(pipeline.subprocess, "Popen", return_value=process) as popen:
            pipeline._run_blender_process(["/trusted/blender", "--background"], Path.cwd())
        args, kwargs = popen.call_args
        self.assertEqual(["/trusted/blender", "--background"], args[0])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdout"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stderr"])
        if os.name != "nt":
            self.assertTrue(kwargs["start_new_session"])
        else:
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)

    def test_timeout_and_cancel_terminate_the_process_tree_without_retry(self):
        process = mock.Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired(["blender"], 1), 1]
        with (
            self.windows_job_patch(),
            mock.patch.object(pipeline.subprocess, "Popen", return_value=process),
            mock.patch.object(pipeline, "_terminate_blender_process_tree") as terminate,
        ):
            with self.assertRaisesRegex(ValueError, "timeout; no retry"):
                pipeline._run_blender_process(["blender"], Path.cwd())
        terminate.assert_called_once_with(process, None)

        process = mock.Mock()
        process.wait.side_effect = [KeyboardInterrupt(), 1]
        with (
            self.windows_job_patch(),
            mock.patch.object(pipeline.subprocess, "Popen", return_value=process),
            mock.patch.object(pipeline, "_terminate_blender_process_tree") as terminate,
        ):
            with self.assertRaisesRegex(ValueError, "cancelled; no retry"):
                pipeline._run_blender_process(["blender"], Path.cwd())
        terminate.assert_called_once_with(process, None)

    def test_nonzero_worker_exit_is_controlled_and_never_retried(self):
        process = mock.Mock()
        process.wait.return_value = 7
        with self.windows_job_patch(), mock.patch.object(pipeline.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(ValueError, "exit code 7; no retry"):
                pipeline._run_blender_process(["blender"], Path.cwd())


if __name__ == "__main__":
    unittest.main()
