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

    def test_rejects_bad_caption_shape_order_and_bounds(self):
        data = json.loads(json.dumps(self.valid))
        data["captions"] = [
            {"text": "Second", "start_ms": 2000, "end_ms": 2500},
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

    def test_rejects_invalid_aspect_ratio_and_scene_past_total(self):
        data = json.loads(json.dumps(self.valid))
        data["aspect_ratio"] = "wide"
        data["scenes"][0]["end_frame"] = data["total_frames"] + 1
        errors = story_validator.validate(data)
        self.assertTrue(any("aspect_ratio" in error for error in errors))
        self.assertTrue(any("exceeds total_frames" in error for error in errors))

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

    def test_skill_validator_reports_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "duplicate.json").write_text('{"fps": 30, "fps": 24}', encoding="utf-8")
            errors = skill_validator.validate_json_files(root)
        self.assertTrue(any("duplicate JSON key" in error for error in errors))

    def test_skill_validator_reports_missing_required_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sample-skill"
            root.mkdir()
            (root / "SKILL.md").write_text('---\nname: sample-skill\ndescription: "A valid description longer than twenty chars."\n---\n', encoding="utf-8")
            errors = skill_validator.validate(root)
        self.assertTrue(any("missing required file: references/storytelling.md" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
