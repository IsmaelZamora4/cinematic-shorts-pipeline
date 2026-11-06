import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_pipeline import ROOT, load_pipeline
from test_productive_scenes_profile import make_scene_storyboard, make_scenes_config, make_valid_scene_spec
from test_productive_tts_execution import make_productive_config


pipeline = load_pipeline()


class BlenderSceneSpecValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "storyboard.json").write_text(json.dumps(make_scene_storyboard()), encoding="utf-8")
        self.config = make_scenes_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        self.spec_path = self.root / "scene-spec.json"
        self.write_spec()

    def tearDown(self):
        self.temp.cleanup()

    def write_spec(self, spec=None):
        spec = make_valid_scene_spec() if spec is None else spec
        self.spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def errors(self):
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        return errors

    def assert_spec_rejected(self, mutate, message=None):
        spec = copy.deepcopy(make_valid_scene_spec())
        mutate(spec)
        self.write_spec(spec)
        errors = self.errors()
        self.assertTrue(errors, "invalid scene spec unexpectedly validated")
        if message:
            self.assertTrue(any(message in error for error in errors), errors)

    def test_accepts_complete_scene_spec_v1_and_validates_mvp_profile(self):
        loaded, errors, warnings, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertTrue(pipeline._is_productive_scenes_config(loaded))

    def test_committed_example_pipeline_storyboard_and_scene_spec_validate_together(self):
        example = ROOT / "examples" / "blender-scene-spec-v1"
        config, errors, warnings, _ = pipeline.load_and_validate_config(example / "pipeline.json")
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertTrue(pipeline._is_productive_scenes_config(config))

    def test_productive_kokoro_profile_remains_eligible_and_not_a_scene_profile(self):
        root = self.root / "kokoro"
        root.mkdir()
        (root / "storyboard.json").write_text(
            (ROOT / "templates" / "storyboard.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        config = make_productive_config(root)
        config_path = root / "pipeline.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        loaded, errors, warnings, _ = pipeline.load_and_validate_config(config_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertFalse(pipeline._intends_productive_scenes_config(loaded))
        self.assertFalse(pipeline._is_productive_scenes_config(loaded))

    def test_accepts_numeric_range_endpoints_and_maximum_collection_counts(self):
        spec = make_valid_scene_spec()
        spec["render"]["seed"] = 0
        material = spec["materials"][0]
        material["base_color_linear_rgba"] = [0, 1, 0, 1]
        material["metallic"] = 0
        material["roughness"] = 0.05
        spec["objects"][0]["location_m"] = [-20, 20, 0]
        spec["objects"][0]["rotation_rad"] = [-6.28319, 6.28319, 0]
        spec["objects"][0]["scale"] = [0.05, 10, 1]
        spec["camera"]["lens_mm"] = 24
        spec["lights"][0].update(
            location_m=[20, -20, 1], color_linear_rgb=[0, 1, 0.5], power_w=1, size_m=0.1,
        )
        spec["materials"] = [dict(material, id=f"material-{index}") for index in range(12)]
        spec["objects"] = [
            dict(spec["objects"][0], id=f"object-{index}", material_id="material-0")
            for index in range(24)
        ]
        spec["lights"] = [dict(spec["lights"][0], id=f"light-{index}") for index in range(3)]
        self.write_spec(spec)
        self.assertEqual([], self.errors())

        upper = copy.deepcopy(spec)
        upper["render"]["seed"] = 2147483647
        upper["materials"][0].update(metallic=1, roughness=1)
        upper["camera"]["lens_mm"] = 50
        upper["lights"][0].update(power_w=2000, size_m=10)
        self.write_spec(upper)
        self.assertEqual([], self.errors())

    def test_rejects_unknown_version_project_and_scene_ids(self):
        self.assert_spec_rejected(lambda spec: spec.update(scene_spec_version=2), "scene_spec_version")
        self.assert_spec_rejected(lambda spec: spec.update(scene_spec_version=True), "scene_spec_version")
        self.assert_spec_rejected(lambda spec: spec.update(project_id="another-project"), "project_id")
        self.assert_spec_rejected(lambda spec: spec.update(scene_id="other-shot"), "scene_id")
        self.assert_spec_rejected(lambda spec: spec.update(scene_id="not a slug"), "scene_id")

    def test_rejects_missing_unknown_and_wrongly_typed_fields(self):
        self.assert_spec_rejected(lambda spec: spec.pop("lights"), "missing required field")
        self.assert_spec_rejected(lambda spec: spec.update(extra="ignored"), "unknown field")
        self.assert_spec_rejected(lambda spec: spec["render"].update(extra=1), "unknown field")
        self.assert_spec_rejected(lambda spec: spec["objects"][0].update(extra=1), "unknown field")
        self.assert_spec_rejected(lambda spec: spec.update(render=[]), "render must be an object")
        self.assert_spec_rejected(lambda spec: spec.update(materials={}), "materials must be an array")

    def test_render_parameters_are_fixed_and_seed_is_bounded_integer(self):
        invalid_render_values = {
            "width": 539, "height": 961, "fps": 11, "start_frame": 1,
            "end_frame_exclusive": 23, "engine": "BLENDER_EEVEE_NEXT", "device": "GPU",
            "samples": 7, "threads": 3, "denoising": True, "image_format": "PNG_RGBA16",
        }
        for key, value in invalid_render_values.items():
            with self.subTest(field=key):
                self.assert_spec_rejected(lambda spec, k=key, v=value: spec["render"].update({k: v}), f"render.{key}")
        for seed in (-1, 2147483648, 1.5, True):
            with self.subTest(seed=seed):
                self.assert_spec_rejected(lambda spec, value=seed: spec["render"].update(seed=value), "render.seed")
        self.assert_spec_rejected(lambda spec: spec["render"].update(fps=True), "render.fps")
        spec = make_valid_scene_spec()
        spec["render"]["seed"] = 2147483647
        self.write_spec(spec)
        self.assertEqual([], self.errors())

    def test_material_count_shape_ids_and_numeric_ranges_are_strict(self):
        for materials in ([], [make_valid_scene_spec()["materials"][0]] * 13):
            with self.subTest(count=len(materials)):
                self.assert_spec_rejected(lambda spec, value=materials: spec.update(materials=value), "materials")
        self.assert_spec_rejected(lambda spec: spec["materials"][0].update(id="Bad ID"), "materials[0].id")
        self.assert_spec_rejected(
            lambda spec: spec["materials"].append(copy.deepcopy(spec["materials"][0])), "duplicate id"
        )
        for field, value in (
            ("base_color_linear_rgba", [0, 0, 0]),
            ("base_color_linear_rgba", [-0.01, 0, 0, 1]),
            ("base_color_linear_rgba", [0, 0, 1.01, 1]),
            ("metallic", -0.01), ("metallic", 1.01), ("metallic", True),
            ("roughness", 0.049), ("roughness", 1.01),
        ):
            with self.subTest(field=field, value=value):
                self.assert_spec_rejected(lambda spec, k=field, v=value: spec["materials"][0].update({k: v}), f"materials[0].{field}")

    def test_object_primitives_references_vectors_and_numeric_ranges_are_strict(self):
        for primitive in ("cone", "cube.py", "UV_SPHERE"):
            with self.subTest(primitive=primitive):
                self.assert_spec_rejected(lambda spec, value=primitive: spec["objects"][0].update(primitive=value), "primitive")
        self.assert_spec_rejected(lambda spec: spec["objects"][0].update(material_id="missing"), "material_id")
        self.assert_spec_rejected(lambda spec: spec["objects"][0].update(id="bad id"), "objects[0].id")
        self.assert_spec_rejected(
            lambda spec: spec["objects"].append(copy.deepcopy(spec["objects"][0])), "duplicate id"
        )
        self.assert_spec_rejected(
            lambda spec: spec["objects"][0].update(id=spec["materials"][0]["id"]), "duplicate id"
        )
        for field, value in (
            ("location_m", [0, 0]), ("location_m", [20.001, 0, 0]),
            ("rotation_rad", [0, 0, 6.2832]), ("scale", [0.049, 1, 1]),
            ("scale", [1, 10.001, 1]), ("scale", [True, 1, 1]),
        ):
            with self.subTest(field=field, value=value):
                self.assert_spec_rejected(lambda spec, k=field, v=value: spec["objects"][0].update({k: v}), f"objects[0].{field}")
        self.assert_spec_rejected(lambda spec: spec.update(objects=[]), "objects must contain")
        self.assert_spec_rejected(
            lambda spec: spec.update(objects=[copy.deepcopy(spec["objects"][0]) for _ in range(25)]),
            "objects must contain",
        )

    def test_camera_requires_two_exact_frames_valid_distances_and_real_motion(self):
        for lens in (23.99, 50.01, True):
            with self.subTest(lens=lens):
                self.assert_spec_rejected(lambda spec, value=lens: spec["camera"].update(lens_mm=value), "camera.lens_mm")
        self.assert_spec_rejected(lambda spec: spec["camera"].update(interpolation="BEZIER"), "interpolation")
        self.assert_spec_rejected(lambda spec: spec["camera"].update(keyframes=[]), "two keyframes")
        self.assert_spec_rejected(
            lambda spec: spec["camera"]["keyframes"][0].update(frame=1), "frames 0 and 23"
        )
        self.assert_spec_rejected(
            lambda spec: spec["camera"]["keyframes"][0].update(location_m=[20.001, 0, 0]), "camera.keyframes[0].location_m"
        )
        self.assert_spec_rejected(
            lambda spec: spec["camera"]["keyframes"][0].update(target_m=[0, 0]), "camera.keyframes[0].target_m"
        )
        self.assert_spec_rejected(
            lambda spec: spec["camera"]["keyframes"][0].update(location_m=[0, 0, 0], target_m=[0.01, 0, 0]),
            "camera-target distance",
        )
        self.assert_spec_rejected(
            lambda spec: spec["camera"]["keyframes"][1].update(location_m=[0.005, -5, 2]),
            "camera must move",
        )
        exact_minimum_motion = make_valid_scene_spec()
        exact_minimum_motion["camera"]["keyframes"][0]["location_m"] = [0, -5, 2]
        exact_minimum_motion["camera"]["keyframes"][1]["location_m"] = [0.01, -5, 2]
        self.write_spec(exact_minimum_motion)
        self.assertEqual([], self.errors())

    def test_lights_count_type_targets_and_numeric_ranges_are_strict(self):
        for light_count in (0, 4):
            with self.subTest(count=light_count):
                self.assert_spec_rejected(
                    lambda spec, count=light_count: spec.update(lights=[copy.deepcopy(spec["lights"][0]) for _ in range(count)]),
                    "lights must contain",
                )
        self.assert_spec_rejected(lambda spec: spec["lights"][0].update(type="POINT"), "type")
        self.assert_spec_rejected(lambda spec: spec["lights"][0].update(id="bad id"), "lights[0].id")
        self.assert_spec_rejected(lambda spec: spec["lights"][0].update(target_m=[2, -3, 4]), "location and target")
        for field, value in (
            ("location_m", [20.01, 0, 0]), ("target_m", [0, 0, -20.01]),
            ("color_linear_rgb", [1, 0, -0.01]), ("color_linear_rgb", [1, 1, 1, 1]),
            ("power_w", 0.99), ("power_w", 2000.01), ("size_m", 0.099),
            ("size_m", 10.01), ("power_w", True),
        ):
            with self.subTest(field=field, value=value):
                self.assert_spec_rejected(lambda spec, k=field, v=value: spec["lights"][0].update({k: v}), f"lights[0].{field}")

    def test_rejects_duplicate_keys_nonstandard_numbers_invalid_utf8_and_oversize(self):
        valid_text = json.dumps(make_valid_scene_spec(), ensure_ascii=False)
        self.spec_path.write_text(valid_text.replace('"scene_spec_version": 1', '"scene_spec_version": 1, "scene_spec_version": 1'), encoding="utf-8")
        self.assertTrue(any("duplicate JSON key" in error for error in self.errors()))
        self.spec_path.write_text(valid_text.replace('"seed": 17', '"seed": NaN'), encoding="utf-8")
        self.assertTrue(any("non-standard JSON number" in error for error in self.errors()))
        self.spec_path.write_text(valid_text.replace('"seed": 17', '"seed": Infinity'), encoding="utf-8")
        self.assertTrue(any("non-standard JSON number" in error for error in self.errors()))
        self.spec_path.write_bytes(b"\xff\xfe")
        self.assertTrue(any("UTF-8" in error for error in self.errors()))
        self.spec_path.write_bytes(b"{" + b" " * pipeline.MAX_BLENDER_SCENE_SPEC_BYTES + b"}")
        self.assertTrue(any("maximum size" in error for error in self.errors()))

    def test_changed_valid_spec_invalidates_stage_01_manifest_semantics(self):
        config, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        input_hashes = pipeline._project_input_hashes(config, self.config_path)
        input_entries = [
            {"id": "resource-scene-spec", "path": "scene-spec.json", "size_bytes": self.spec_path.stat().st_size,
             "sha256": input_hashes["resource-scene-spec"]},
            {"id": "storyboard", "path": "storyboard.json", "size_bytes": (self.root / "storyboard.json").stat().st_size,
             "sha256": input_hashes["storyboard"]},
        ]
        plan = pipeline._validated_plan(config, self.config_path, waves, input_entries, input_hashes)
        plan_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        plan_path.parent.mkdir(parents=True)
        plan_bytes = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        plan_path.write_bytes(plan_bytes)
        manifest = pipeline.new_pending_manifest(config, self.config_path)
        stage01 = next(stage for stage in config["stages"] if stage["id"] == "01-validate-project")
        manifest["stages"][stage01["id"]].update(
            status="completed", attempts=1,
            stage_fingerprint=pipeline.stage_fingerprint(
                config, stage01, {key: input_hashes[key] for key in stage01["inputs"]}, {},
            ),
            artifacts=[{"id": "validated-plan", "path": stage01["outputs"][0]["path"], "sha256": hashlib.sha256(plan_bytes).hexdigest()}],
        )
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        modified = make_valid_scene_spec()
        modified["materials"][0]["roughness"] = 0.45
        self.write_spec(modified)
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("validated-plan does not match current project semantics" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
