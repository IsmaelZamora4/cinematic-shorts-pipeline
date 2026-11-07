import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_pipeline import ROOT, load_pipeline, make_config


pipeline = load_pipeline()


def make_valid_scene_spec(project_id="test-project", scene_id="shot-01"):
    return {
        "scene_spec_version": 1,
        "project_id": project_id,
        "scene_id": scene_id,
        "render": {
            "width": 540, "height": 960, "fps": 12, "start_frame": 0,
            "end_frame_exclusive": 24, "engine": "CYCLES", "device": "CPU",
            "samples": 8, "threads": 2, "seed": 17, "denoising": False,
            "image_format": "PNG_RGB8_SRGB",
        },
        "materials": [{
            "id": "bronze", "base_color_linear_rgba": [0.6, 0.25, 0.08, 1.0],
            "metallic": 0.35, "roughness": 0.4,
        }],
        "objects": [{
            "id": "orb", "primitive": "uv_sphere", "material_id": "bronze",
            "location_m": [0.0, 0.0, 0.0], "rotation_rad": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
        }],
        "camera": {
            "lens_mm": 35, "interpolation": "LINEAR",
            "keyframes": [
                {"frame": 0, "location_m": [0.0, -5.0, 2.0], "target_m": [0.0, 0.0, 0.0]},
                {"frame": 23, "location_m": [0.1, -4.8, 2.0], "target_m": [0.0, 0.0, 0.0]},
            ],
        },
        "lights": [{
            "id": "key", "type": "AREA", "location_m": [2.0, -3.0, 4.0],
            "target_m": [0.0, 0.0, 0.0], "color_linear_rgb": [1.0, 0.82, 0.64],
            "power_w": 350, "size_m": 2.0,
        }],
    }


def make_scene_storyboard():
    storyboard = json.loads((ROOT / "templates" / "storyboard.json").read_text(encoding="utf-8"))
    storyboard["fps"] = 12
    storyboard["total_frames"] = 24
    storyboard["voiceover"]["duration_ms"] = 1800
    scene = dict(storyboard["scenes"][1])
    scene.update(id="shot-01", start_frame=0, end_frame=24)
    storyboard["scenes"] = [scene]
    return storyboard


def make_scenes_config(root):
    config = make_config(root)
    storyboard_path = Path(root) / "storyboard.json"
    storyboard = make_scene_storyboard()
    storyboard_path.write_text(json.dumps(storyboard, indent=2), encoding="utf-8")
    config["profile"]["fps"] = 12
    config["project"]["resources"] = [{
        "id": "scene-spec",
        "path": "scene-spec.json",
        "required": True,
        "kind": "blender-scene-spec",
    }]
    (Path(root) / "scene-spec.json").write_text(
        json.dumps(make_valid_scene_spec(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validation = next(stage for stage in config["stages"] if stage["id"] == "01-validate-project")
    validation["inputs"] = ["pipeline-config", "storyboard", "resource-scene-spec"]
    scenes = next(stage for stage in config["stages"] if stage["id"] == "02-blender-scenes")
    scenes.update(
        adapter="blender-scenes",
        adapter_status="pending",
        depends_on=["01-validate-project"],
        inputs=["storyboard", "validated-plan", "resource-scene-spec"],
        outputs=[{"id": "scene-package", "path": ".pipeline/work/scenes.zip"}],
        tools=["blender"],
    )
    return config


def make_combined_config(root):
    config = make_scenes_config(root)
    config["project"]["resources"].append({
        "id": "voiceover-script", "path": "voiceover-script.txt", "required": True,
        "kind": "voiceover-script",
    })
    (Path(root) / "voiceover-script.txt").write_text("Una prueba de narración.\n", encoding="utf-8")
    validation = next(stage for stage in config["stages"] if stage["id"] == "01-validate-project")
    validation["inputs"].append("resource-voiceover-script")
    blender_stage = next(stage for stage in config["stages"] if stage["id"] == "02-blender-scenes")
    blender_stage["adapter_status"] = "implemented"
    narration = next(stage for stage in config["stages"] if stage["id"] == "03-kokoro-narration")
    narration.update(
        id="03-tts-narration", adapter="tts-narration", adapter_status="implemented",
        depends_on=["01-validate-project"],
        inputs=["validated-plan", "resource-voiceover-script"],
        outputs=[
            {"id": "narration", "path": ".pipeline/work/narration.wav"},
            {"id": "narration-provenance", "path": ".pipeline/work/narration-provenance.json"},
        ], tools=["kokoro"],
        tts={"provider": "kokoro", "voice": "em_alex", "language": "es",
             "audio": {"container": "wav", "sample_rate_hz": 24000, "channels": 1,
                       "sample_format": "pcm_s16le"},
             "script_input": "resource-voiceover-script"},
    )
    compose = next(stage for stage in config["stages"] if stage["id"] == "04-remotion-compose")
    compose["depends_on"] = ["02-blender-scenes", "03-tts-narration"]
    for tool in config["tools"]:
        if tool["id"] in {"blender", "kokoro"}:
            tool.update(availability="verified", adapter_status="implemented")
            if tool["id"] == "kokoro":
                tool["version_requirement"] = "kokoro-onnx==0.6.1; Python>=3.10,<3.14; CPUExecutionProvider"
    return config


def add_extra_stage(config, adapter_status="pending"):
    config["stages"].append({
        "id": "07-extra", "adapter": "extra-worker", "adapter_status": adapter_status,
        "depends_on": ["06-quality-control"], "inputs": ["storyboard"],
        "outputs": [{"id": "extra-output", "path": ".pipeline/work/extra.json"}], "tools": [],
    })


class ProductiveScenesProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        self.config = make_scenes_config(self.root)
        self.config_path = self.root / "pipeline.json"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def errors(self):
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        return errors

    def fake_blender_executable(self):
        path = self.root / ("blender.exe" if os.name == "nt" else "blender")
        path.write_bytes(b"fake Blender executable")
        if os.name != "nt":
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path.resolve()

    @staticmethod
    def successful_blender_version_probe(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="Blender 5.2.3\n", stderr="")

    def test_accepts_stage_01_02_profile_and_keeps_following_stages_pending(self):
        loaded, errors, warnings, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertTrue(pipeline._is_productive_scenes_config(loaded))
        self.assertEqual(1, loaded["pipeline_version"])
        self.assertEqual([["01-validate-project"], ["02-blender-scenes", "03-kokoro-narration"], ["04-remotion-compose"], ["05-ffmpeg-finalize"], ["06-quality-control"]], waves)
        self.assertEqual(
            {"pending"},
            {stage["adapter_status"] for stage in loaded["stages"] if stage["id"] != "01-validate-project"},
        )

    def test_classifies_complete_independent_blender_profile(self):
        scenes = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scenes["adapter_status"] = "implemented"
        blender = next(tool for tool in self.config["tools"] if tool["id"] == "blender")
        blender.update(adapter_status="implemented", availability="verified")
        self.write_config()
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual("blender-independent", pipeline._classify_productive_profile(loaded))

    def test_rejects_implemented_extra_stage_in_productive_blender_profile(self):
        scenes = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scenes["adapter_status"] = "implemented"
        next(tool for tool in self.config["tools"] if tool["id"] == "blender").update(
            adapter_status="implemented", availability="verified")
        add_extra_stage(self.config, "implemented")
        self.write_config()
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertIsNone(pipeline._classify_productive_profile(loaded))
        self.assertTrue(any("productive profile" in error for error in errors), errors)

    def test_accepts_pending_extra_stage_in_historical_declarative_v1_config(self):
        add_extra_stage(self.config)
        self.write_config()
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertTrue(pipeline._is_productive_scenes_config(loaded))

    def test_rejects_malformed_extra_stage_status_with_controlled_validation_error(self):
        add_extra_stage(self.config, [])
        self.write_config()
        _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertTrue(any("adapter_status must be 'pending' or 'implemented'" in error for error in errors), errors)

    def test_rejects_non_string_extra_stage_ids_without_uncaught_exceptions(self):
        for invalid_id in ([], {}, None, 123, ["07-extra"]):
            with self.subTest(invalid_id=invalid_id):
                self.config = make_scenes_config(self.root)
                add_extra_stage(self.config)
                self.config["stages"][-1]["id"] = invalid_id
                self.write_config()
                try:
                    _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
                except Exception as exc:
                    self.fail(f"invalid stage id raised {type(exc).__name__}: {exc}")
                self.assertTrue(errors)

    def test_validate_manifest_file_rejects_non_string_extra_stage_ids_without_exception(self):
        for invalid_id in ([], {}, None, 123, ["07-extra"]):
            with self.subTest(invalid_id=invalid_id):
                self.config = make_scenes_config(self.root)
                manifest = pipeline.new_pending_manifest(self.config, self.config_path)
                manifest_path = self.root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                add_extra_stage(self.config)
                self.config["stages"][-1]["id"] = invalid_id
                self.write_config()
                try:
                    errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
                except Exception as exc:
                    self.fail(f"invalid stage id raised {type(exc).__name__}: {exc}")
                self.assertTrue(errors)

    def test_rejects_mixed_and_malformed_profile_inputs_without_uncaught_exceptions(self):
        cases = (
            ("stage01", [{"not": "hashable"}]),
            ("stage02", [[]]),
            ("stage03", [None]),
        )
        for target, malformed_inputs in cases:
            with self.subTest(target=target):
                self.config = make_combined_config(self.root)
                stage_id = {
                    "stage01": "01-validate-project",
                    "stage02": "02-blender-scenes",
                    "stage03": "03-tts-narration",
                }[target]
                stage = next(item for item in self.config["stages"] if item["id"] == stage_id)
                stage["inputs"] = malformed_inputs
                self.write_config()
                self.assertIsNone(pipeline._classify_productive_profile(self.config))
                _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
                self.assertTrue(errors)

    def test_rejects_missing_fields_and_wrong_profile_input_types_without_tracebacks(self):
        cases = (
            ("01-validate-project", "inputs", None),
            ("02-blender-scenes", "inputs", {"storyboard": True}),
            ("03-tts-narration", "tts", []),
        )
        for stage_id, key, value in cases:
            with self.subTest(stage=stage_id, key=key):
                self.config = make_combined_config(self.root)
                stage = next(item for item in self.config["stages"] if item["id"] == stage_id)
                if value is None:
                    stage.pop(key, None)
                else:
                    stage[key] = value
                self.write_config()
                self.assertIsNone(pipeline._classify_productive_profile(self.config))
                _, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
                self.assertTrue(errors)

    def test_rejects_partial_and_ambiguous_profile_classification(self):
        self.config = make_combined_config(self.root)
        scene = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scene["adapter_status"] = "pending"
        self.assertIsNone(pipeline._classify_productive_profile(self.config))
        self.write_config()
        self.assertTrue(self.errors())

        self.config = make_scenes_config(self.root)
        next(tool for tool in self.config["tools"] if tool["id"] == "blender")["adapter_status"] = "implemented"
        self.assertIsNone(pipeline._classify_productive_profile(self.config))
        self.write_config()
        self.assertTrue(self.errors())

        self.config = make_combined_config(self.root)
        scene = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scene["adapter"] = "unexpected-scenes-adapter"
        self.assertIsNone(pipeline._classify_productive_profile(self.config))
        self.write_config()
        self.assertTrue(self.errors())

        self.config = make_combined_config(self.root)
        self.config["stages"].pop()
        self.assertIsNone(pipeline._classify_productive_profile(self.config))
        self.write_config()
        self.assertTrue(self.errors())

    def test_accepts_complete_combined_declaration_without_enabling_stage_04(self):
        self.config = make_combined_config(self.root)
        self.write_config()
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertEqual("audiovisual-combined", pipeline._classify_productive_profile(loaded))
        statuses = {stage["id"]: stage["adapter_status"] for stage in loaded["stages"]}
        self.assertEqual("implemented", statuses["02-blender-scenes"])
        self.assertEqual("implemented", statuses["03-tts-narration"])
        self.assertEqual("pending", statuses["04-remotion-compose"])

    def test_rejects_implemented_extra_stage_in_combined_profile(self):
        self.config = make_combined_config(self.root)
        add_extra_stage(self.config, "implemented")
        self.write_config()
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertIsNone(pipeline._classify_productive_profile(loaded))
        self.assertTrue(any("productive profile" in error for error in errors), errors)

    def test_rejects_combined_declaration_with_stage_04_enabled(self):
        self.config = make_combined_config(self.root)
        next(stage for stage in self.config["stages"] if stage["id"] == "04-remotion-compose")["adapter_status"] = "implemented"
        self.write_config()
        self.assertTrue(self.errors())

    def test_rejects_stage_02_additional_dependency(self):
        scenes = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scenes["depends_on"].append("03-kokoro-narration")
        self.write_config()
        self.assertTrue(any("02-blender-scenes" in error and "depend exclusively" in error for error in self.errors()))

    def test_rejects_missing_or_unknown_stage_02_inputs_and_resources(self):
        scenes = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scenes["inputs"] = ["storyboard", "resource-undeclared"]
        self.write_config()
        errors = self.errors()
        self.assertTrue(any("validated-plan" in error for error in errors), errors)
        self.assertTrue(any("unknown input or artifact" in error for error in errors), errors)

    def test_requires_one_required_scene_spec_resource_explicitly_in_stage_02(self):
        self.config["project"]["resources"][0]["required"] = False
        self.write_config()
        self.assertTrue(any("required blender-scene-spec" in error for error in self.errors()))

    def test_rejects_profile_when_scene_spec_resource_is_missing(self):
        self.config["project"]["resources"] = []
        self.write_config()
        errors = self.errors()
        self.assertTrue(any("productive scene profile requires exactly one" in error for error in errors), errors)
        self.assertTrue(any("unknown input or artifact: resource-scene-spec" in error for error in errors), errors)

    def test_rejects_scene_spec_resource_with_altered_kind(self):
        self.config["project"]["resources"][0]["kind"] = "generic-json"
        self.write_config()
        self.assertTrue(any("requires exactly one required blender-scene-spec" in error for error in self.errors()))

    def test_rejects_scene_profile_with_altered_stage_02_adapter(self):
        next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")["adapter"] = "generic-renderer"
        self.write_config()
        self.assertTrue(any("productive scene profile Stage 02 adapter must be blender-scenes" in error for error in self.errors()))

    def test_rejects_implemented_stage_02_without_scene_profile_contract(self):
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        config = make_config(self.root)
        config["profile"]["fps"] = 12
        next(stage for stage in config["stages"] if stage["id"] == "02-blender-scenes")["adapter_status"] = "implemented"
        self.config = config
        self.write_config()
        self.assertTrue(any("productive scene profile" in error for error in self.errors()))

    def test_implemented_blender_zip_cannot_bypass_scene_profile_by_renaming_output(self):
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        config = make_config(self.root)
        stage = next(item for item in config["stages"] if item["id"] == "02-blender-scenes")
        stage["adapter_status"] = "implemented"
        stage["outputs"] = [{"id": "other-package", "path": ".pipeline/work/scenes.zip"}]
        self.config = config
        self.write_config()
        self.assertTrue(any("productive scene profile" in error for error in self.errors()))

    def test_rejects_ambiguous_multiple_scene_spec_declarations(self):
        second = dict(self.config["project"]["resources"][0], id="scene-spec-secondary", path="scene-spec-secondary.json")
        self.config["project"]["resources"].append(second)
        (self.root / second["path"]).write_text('{"scene_spec_version":1}\n', encoding="utf-8")
        validation = next(stage for stage in self.config["stages"] if stage["id"] == "01-validate-project")
        validation["inputs"].append("resource-scene-spec-secondary")
        self.write_config()
        self.assertTrue(any("requires exactly one required blender-scene-spec" in error for error in self.errors()))

    def test_legacy_declarative_blender_config_is_not_misclassified(self):
        shutil.copyfile(ROOT / "templates" / "storyboard.json", self.root / "storyboard.json")
        legacy = make_config(self.root)
        legacy["project"]["resources"] = [
            {"id": "geometry", "path": "geometry.json", "required": True, "kind": "generic-json"},
            {"id": "scene-spec", "path": "scene-spec.json", "required": True, "kind": "application-json"},
        ]
        (self.root / "geometry.json").write_text("{}\n", encoding="utf-8")
        (self.root / "scene-spec.json").write_text("{}\n", encoding="utf-8")
        next(stage for stage in legacy["stages"] if stage["id"] == "02-blender-scenes")["inputs"].append("resource-geometry")
        legacy_path = self.root / "legacy-pipeline.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        loaded, errors, _, _ = pipeline.load_and_validate_config(legacy_path)
        self.assertEqual([], errors)
        self.assertFalse(pipeline._intends_productive_scenes_config(loaded))
        self.assertFalse(pipeline._is_productive_scenes_config(loaded))

    def test_rejects_profile_or_storyboard_outside_the_fixed_mvp_timeline(self):
        self.config["profile"]["fps"] = 30
        self.write_config()
        self.assertTrue(any("540x960 at 12 FPS" in error for error in self.errors()))
        self.config["profile"]["fps"] = 12
        storyboard_path = self.root / "storyboard.json"
        storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
        storyboard["total_frames"] = 25
        storyboard_path.write_text(json.dumps(storyboard), encoding="utf-8")
        self.write_config()
        self.assertTrue(any("one storyboard scene covering [0,24)" in error for error in self.errors()))

    def test_rejects_unverified_stage_02_or_downstream_adapter_activation(self):
        scenes = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        scenes["adapter_status"] = "implemented"
        self.write_config()
        self.assertTrue(any("verified implemented blender" in error for error in self.errors()))

        scenes["adapter_status"] = "pending"
        next(stage for stage in self.config["stages"] if stage["id"] == "03-kokoro-narration")["adapter_status"] = "implemented"
        self.write_config()
        self.assertTrue(any("remain pending" in error for error in self.errors()))

    def test_productive_scene_profile_requires_verified_implemented_blender(self):
        scenes = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        blender = next(tool for tool in self.config["tools"] if tool["id"] == "blender")
        scenes["adapter_status"] = "implemented"
        for availability, status in (("not_checked", "pending"), ("verified", "pending"), ("not_checked", "implemented")):
            with self.subTest(availability=availability, status=status):
                blender.update(availability=availability, adapter_status=status)
                self.write_config()
                self.assertTrue(any("verified implemented blender" in error for error in self.errors()))
        blender.update(availability="verified", adapter_status="implemented")
        self.write_config()
        loaded, errors, _, _ = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        self.assertTrue(pipeline._is_productive_scenes_config(loaded))
        self.assertEqual("pending", next(stage for stage in loaded["stages"] if stage["id"] == "03-kokoro-narration")["adapter_status"])

    def test_rejects_ineligible_stage_01_contract(self):
        validation = next(stage for stage in self.config["stages"] if stage["id"] == "01-validate-project")
        validation["inputs"].remove("pipeline-config")
        self.write_config()
        self.assertTrue(any("productive scene profile" in error for error in self.errors()))

    def test_config_changes_invalidate_stage_01_and_validated_plan_fingerprint(self):
        loaded, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        before_hashes = pipeline._project_input_hashes(loaded, self.config_path)
        before_plan = pipeline._validated_plan(loaded, self.config_path, waves, [], before_hashes)
        before_stage = before_plan["stage_fingerprint"]
        self.config["profile"]["limits"]["memory_mb"] = 8193
        self.write_config()
        updated, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        after_hashes = pipeline._project_input_hashes(updated, self.config_path)
        after_plan = pipeline._validated_plan(updated, self.config_path, waves, [], after_hashes)
        self.assertNotEqual(before_hashes["pipeline-config"], after_hashes["pipeline-config"])
        self.assertNotEqual(before_stage, after_plan["stage_fingerprint"])
        self.assertNotEqual(before_plan["config_fingerprint"], after_plan["config_fingerprint"])

    def _completed_stage_01_manifest(self):
        loaded, errors, _, waves = pipeline.load_and_validate_config(self.config_path)
        self.assertEqual([], errors)
        input_hashes = pipeline._project_input_hashes(loaded, self.config_path)
        input_entries = [
            {"id": "resource-scene-spec", "path": "scene-spec.json", "size_bytes": (self.root / "scene-spec.json").stat().st_size,
             "sha256": input_hashes["resource-scene-spec"]},
            {"id": "storyboard", "path": "storyboard.json", "size_bytes": (self.root / "storyboard.json").stat().st_size,
             "sha256": input_hashes["storyboard"]},
        ]
        plan = pipeline._validated_plan(loaded, self.config_path, waves, input_entries, input_hashes)
        plan_path = self.root / ".pipeline" / "work" / "validated-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_bytes = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        plan_path.write_bytes(plan_bytes)
        manifest = pipeline.new_pending_manifest(loaded, self.config_path)
        validation = next(stage for stage in loaded["stages"] if stage["id"] == "01-validate-project")
        manifest["stages"][validation["id"]].update(
            status="completed", attempts=1,
            stage_fingerprint=pipeline.stage_fingerprint(
                loaded, validation, {key: input_hashes[key] for key in validation["inputs"]}, {}
            ),
            artifacts=[{"id": "validated-plan", "path": validation["outputs"][0]["path"], "sha256": hashlib.sha256(plan_bytes).hexdigest()}],
        )
        return manifest, plan_path

    def test_validate_manifest_reverifies_full_dag_plan_with_stage_03_pending(self):
        manifest, plan_path = self._completed_stage_01_manifest()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(6, len(plan["stages"]))
        self.assertEqual("pending", next(stage for stage in plan["stages"] if stage["id"] == "03-kokoro-narration")["adapter_status"])
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        errors, warnings = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_validate_manifest_invalidates_plan_when_storyboard_or_resource_changes(self):
        changed_storyboard = make_scene_storyboard()
        changed_storyboard["title"] = "Changed valid title"
        for relative_path, replacement in (
            ("storyboard.json", json.dumps(changed_storyboard)),
            ("scene-spec.json", json.dumps({**make_valid_scene_spec(), "materials": [dict(make_valid_scene_spec()["materials"][0], roughness=0.45)]})),
        ):
            with self.subTest(path=relative_path):
                (self.root / "storyboard.json").write_text(json.dumps(make_scene_storyboard(), indent=2), encoding="utf-8")
                (self.root / "scene-spec.json").write_text(
                    json.dumps(make_valid_scene_spec(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                manifest, _ = self._completed_stage_01_manifest()
                (self.root / relative_path).write_text(replacement, encoding="utf-8")
                manifest_path = self.root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
                self.assertTrue(any("validated-plan does not match current project semantics" in error for error in errors), errors)

    def test_validate_manifest_invalidates_plan_when_dag_changes(self):
        manifest, _ = self._completed_stage_01_manifest()
        next(stage for stage in self.config["stages"] if stage["id"] == "03-kokoro-narration")["adapter"] = "renamed-kokoro"
        self.write_config()
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("validated-plan does not match current project semantics" in error for error in errors), errors)

    def test_validate_manifest_rejects_tampered_plan_even_when_artifact_hash_is_recalculated(self):
        manifest, plan_path = self._completed_stage_01_manifest()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["stages"] = plan["stages"][:-1]
        tampered = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        plan_path.write_bytes(tampered)
        manifest["stages"]["01-validate-project"]["artifacts"][0]["sha256"] = hashlib.sha256(tampered).hexdigest()
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("validated-plan does not match current project semantics" in error for error in errors), errors)

    def test_stage_02_completed_state_cannot_pass_manifest_while_adapter_pending(self):
        manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        stage = manifest["stages"]["02-blender-scenes"]
        stage.update(status="completed", attempts=1, stage_fingerprint="0" * 64,
                     artifacts=[{"id": "scene-package", "path": ".pipeline/work/scenes.zip", "sha256": "0" * 64}],
                     tool_versions={"blender": "5.2.3"})
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("cannot be completed while its adapter is pending" in error for error in errors), errors)

    def test_manifest_cannot_accept_completed_stage_02_after_contract_markers_are_altered(self):
        stage = next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")
        stage["adapter_status"] = "implemented"
        stage["adapter"] = "other-renderer"
        self.write_config()
        manifest = pipeline.new_pending_manifest(self.config, self.config_path)
        manifest["stages"]["02-blender-scenes"].update(
            status="completed", attempts=1, stage_fingerprint="0" * 64,
            artifacts=[{"id": "scene-package", "path": ".pipeline/work/scenes.zip", "sha256": "0" * 64}],
            tool_versions={"blender": "5.2.3"},
        )
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        errors, _ = pipeline.validate_manifest_file(self.config_path, manifest_path)
        self.assertTrue(any("Stage 02 adapter must be blender-scenes" in error for error in errors), errors)

    def test_cli_rejects_legacy_pending_profile_without_writes_or_processes(self):
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        blender_path = self.fake_blender_executable()
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        output = io.StringIO()
        with (
            mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=self.successful_blender_version_probe) as probe,
            mock.patch.object(pipeline, "_run_blender_scenes_worker", side_effect=AssertionError("worker must not start")),
            contextlib.redirect_stdout(output),
        ):
            result = pipeline.main(["run-productive-scenes", str(self.config_path), "--blender", str(blender_path)])
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(1, result)
        self.assertIn("requires the implemented productive", output.getvalue())
        self.assertEqual(0, probe.call_count)
        self.assertEqual(before, after)

    def test_cli_rejects_relative_blender_path_without_starting_anything(self):
        next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")["adapter_status"] = "implemented"
        next(tool for tool in self.config["tools"] if tool["id"] == "blender").update(
            adapter_status="implemented", availability="verified")
        self.write_config()
        output = io.StringIO()
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=AssertionError("Blender must not start")), contextlib.redirect_stdout(output):
            result = pipeline.main(["run-productive-scenes", str(self.config_path), "--blender", "blender"])
        self.assertEqual(1, result)
        self.assertIn("must be an absolute path", output.getvalue())

    def test_cli_rejects_inconsistent_profile_before_adapter_unavailable_response(self):
        next(stage for stage in self.config["stages"] if stage["id"] == "02-blender-scenes")["adapter"] = "changed-adapter"
        self.write_config()
        blender_path = self.fake_blender_executable()
        output = io.StringIO()
        with mock.patch.object(pipeline, "_run_blender_version_probe", side_effect=AssertionError("Blender must not start")), contextlib.redirect_stdout(output):
            result = pipeline.main(["run-productive-scenes", str(self.config_path), "--blender", str(blender_path)])
        self.assertEqual(1, result)
        self.assertIn("invalid productive scenes profile", output.getvalue())
        self.assertNotIn("execution is not available", output.getvalue())


if __name__ == "__main__":
    unittest.main()
