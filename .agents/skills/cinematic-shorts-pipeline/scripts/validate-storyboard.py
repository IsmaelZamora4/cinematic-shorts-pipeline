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


def _non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_cinematography(data, label):
    errors = []
    if not isinstance(data, dict):
        return [f"{label}.cinematography must be an object when provided"]

    for field in ("subject_or_clue", "narrative_light_source", "transition_reason"):
        if not _non_empty_string(data.get(field)):
            errors.append(f"{label}.cinematography.{field} must be a non-empty string")
    if data.get("shot_size") not in SHOT_SIZES:
        errors.append(f"{label}.cinematography.shot_size must be a supported shot size")
    if data.get("angle") not in CAMERA_ANGLES:
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
        if continuity.get("screen_direction") not in SCREEN_DIRECTIONS:
            errors.append(f"{label}.cinematography.continuity.screen_direction is unsupported")
        if continuity.get("gaze_direction") not in GAZE_DIRECTIONS:
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


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value):
    raise ValueError(f"non-standard JSON number: {value}")


def parse_json(text):
    """Parse strict JSON; reject duplicate keys and NaN/Infinity values."""
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonstandard_constant,
    )


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
    if aspect_ratio is not None and (not isinstance(aspect_ratio, str) or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", aspect_ratio)):
        errors.append("aspect_ratio must use the form 'width:height' with positive integers")

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
