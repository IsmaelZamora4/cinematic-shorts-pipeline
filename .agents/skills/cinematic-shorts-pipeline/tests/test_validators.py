import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


skill_validator = load_script("validate-skill.py")
story_validator = load_script("validate-storyboard.py")


class StoryboardValidationTests(unittest.TestCase):
    def setUp(self):
        self.valid = json.loads((ROOT / "examples" / "cave-exploration" / "storyboard.json").read_text(encoding="utf-8"))

    def valid_animation(self):
        return {
            "rig": {
                "status": "unselected",
                "profile": "Adult biped with root, gaze, feet, and hand controls.",
                "required_controls": ["root", "gaze", "foot_ik", "hand_ik", "prop_contact"],
            },
            "beats": [
                {
                    "start_offset": 0,
                    "end_offset": 30,
                    "action": "Approach the wet trail and decelerate.",
                    "pose_in": "Cautious upright walk.",
                    "pose_out": "Upper body settles above the planted feet.",
                    "anticipation": "Short weight shift before stopping.",
                    "weight_intent": "Transfer weight into the leading planted foot.",
                    "root_motion": {"mode": "travel", "screen_direction": "toward-camera", "intent": "Advance a short distance along the trail."},
                    "support": {"left_foot": "support", "right_foot": "swing", "weight_transfer": "Left foot carries weight as right foot clears the ground."},
                    "gaze": {"target": "Waterline", "direction_in": "away-from-camera", "direction_out": "down"},
                    "hands": [],
                },
                {
                    "start_offset": 30,
                    "end_offset": 60,
                    "action": "Settle, then reach toward the rope without pulling it.",
                    "pose_in": "Stopped with weight over both feet.",
                    "pose_out": "Hand held just short of the rope.",
                    "anticipation": "Gaze arrives before the reaching hand.",
                    "weight_intent": "Keep the pelvis balanced over the planted stance.",
                    "root_motion": {"mode": "stationary", "screen_direction": "static", "intent": "No root displacement while inspecting the rope."},
                    "support": {"left_foot": "planted", "right_foot": "planted", "weight_transfer": "Small shift toward the reaching side; both soles stay planted."},
                    "gaze": {"target": "Cut rope fibers", "direction_in": "down", "direction_out": "screen-right"},
                    "hands": [{"side": "left", "intent": "Reach without touching yet.", "target": "Rope end", "contact_state": "approach"}],
                    "prop_interaction": {"prop": "Rope", "hand": "left", "action": "Reach and stop short.", "contact_state": "approach", "prop_response": "Rope remains still; no contact yet."},
                },
            ],
            "continuity": {
                "incoming": "Begins already moving inward from the previous establishing beat.",
                "outgoing": "Ends balanced and looking screen-right toward the rope close-up.",
            },
        }

    def test_all_simulation_storyboards_are_valid(self):
        paths = sorted((ROOT / "examples").glob("*/storyboard.json"))
        paths.append(ROOT / "templates" / "storyboard.json")
        for path in paths:
            with self.subTest(path=path.parent.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual([], story_validator.validate(data))

    def test_rejects_non_object_and_missing_fields(self):
        self.assertIn("root must be an object", story_validator.validate([]))
        errors = story_validator.validate({"schema_version": 1})
        self.assertTrue(any(error.startswith("missing required field:") for error in errors))

    def test_rejects_boolean_schema_and_timing_integers(self):
        for key in ("schema_version", "fps", "total_frames"):
            with self.subTest(key=key):
                data = json.loads(json.dumps(self.valid))
                data[key] = True
                self.assertTrue(any(key in error for error in story_validator.validate(data)))

    def test_rejects_blank_text_and_non_object_scene(self):
        data = json.loads(json.dumps(self.valid))
        data["title"] = "   "
        data["scenes"][1] = None
        errors = story_validator.validate(data)
        self.assertTrue(any("title" in error for error in errors))
        self.assertTrue(any("scenes[1] must be an object" in error for error in errors))

    def test_rejects_missing_scene_fields_and_nonpositive_duration(self):
        data = json.loads(json.dumps(self.valid))
        data["scenes"][0].pop("action")
        data["scenes"][0]["start_frame"] = 60
        data["scenes"][0]["end_frame"] = 60
        errors = story_validator.validate(data)
        self.assertTrue(any("missing required field: action" in error for error in errors))
        self.assertTrue(any("positive duration" in error for error in errors))

    def test_rejects_duplicate_ids_gaps_and_overlaps(self):
        data = json.loads(json.dumps(self.valid))
        data["scenes"][1]["id"] = data["scenes"][0]["id"]
        data["scenes"][1]["start_frame"] += 1
        errors = story_validator.validate(data)
        self.assertTrue(any("duplicate scene id" in error for error in errors))
        self.assertTrue(any("expected contiguous frame" in error for error in errors))
        data["scenes"][1]["start_frame"] = 59
        self.assertTrue(any("expected contiguous frame" in error for error in story_validator.validate(data)))

    def test_rejects_inconsistent_voice_duration(self):
        data = json.loads(json.dumps(self.valid))
        data["voiceover"]["duration_ms"] = data["total_frames"] * 1000 // data["fps"] + 1
        self.assertTrue(any("voiceover.duration_ms exceeds" in error for error in story_validator.validate(data)))
        data["voiceover"]["duration_ms"] = True
        self.assertTrue(any("voiceover.duration_ms must" in error for error in story_validator.validate(data)))

    def test_accepts_optional_audio_clock_anchors_with_timing_provenance(self):
        data = json.loads(json.dumps(self.valid))
        data["voiceover"]["duration_ms"] = 4000
        data["narrative_timing"] = {
            "clock_source": "recording",
            "anchors": [
                {"id": "hook", "scene_id": "entrada", "kind": "beat", "start_ms": 0, "end_ms": 1200, "basis": "measured"},
                {"id": "breath", "scene_id": "entrada", "kind": "pause", "start_ms": 1200, "end_ms": 2000, "basis": "manually_reviewed"},
                {"id": "reveal-cut", "scene_id": "entrada", "kind": "cut", "start_ms": 2000, "end_ms": 2000, "basis": "measured"},
            ],
        }
        self.assertEqual([], story_validator.validate(data))

    def test_rejects_invalid_audio_clock_anchor_contract(self):
        data = json.loads(json.dumps(self.valid))
        data["narrative_timing"] = {
            "clock_source": "recording",
            "word_alignment": "exact",
            "anchors": [
                {"id": "hook", "scene_id": "missing-scene", "kind": "beat", "start_ms": 0, "end_ms": 1200, "basis": "estimated"},
                {"id": "reveal-cut", "scene_id": "decision", "kind": "cut", "start_ms": 1300, "end_ms": 1400, "basis": "measured"},
                {"id": "late", "scene_id": "decision", "kind": "pause", "start_ms": 4200, "end_ms": 4300, "basis": "measured"},
            ],
        }
        errors = story_validator.validate(data)
        self.assertTrue(any("scene_id" in error for error in errors), errors)
        self.assertTrue(any("basis" in error for error in errors), errors)
        self.assertTrue(any("cut" in error for error in errors), errors)
        self.assertTrue(any("voiceover.duration_ms" in error for error in errors), errors)
        self.assertTrue(any("unsupported field: word_alignment" in error for error in errors), errors)

    def test_audio_timing_anchor_must_overlap_referenced_scene(self):
        data = json.loads(json.dumps(self.valid))
        data["narrative_timing"] = {
            "clock_source": "estimated",
            "anchors": [
                {"id": "misplaced-beat", "scene_id": "decision", "kind": "beat", "start_ms": 0, "end_ms": 500, "basis": "estimated"},
            ],
        }
        errors = story_validator.validate(data)
        self.assertTrue(any("timing does not overlap scene decision timing" in error for error in errors), errors)

    def test_cut_anchor_must_match_an_outgoing_scene_transition(self):
        data = json.loads(json.dumps(self.valid))
        data["narrative_timing"] = {
            "clock_source": "estimated",
            "anchors": [
                {"id": "early-cut", "scene_id": "entrada", "kind": "cut", "start_ms": 1000, "end_ms": 1000, "basis": "estimated"},
            ],
        }
        errors = story_validator.validate(data)
        self.assertTrue(any("cut time must match a transition between scenes" in error for error in errors), errors)

    def test_cut_at_scene_boundary_allows_voice_beat_to_continue_across_cut(self):
        data = json.loads(json.dumps(self.valid))
        data["narrative_timing"] = {
            "clock_source": "recording",
            "anchors": [
                {"id": "spoken-beat", "scene_id": "entrada", "kind": "beat", "start_ms": 1500, "end_ms": 2500, "basis": "measured"},
                {"id": "picture-cut", "scene_id": "entrada", "kind": "cut", "start_ms": 1980, "end_ms": 1980, "basis": "manually_reviewed"},
                {"id": "breath", "scene_id": "indicio", "kind": "pause", "start_ms": 2500, "end_ms": 2800, "basis": "measured"},
            ],
        }
        self.assertEqual([], story_validator.validate(data))

    def test_audio_clock_rejects_unexpected_json_types_without_crashing(self):
        for field, value in (("clock_source", []), ("anchors", {})):
            with self.subTest(field=field):
                data = json.loads(json.dumps(self.valid))
                data["narrative_timing"] = {
                    "clock_source": "estimated",
                    "anchors": [{"id": "hook", "scene_id": "entrada", "kind": "beat", "start_ms": 0, "end_ms": 1000, "basis": "estimated"}],
                }
                data["narrative_timing"][field] = value
                errors = story_validator.validate(data)
                self.assertTrue(any("narrative_timing" in error for error in errors), errors)

    def test_rejects_bad_caption_shape_order_and_bounds(self):
        data = json.loads(json.dumps(self.valid))
        data["captions"] = [
            {"text": "Second", "start_ms": 5000, "end_ms": 2500},
            {"text": "First", "start_ms": 1000, "end_ms": 500},
            {"text": " ", "start_ms": True, "end_ms": 7000},
            "not an object",
        ]
        errors = story_validator.validate(data)
        self.assertTrue(any("chronological" in error for error in errors))
        self.assertTrue(any("positive duration" in error for error in errors))
        self.assertTrue(any("captions[2].text" in error for error in errors))
        self.assertTrue(any("captions[2].start_ms" in error for error in errors))
        self.assertTrue(any("exceeds storyboard duration" in error for error in errors))
        self.assertTrue(any("captions[3] must be an object" in error for error in errors))

    def test_rejects_accidental_caption_overlap(self):
        data = json.loads(json.dumps(self.valid))
        data["captions"] = [
            {"text": "First", "start_ms": 100, "end_ms": 800},
            {"text": "Second", "start_ms": 799, "end_ms": 1200},
        ]
        errors = story_validator.validate(data)
        self.assertTrue(any("overlap" in error for error in errors))

    def test_allows_caption_boundary_and_documented_intentional_overlap(self):
        data = json.loads(json.dumps(self.valid))
        data["captions"] = [
            {"text": "First", "start_ms": 100, "end_ms": 800},
            {"text": "Second", "start_ms": 800, "end_ms": 1200},
        ]
        self.assertFalse(any("overlap" in error for error in story_validator.validate(data)))
        data["captions"][1]["start_ms"] = 799
        data["captions"][1]["overlap_exception"] = "Deliberate crossfade of two speaker captions."
        self.assertFalse(any("overlap" in error for error in story_validator.validate(data)))

    def test_rejects_caption_overlap_exception_without_reason(self):
        data = json.loads(json.dumps(self.valid))
        data["captions"] = [
            {"text": "First", "start_ms": 100, "end_ms": 800},
            {"text": "Second", "start_ms": 799, "end_ms": 1200, "overlap_exception": "  "},
        ]
        errors = story_validator.validate(data)
        self.assertTrue(any("overlap_exception" in error for error in errors))
        self.assertTrue(any("overlap" in error for error in errors))

    def test_parse_rejects_duplicate_keys_and_nonstandard_numbers(self):
        with self.assertRaises(ValueError):
            story_validator.parse_json('{"fps": 30, "fps": 24}')
        with self.assertRaises(ValueError):
            story_validator.parse_json('{"extra": NaN}')
        with self.assertRaises(json.JSONDecodeError):
            story_validator.parse_json('{"fps": 30,}')

    def test_parse_enforces_explicit_json_nesting_limit(self):
        allowed = "[" * story_validator.MAX_JSON_NESTING + "0" + "]" * story_validator.MAX_JSON_NESTING
        parsed = story_validator.parse_json(allowed)
        for _ in range(story_validator.MAX_JSON_NESTING):
            self.assertIsInstance(parsed, list)
            parsed = parsed[0]
        self.assertEqual(0, parsed)

        excessive = "[" * (story_validator.MAX_JSON_NESTING + 1) + "0" + "]" * (story_validator.MAX_JSON_NESTING + 1)
        with self.assertRaisesRegex(ValueError, "maximum depth of 128"):
            story_validator.parse_json(excessive)

    def test_parse_ignores_json_structure_inside_escaped_strings(self):
        parsed = story_validator.parse_json(r'{"text":"brackets [{]} and escaped quote: \" remain text"}')
        self.assertEqual('brackets [{]} and escaped quote: " remain text', parsed["text"])

    def test_parse_rejects_unpaired_unicode_surrogates_in_keys_and_values(self):
        for source in (r'{"value":"\ud800"}', r'{"\udfff":"value"}'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "unpaired Unicode surrogate"):
                    story_validator.parse_json(source)

    def test_parse_preserves_valid_unicode_and_surrogate_pairs(self):
        parsed = story_validator.parse_json(r'{"español":"Núñez", "emoji":"\ud83d\ude0a"}')
        self.assertEqual("Núñez", parsed["español"])
        self.assertEqual("😊", parsed["emoji"])

    def test_rejects_invalid_aspect_ratio_and_scene_past_total(self):
        data = json.loads(json.dumps(self.valid))
        data["aspect_ratio"] = "wide"
        data["scenes"][0]["end_frame"] = data["total_frames"] + 1
        errors = story_validator.validate(data)
        self.assertTrue(any("aspect_ratio" in error for error in errors))
        self.assertTrue(any("exceeds total_frames" in error for error in errors))

    def test_rejects_aspect_ratio_components_exceeding_safe_digit_limit(self):
        data = json.loads(json.dumps(self.valid))
        data["aspect_ratio"] = "9" * 129 + ":16"
        errors = story_validator.validate(data)
        self.assertTrue(any("aspect_ratio" in error and "128 digits" in error for error in errors), errors)

    def test_accepts_optional_cinematography_contract(self):
        data = json.loads(json.dumps(self.valid))
        data["scenes"][0]["cinematography"] = {
            "subject_or_clue": "Explorer and wet footprints",
            "shot_size": "wide",
            "angle": "eye-level",
            "focal_point": {"x": 0.5, "y": 0.62},
            "continuity": {
                "axis_id": "explorer-to-tunnel",
                "screen_direction": "left-to-right",
                "gaze_direction": "screen-right",
            },
            "camera": {
                "intent": "Reveal cave geography before the clue.",
                "movement": "Slow motivated push-in.",
                "lens_intent": "Moderate perspective; preserve adult proportions.",
            },
            "narrative_light_source": "Explorer's headlamp",
            "visible_information": ["Explorer silhouette", "Wet trail toward tunnel"],
            "transition_reason": "Cut to the clue when the beam reaches it.",
        }
        self.assertEqual([], story_validator.validate(data))

    def test_rejects_invalid_cinematography_enum_and_coordinate_range(self):
        data = json.loads(json.dumps(self.valid))
        data["scenes"][0]["cinematography"] = {
            "subject_or_clue": "Explorer",
            "shot_size": "medium-wide-ish",
            "angle": "eye-level",
            "focal_point": {"x": 1.2, "y": 0.5},
            "continuity": {
                "axis_id": "explorer-to-tunnel",
                "screen_direction": "left-to-right",
                "gaze_direction": "screen-right",
            },
            "camera": {
                "intent": "Reveal geography.",
                "movement": "Slow push-in.",
                "lens_intent": "Moderate perspective.",
            },
            "narrative_light_source": "Headlamp",
            "visible_information": ["Explorer"],
            "transition_reason": "Cut to clue.",
        }
        errors = story_validator.validate(data)
        self.assertTrue(any("shot_size" in error for error in errors))
        self.assertTrue(any("focal_point.x" in error for error in errors))

    def test_rejects_invalid_cinematography_types_and_empty_visible_information(self):
        data = json.loads(json.dumps(self.valid))
        data["scenes"][0]["cinematography"] = {
            "subject_or_clue": "Explorer",
            "shot_size": "wide",
            "angle": "eye-level",
            "focal_point": {"x": True, "y": 0.5},
            "continuity": {
                "axis_id": " ",
                "screen_direction": "sideways",
                "gaze_direction": "screen-right",
            },
            "camera": {
                "intent": "Reveal geography.",
                "movement": "Slow push-in.",
                "lens_intent": "Moderate perspective.",
            },
            "narrative_light_source": "Headlamp",
            "visible_information": [],
            "transition_reason": "Cut to clue.",
        }
        errors = story_validator.validate(data)
        self.assertTrue(any("focal_point.x" in error for error in errors))
        self.assertTrue(any("axis_id" in error for error in errors))
        self.assertTrue(any("screen_direction" in error for error in errors))
        self.assertTrue(any("visible_information" in error for error in errors))

    def test_accepts_optional_animation_contract_with_rig_support_and_prop_contact(self):
        data = json.loads(json.dumps(self.valid))
        data["scenes"][0]["animation"] = self.valid_animation()
        self.assertEqual([], story_validator.validate(data))

    def test_rejects_animation_gaps_and_offsets_outside_scene(self):
        data = json.loads(json.dumps(self.valid))
        animation = self.valid_animation()
        animation["beats"][1]["start_offset"] = 31
        animation["beats"][1]["end_offset"] = 61
        data["scenes"][0]["animation"] = animation
        errors = story_validator.validate(data)
        self.assertTrue(any("contiguous" in error for error in errors))
        self.assertTrue(any("scene duration" in error for error in errors))

    def test_rejects_root_motion_direction_conflicting_with_mode(self):
        data = json.loads(json.dumps(self.valid))
        animation = self.valid_animation()
        animation["beats"][0]["root_motion"] = {
            "mode": "travel",
            "screen_direction": "static",
            "intent": "Move forward.",
        }
        data["scenes"][0]["animation"] = animation
        errors = story_validator.validate(data)
        self.assertTrue(any("must indicate travel for travel" in error for error in errors))

    def test_rejects_invalid_animation_rig_root_support_and_gaze_fields(self):
        data = json.loads(json.dumps(self.valid))
        animation = self.valid_animation()
        animation["rig"]["status"] = []
        animation["rig"]["required_controls"] = ["root", {"unknown": "control"}]
        animation["beats"][0]["root_motion"]["mode"] = ["teleport"]
        animation["beats"][0]["support"]["left_foot"] = {"state": "floating"}
        animation["beats"][0]["gaze"]["direction_out"] = ["behind-the-camera-left"]
        animation["beats"][1]["prop_interaction"]["hand"] = {"side": "third-hand"}
        data["scenes"][0]["animation"] = animation
        errors = story_validator.validate(data)
        self.assertTrue(any("required_controls" in error for error in errors))
        self.assertTrue(any("root_motion.mode" in error for error in errors))
        self.assertTrue(any("left_foot" in error for error in errors))
        self.assertTrue(any("gaze.direction_out" in error for error in errors))
        self.assertTrue(any("prop_interaction.hand" in error for error in errors))

    def test_cli_reports_invalid_encoding_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_bytes(b"\xff")
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / "validate-storyboard.py"), str(path)],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("FAIL:", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_returns_json_error_for_malformed_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_text('{"schema_version": 1,}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / "validate-storyboard.py"), str(path)],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("cannot read valid storyboard JSON", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


    def test_storyboard_cli_reports_excessive_nesting_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "deep.json"
            path.write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / "validate-storyboard.py"), str(path)],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("nesting", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


class SkillValidationTests(unittest.TestCase):
    def test_current_skill_is_valid(self):
        self.assertEqual([], skill_validator.validate(ROOT))

    def test_frontmatter_rejects_unquoted_colon_and_duplicate_fields(self):
        bad_yaml = "---\nname: sample-skill\ndescription: Planifica: aquí hay colon\n---\n"
        self.assertTrue(skill_validator.validate_frontmatter("sample-skill", bad_yaml))
        duplicate = '---\nname: sample-skill\nname: other-skill\ndescription: "Descripción suficientemente larga"\n---\n'
        self.assertTrue(skill_validator.validate_frontmatter("sample-skill", duplicate))

    def test_frontmatter_rejects_wrong_name_and_empty_description(self):
        bad = '---\nname: Otro\ndescription: ""\n---\n'
        errors = skill_validator.validate_frontmatter("sample-skill", bad)
        self.assertTrue(any("name" in error for error in errors))
        self.assertTrue(any("description" in error for error in errors))

    def test_frontmatter_rejects_missing_closing_delimiter(self):
        self.assertTrue(skill_validator.validate_frontmatter("sample-skill", '---\nname: sample-skill\ndescription: "A long enough description here"\n'))

    def test_scans_links_in_all_markdown_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
            (root / "guide.md").write_text("[missing](absent.md) and [escape](../outside.md)", encoding="utf-8")
            errors = skill_validator.validate_markdown_links(root)
        self.assertTrue(any("broken local link" in error for error in errors))
        self.assertTrue(any("escapes skill directory" in error for error in errors))

    def test_validates_reference_links_and_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "guide with spaces.md").write_text("# Guide\n", encoding="utf-8")
            (root / "guide.md").write_text(
                "[guide][main] [missing][bad]\n\n"
                "[main]: <guide with spaces.md> \"title\"\n"
                "[bad]: <missing file.md>\n",
                encoding="utf-8",
            )
            errors = skill_validator.validate_markdown_links(root)
        self.assertEqual(1, sum("broken local link" in error for error in errors))
        self.assertTrue(any("missing file.md" in error for error in errors))

    def test_ignores_apparent_links_in_fenced_indented_and_inline_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "guide.md").write_text(
                "````markdown\n[broken](missing-fenced.md)\n````\n"
                "    [broken](missing-indented.md)\n"
                "Inline ``[broken](missing-inline.md)`` code.\n",
                encoding="utf-8",
            )
            self.assertEqual([], skill_validator.validate_markdown_links(root))

    def test_semantic_skill_validation_checks_examples_and_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in (
                "examples/bad/storyboard.json",
                "templates/storyboard.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"schema_version": 1}', encoding="utf-8")
            errors = skill_validator.validate_storyboards(root)
        errors_text = "\n".join(errors).replace("\\", "/")
        self.assertIn("examples/bad/storyboard.json", errors_text)
        self.assertIn("templates/storyboard.json", errors_text)

    def test_skill_validator_reports_malformed_json_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bad.json").write_text('{"value": NaN}', encoding="utf-8")
            errors = skill_validator.validate_json_files(root)
        self.assertTrue(any("invalid JSON" in error for error in errors))

    def test_skill_validator_reports_excessive_json_nesting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "deep.json").write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")
            errors = skill_validator.validate_json_files(root)
        self.assertTrue(any("nesting" in error for error in errors), errors)

    def test_skill_validator_reports_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "duplicate.json").write_text('{"fps": 30, "fps": 24}', encoding="utf-8")
            errors = skill_validator.validate_json_files(root)
        self.assertTrue(any("duplicate JSON key" in error for error in errors))

    def test_skill_validator_checks_pipeline_configs_semantically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "examples" / "bad" / "pipeline.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"pipeline_version": 1}', encoding="utf-8")
            errors = skill_validator.validate_pipeline_configs(root)
        self.assertTrue(any("missing required field: project" in error for error in errors))

    def test_skill_validator_reports_missing_required_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sample-skill"
            root.mkdir()
            (root / "SKILL.md").write_text('---\nname: sample-skill\ndescription: "A valid description longer than twenty chars."\n---\n', encoding="utf-8")
            errors = skill_validator.validate(root)
        self.assertTrue(any("missing required file: references/storytelling.md" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
