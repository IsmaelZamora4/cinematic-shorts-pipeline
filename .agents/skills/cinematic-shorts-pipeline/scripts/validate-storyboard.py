#!/usr/bin/env python3
"""Validate storyboard structure and timeline using Python's standard library."""
import argparse
import json
import math
import re
import sys
from pathlib import Path


SHOT_SIZES = {"extreme-wide", "wide", "full", "medium", "close-up", "extreme-close-up", "insert"}
CAMERA_ANGLES = {"eye-level", "low", "high", "overhead", "dutch"}
SCREEN_DIRECTIONS = {"left-to-right", "right-to-left", "toward-camera", "away-from-camera", "static"}
GAZE_DIRECTIONS = {"screen-left", "screen-right", "up", "down", "toward-camera", "away-from-camera", "on-subject", "none"}
RIG_STATUSES = {"unselected", "unverified", "verified"}
RIG_CONTROLS = {"root", "gaze", "foot_ik", "hand_ik", "prop_contact"}
ROOT_MOTION_MODES = {"stationary", "in-place-turn", "travel", "combined"}
FOOT_STATES = {"planted", "support", "swing", "airborne", "offscreen", "not-applicable"}
HAND_SIDES = {"left", "right", "both"}
HAND_CONTACT_STATES = {"none", "approach", "touch", "grasp", "release", "maintain"}
MAX_ASPECT_RATIO_COMPONENT_DIGITS = 128
MAX_JSON_NESTING = 128


def _non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_enum(value, choices):
    return isinstance(value, str) and value in choices


def _validate_cinematography(data, label):
    errors = []
    if not isinstance(data, dict):
        return [f"{label}.cinematography must be an object when provided"]

    for field in ("subject_or_clue", "narrative_light_source", "transition_reason"):
        if not _non_empty_string(data.get(field)):
            errors.append(f"{label}.cinematography.{field} must be a non-empty string")
    if not _is_enum(data.get("shot_size"), SHOT_SIZES):
        errors.append(f"{label}.cinematography.shot_size must be a supported shot size")
    if not _is_enum(data.get("angle"), CAMERA_ANGLES):
        errors.append(f"{label}.cinematography.angle must be a supported camera angle")

    focal_point = data.get("focal_point")
    if not isinstance(focal_point, dict):
        errors.append(f"{label}.cinematography.focal_point must be an object")
    else:
        for coordinate in ("x", "y"):
            value = focal_point.get(coordinate)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or (isinstance(value, float) and not math.isfinite(value))
                or not 0 <= value <= 1
            ):
                errors.append(f"{label}.cinematography.focal_point.{coordinate} must be between 0 and 1")

    continuity = data.get("continuity")
    if not isinstance(continuity, dict):
        errors.append(f"{label}.cinematography.continuity must be an object")
    else:
        if not _non_empty_string(continuity.get("axis_id")):
            errors.append(f"{label}.cinematography.continuity.axis_id must be a non-empty string")
        if not _is_enum(continuity.get("screen_direction"), SCREEN_DIRECTIONS):
            errors.append(f"{label}.cinematography.continuity.screen_direction is unsupported")
        if not _is_enum(continuity.get("gaze_direction"), GAZE_DIRECTIONS):
            errors.append(f"{label}.cinematography.continuity.gaze_direction is unsupported")

    camera = data.get("camera")
    if not isinstance(camera, dict):
        errors.append(f"{label}.cinematography.camera must be an object")
    else:
        for field in ("intent", "movement", "lens_intent"):
            if not _non_empty_string(camera.get(field)):
                errors.append(f"{label}.cinematography.camera.{field} must be a non-empty string")

    visible = data.get("visible_information")
    if (
        not isinstance(visible, list)
        or not visible
        or any(not _non_empty_string(item) for item in visible)
    ):
        errors.append(f"{label}.cinematography.visible_information must be a non-empty array of strings")
    return errors


def _validate_animation(data, label, scene_duration):
    errors = []
    prefix = f"{label}.animation"
    if not isinstance(data, dict):
        return [f"{prefix} must be an object when provided"]

    rig = data.get("rig")
    if not isinstance(rig, dict):
        errors.append(f"{prefix}.rig must be an object")
    else:
        if not _is_enum(rig.get("status"), RIG_STATUSES):
            errors.append(f"{prefix}.rig.status is unsupported")
        if not _non_empty_string(rig.get("profile")):
            errors.append(f"{prefix}.rig.profile must be a non-empty string")
        controls = rig.get("required_controls")
        valid_controls = (
            isinstance(controls, list)
            and bool(controls)
            and all(_is_enum(control, RIG_CONTROLS) for control in controls)
        )
        if not valid_controls or len(set(controls)) != len(controls) or "root" not in controls:
            errors.append(f"{prefix}.rig.required_controls must be unique supported controls including root")

    continuity = data.get("continuity")
    if not isinstance(continuity, dict):
        errors.append(f"{prefix}.continuity must be an object")
    else:
        for field in ("incoming", "outgoing"):
            if not _non_empty_string(continuity.get(field)):
                errors.append(f"{prefix}.continuity.{field} must be a non-empty string")

    beats = data.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append(f"{prefix}.beats must be a non-empty array")
        return errors

    expected_start = 0
    for index, beat in enumerate(beats):
        beat_prefix = f"{prefix}.beats[{index}]"
        if not isinstance(beat, dict):
            errors.append(f"{beat_prefix} must be an object")
            continue
        start, end = beat.get("start_offset"), beat.get("end_offset")
        valid_start = isinstance(start, int) and not isinstance(start, bool) and start >= 0
        valid_end = isinstance(end, int) and not isinstance(end, bool) and end > 0
        if not valid_start:
            errors.append(f"{beat_prefix}.start_offset must be a non-negative integer")
        if not valid_end:
            errors.append(f"{beat_prefix}.end_offset must be a positive integer")
        if valid_start and valid_end:
            if end <= start:
                errors.append(f"{beat_prefix} must have positive duration")
            if start != expected_start:
                errors.append(f"{beat_prefix}.start_offset must be contiguous at {expected_start}")
            if end > scene_duration:
                errors.append(f"{beat_prefix}.end_offset exceeds scene duration")
            expected_start = end

        for field in ("action", "pose_in", "pose_out", "anticipation", "weight_intent"):
            if not _non_empty_string(beat.get(field)):
                errors.append(f"{beat_prefix}.{field} must be a non-empty string")

        root_motion = beat.get("root_motion")
        if not isinstance(root_motion, dict):
            errors.append(f"{beat_prefix}.root_motion must be an object")
        else:
            mode = root_motion.get("mode")
            direction = root_motion.get("screen_direction")
            valid_mode = _is_enum(mode, ROOT_MOTION_MODES)
            if not valid_mode:
                errors.append(f"{beat_prefix}.root_motion.mode is unsupported")
            valid_direction = _is_enum(direction, SCREEN_DIRECTIONS)
            if not valid_direction:
                errors.append(f"{beat_prefix}.root_motion.screen_direction is unsupported")
            elif valid_mode and mode in {"stationary", "in-place-turn"} and direction != "static":
                errors.append(f"{beat_prefix}.root_motion.screen_direction must be static for {mode}")
            elif valid_mode and mode in {"travel", "combined"} and direction == "static":
                errors.append(f"{beat_prefix}.root_motion.screen_direction must indicate travel for {mode}")
            if not _non_empty_string(root_motion.get("intent")):
                errors.append(f"{beat_prefix}.root_motion.intent must be a non-empty string")

        support = beat.get("support")
        if not isinstance(support, dict):
            errors.append(f"{beat_prefix}.support must be an object")
        else:
            for foot in ("left_foot", "right_foot"):
                if not _is_enum(support.get(foot), FOOT_STATES):
                    errors.append(f"{beat_prefix}.support.{foot} is unsupported")
            if not _non_empty_string(support.get("weight_transfer")):
                errors.append(f"{beat_prefix}.support.weight_transfer must be a non-empty string")

        gaze = beat.get("gaze")
        if not isinstance(gaze, dict):
            errors.append(f"{beat_prefix}.gaze must be an object")
        else:
            if not _non_empty_string(gaze.get("target")):
                errors.append(f"{beat_prefix}.gaze.target must be a non-empty string")
            for direction in ("direction_in", "direction_out"):
                if not _is_enum(gaze.get(direction), GAZE_DIRECTIONS):
                    errors.append(f"{beat_prefix}.gaze.{direction} is unsupported")

        hands = beat.get("hands")
        if not isinstance(hands, list):
            errors.append(f"{beat_prefix}.hands must be an array")
        else:
            for hand_index, hand in enumerate(hands):
                hand_prefix = f"{beat_prefix}.hands[{hand_index}]"
                if not isinstance(hand, dict):
                    errors.append(f"{hand_prefix} must be an object")
                    continue
                if not _is_enum(hand.get("side"), HAND_SIDES):
                    errors.append(f"{hand_prefix}.side is unsupported")
                for field in ("intent", "target"):
                    if not _non_empty_string(hand.get(field)):
                        errors.append(f"{hand_prefix}.{field} must be a non-empty string")
                if not _is_enum(hand.get("contact_state"), HAND_CONTACT_STATES):
                    errors.append(f"{hand_prefix}.contact_state is unsupported")

        interaction = beat.get("prop_interaction")
        if interaction is not None:
            if not isinstance(interaction, dict):
                errors.append(f"{beat_prefix}.prop_interaction must be an object when provided")
            else:
                if not _non_empty_string(interaction.get("prop")):
                    errors.append(f"{beat_prefix}.prop_interaction.prop must be a non-empty string")
                if not _is_enum(interaction.get("hand"), HAND_SIDES):
                    errors.append(f"{beat_prefix}.prop_interaction.hand is unsupported")
                for field in ("action", "prop_response"):
                    if not _non_empty_string(interaction.get(field)):
                        errors.append(f"{beat_prefix}.prop_interaction.{field} must be a non-empty string")
                if not _is_enum(interaction.get("contact_state"), HAND_CONTACT_STATES - {"none"}):
                    errors.append(f"{beat_prefix}.prop_interaction.contact_state is unsupported")

    if expected_start != scene_duration:
        errors.append(f"{prefix}.beats end at {expected_start}; expected scene duration {scene_duration}")
    return errors


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in key):
            raise ValueError("JSON strings and object keys must not contain unpaired Unicode surrogate code points")
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value):
    raise ValueError(f"non-standard JSON number: {value}")


def _check_json_nesting(text):
    """Reject containers deeper than MAX_JSON_NESTING without relying on json's recursion limit."""
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise ValueError(
                    f"JSON nesting exceeds maximum depth of {MAX_JSON_NESTING}; reduce nested arrays/objects"
                )
        elif character in "]}":
            depth = max(0, depth - 1)


def _reject_unpaired_surrogates(value):
    """Reject strings that cannot be encoded as valid UTF-8 scalar values."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                raise ValueError("JSON strings and object keys must not contain unpaired Unicode surrogate code points")
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _validate_narrative_timing(data):
    if "narrative_timing" not in data:
        return []
    errors = []
    block = data["narrative_timing"]
    if not isinstance(block, dict):
        return ["narrative_timing must be an object when provided"]
    allowed_block_fields = {"clock_source", "anchors"}
    for field in sorted(block.keys() - allowed_block_fields):
        errors.append(f"narrative_timing contains unsupported field: {field}")

    clock_source = block.get("clock_source")
    if not isinstance(clock_source, str) or clock_source not in {"estimated", "recording"}:
        errors.append("narrative_timing.clock_source must be 'estimated' or 'recording'")

    voice = data.get("voiceover")
    duration_ms = voice.get("duration_ms") if isinstance(voice, dict) else None
    valid_duration = isinstance(duration_ms, int) and not isinstance(duration_ms, bool) and duration_ms > 0
    if not valid_duration:
        errors.append("narrative_timing requires a positive voiceover.duration_ms as its audio clock")

    raw_scenes = data.get("scenes")
    scene_order = raw_scenes if isinstance(raw_scenes, list) else []
    scenes_by_id = {
        scene["id"]: (index, scene) for index, scene in enumerate(scene_order)
        if isinstance(scene, dict) and isinstance(scene.get("id"), str)
    }
    fps = data.get("fps")
    valid_fps = isinstance(fps, int) and not isinstance(fps, bool) and fps > 0
    frame_tolerance_ms = (1000 + fps - 1) // fps if valid_fps else None
    anchors = block.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        errors.append("narrative_timing.anchors must be a non-empty array")
        return errors

    allowed_anchor_fields = {"id", "scene_id", "kind", "start_ms", "end_ms", "basis"}
    seen_ids = set()
    previous_start = -1
    previous_interval_end = -1
    for index, anchor in enumerate(anchors):
        label = f"narrative_timing.anchors[{index}]"
        if not isinstance(anchor, dict):
            errors.append(f"{label} must be an object")
            continue
        for field in sorted(anchor.keys() - allowed_anchor_fields):
            errors.append(f"{label} contains unsupported field: {field}")

        ident = anchor.get("id")
        if not isinstance(ident, str) or not ident.strip():
            errors.append(f"{label}.id must be a non-empty string")
        elif ident in seen_ids:
            errors.append(f"{label}.id duplicates {ident}")
        else:
            seen_ids.add(ident)

        scene_id = anchor.get("scene_id")
        if not isinstance(scene_id, str) or scene_id not in scenes_by_id:
            errors.append(f"{label}.scene_id must reference an existing scene")
            scene = None
        else:
            scene_index, scene = scenes_by_id[scene_id]

        kind = anchor.get("kind")
        if not isinstance(kind, str) or kind not in {"beat", "pause", "cut"}:
            errors.append(f"{label}.kind must be 'beat', 'pause', or 'cut'")

        basis = anchor.get("basis")
        if not isinstance(basis, str) or basis not in {"estimated", "measured", "manually_reviewed"}:
            errors.append(f"{label}.basis must be 'estimated', 'measured', or 'manually_reviewed'")
        elif clock_source == "estimated" and basis != "estimated":
            errors.append(f"{label}.basis must be estimated when clock_source is estimated")
        elif clock_source == "recording" and basis == "estimated":
            errors.append(f"{label}.basis must be measured or manually_reviewed when clock_source is recording")

        start, end = anchor.get("start_ms"), anchor.get("end_ms")
        valid_start = isinstance(start, int) and not isinstance(start, bool) and start >= 0
        valid_end = isinstance(end, int) and not isinstance(end, bool) and end >= 0
        if not valid_start:
            errors.append(f"{label}.start_ms must be a non-negative integer")
        if not valid_end:
            errors.append(f"{label}.end_ms must be a non-negative integer")
        if valid_start and valid_end:
            if kind == "cut" and end != start:
                errors.append(f"{label} cut must be a zero-duration time point")
            elif kind in {"beat", "pause"} and end <= start:
                errors.append(f"{label} {kind} must have positive duration")
            if start < previous_start:
                errors.append(f"{label}.start_ms must be chronological")
            if kind != "cut" and start < previous_interval_end:
                errors.append(f"{label} overlaps an earlier narrative timing anchor")
            if valid_duration and end > duration_ms:
                errors.append(f"{label}.end_ms exceeds voiceover.duration_ms")
            previous_start = start
            if kind != "cut":
                previous_interval_end = max(previous_interval_end, end)

            if scene is not None and valid_fps and frame_tolerance_ms is not None:
                scene_start_frame = scene.get("start_frame")
                scene_end_frame = scene.get("end_frame")
                valid_scene_frames = (
                    isinstance(scene_start_frame, int) and not isinstance(scene_start_frame, bool)
                    and isinstance(scene_end_frame, int) and not isinstance(scene_end_frame, bool)
                    and scene_start_frame >= 0 and scene_end_frame > scene_start_frame
                )
                if valid_scene_frames:
                    if kind in {"beat", "pause"}:
                        overlaps_scene = (
                            (end + frame_tolerance_ms) * fps > scene_start_frame * 1000
                            and (start - frame_tolerance_ms) * fps < scene_end_frame * 1000
                        )
                        if not overlaps_scene:
                            errors.append(f"{label} timing does not overlap scene {scene_id} timing")
                    elif kind == "cut":
                        next_index = scene_index + 1
                        has_next_scene = next_index < len(scene_order)
                        next_scene = scene_order[next_index] if has_next_scene else None
                        is_transition = (
                            isinstance(next_scene, dict)
                            and isinstance(next_scene.get("start_frame"), int)
                            and not isinstance(next_scene.get("start_frame"), bool)
                            and scene_end_frame == next_scene["start_frame"]
                        )
                        matches_boundary = (
                            is_transition
                            and abs(start * fps - scene_end_frame * 1000)
                            <= frame_tolerance_ms * fps
                        )
                        if not matches_boundary:
                            errors.append(
                                f"{label} cut time must match a transition between scenes after {scene_id} "
                                f"within one frame ({frame_tolerance_ms} ms)"
                            )

        if kind == "cut" and scene is not None:
            cinematography = scene.get("cinematography")
            transition = cinematography.get("transition_reason") if isinstance(cinematography, dict) else None
            if not isinstance(transition, str) or not transition.strip():
                errors.append(f"{label} cut requires scene {scene_id} cinematography.transition_reason")
    return errors


def parse_json(text):
    """Parse strict JSON with deterministic depth, key, number, and Unicode checks."""
    _check_json_nesting(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except RecursionError as exc:
        raise ValueError(
            f"JSON parser recursion limit reached; reduce nesting to {MAX_JSON_NESTING} levels or fewer"
        ) from exc
    _reject_unpaired_surrogates(value)
    return value


def validate(data):
    errors = []
    if not isinstance(data, dict):
        return ["root must be an object"]
    for key in ("schema_version", "title", "fps", "total_frames", "logline", "scenes"):
        if key not in data:
            errors.append(f"missing required field: {key}")
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        errors.append("schema_version must be integer 1")
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        errors.append("title must be a non-empty string")
    fps = data.get("fps")
    total = data.get("total_frames")
    valid_fps = isinstance(fps, int) and not isinstance(fps, bool) and fps > 0
    valid_total = isinstance(total, int) and not isinstance(total, bool) and total > 0
    if not valid_fps:
        errors.append("fps must be a positive integer")
    if not valid_total:
        errors.append("total_frames must be a positive integer")
    if not isinstance(data.get("logline"), str) or not data["logline"].strip():
        errors.append("logline must be a non-empty string")
    aspect_ratio = data.get("aspect_ratio")
    if aspect_ratio is not None:
        if not isinstance(aspect_ratio, str) or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", aspect_ratio):
            errors.append("aspect_ratio must use the form 'width:height' with positive integers")
        elif any(
            len(component) > MAX_ASPECT_RATIO_COMPONENT_DIGITS
            for component in aspect_ratio.split(":")
        ):
            errors.append(
                f"aspect_ratio components must not exceed {MAX_ASPECT_RATIO_COMPONENT_DIGITS} digits"
            )

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("scenes must be a non-empty array")
        scenes = []
    seen = set()
    expected_start = 0
    for i, scene in enumerate(scenes):
        label = f"scenes[{i}]"
        if not isinstance(scene, dict):
            errors.append(f"{label} must be an object")
            continue
        for key in ("id", "start_frame", "end_frame", "purpose", "action", "framing", "camera", "lighting"):
            if key not in scene:
                errors.append(f"{label} missing required field: {key}")
        ident = scene.get("id")
        if not isinstance(ident, str) or not ident.strip():
            errors.append(f"{label}.id must be a non-empty string")
        elif ident in seen:
            errors.append(f"duplicate scene id: {ident}")
        else:
            seen.add(ident)
        start, end = scene.get("start_frame"), scene.get("end_frame")
        valid_start = isinstance(start, int) and not isinstance(start, bool) and start >= 0
        valid_end = isinstance(end, int) and not isinstance(end, bool) and end > 0
        if not valid_start:
            errors.append(f"{label}.start_frame must be a non-negative integer")
        if not valid_end:
            errors.append(f"{label}.end_frame must be a positive integer")
        if valid_start and valid_end:
            if end <= start:
                errors.append(f"{label} must have positive duration")
            if start != expected_start:
                errors.append(f"{label} starts at {start}; expected contiguous frame {expected_start}")
            if valid_total and end > total:
                errors.append(f"{label}.end_frame exceeds total_frames")
            expected_start = end
        for key in ("purpose", "action", "framing", "camera", "lighting"):
            if not isinstance(scene.get(key), str) or not scene[key].strip():
                errors.append(f"{label}.{key} must be a non-empty string")
        if "cinematography" in scene:
            errors.extend(_validate_cinematography(scene["cinematography"], label))
        if "animation" in scene and valid_start and valid_end and end > start:
            errors.extend(_validate_animation(scene["animation"], label, end - start))
    if valid_total and scenes and expected_start != total:
        errors.append(f"scene timeline ends at {expected_start}; total_frames is {total}")

    voice = data.get("voiceover")
    if voice is not None:
        if not isinstance(voice, dict):
            errors.append("voiceover must be an object when provided")
        elif "duration_ms" in voice:
            duration_ms = voice["duration_ms"]
            valid_voice_duration = isinstance(duration_ms, int) and not isinstance(duration_ms, bool) and duration_ms > 0
            if not valid_voice_duration:
                errors.append("voiceover.duration_ms must be a positive integer")
            elif valid_fps and valid_total and duration_ms > total * 1000 / fps:
                errors.append("voiceover.duration_ms exceeds storyboard duration")

    errors.extend(_validate_narrative_timing(data))

    captions = data.get("captions", [])
    if not isinstance(captions, list):
        errors.append("captions must be an array")
    else:
        max_ms = total * 1000 / fps if valid_total and valid_fps else None
        previous_start = -1
        active_captions = []
        for i, cue in enumerate(captions):
            label = f"captions[{i}]"
            if not isinstance(cue, dict):
                errors.append(f"{label} must be an object")
                continue
            if not isinstance(cue.get("text"), str) or not cue["text"].strip():
                errors.append(f"{label}.text must be a non-empty string")
            overlap_exception = cue.get("overlap_exception")
            has_overlap_exception = (
                isinstance(overlap_exception, str) and bool(overlap_exception.strip())
            )
            if "overlap_exception" in cue and not has_overlap_exception:
                errors.append(f"{label}.overlap_exception must explain intentional overlap")
            start, end = cue.get("start_ms"), cue.get("end_ms")
            valid_start = isinstance(start, int) and not isinstance(start, bool) and start >= 0
            valid_end = isinstance(end, int) and not isinstance(end, bool) and end > 0
            if not valid_start:
                errors.append(f"{label}.start_ms must be a non-negative integer")
            if not valid_end:
                errors.append(f"{label}.end_ms must be a positive integer")
            if valid_start and start < previous_start:
                errors.append(f"{label}.start_ms must be chronological")
            if valid_start:
                previous_start = start
            if valid_start and valid_end:
                if end <= start:
                    errors.append(f"{label} must have positive duration")
                active_captions = [active for active in active_captions if active[0] > start]
                if active_captions and not has_overlap_exception:
                    errors.append(
                        f"{label} overlaps an earlier caption; add overlap_exception only for deliberate layering"
                    )
                active_captions.append((end, i))
            if valid_end and max_ms is not None and end > max_ms:
                errors.append(f"{label}.end_ms exceeds storyboard duration")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storyboard", help="storyboard JSON file")
    args = parser.parse_args()
    try:
        data = parse_json(Path(args.storyboard).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"FAIL: cannot read valid storyboard JSON: {exc}")
        return 2
    errors = validate(data)
    if errors:
        print("FAIL: storyboard validation")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: storyboard structure and timeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
