#!/usr/bin/env python3
"""Read-only, bounded validator for the Phase 5B scene-package ZIP v1."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
import stat
import struct
import uuid
import zipfile
import zlib

from png_rgb8_decoder import PngValidationError, verify_png_rgb8


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_COUNT = 27
MAX_EXPANDED_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_BLEND_BYTES = 512 * 1024 * 1024
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
IO_CHUNK_BYTES = 64 * 1024
MAX_MEMBER_NAME_BYTES = 65535
MAX_CENTRAL_DIRECTORY_BYTES = MAX_MEMBER_COUNT * (46 + MAX_MEMBER_NAME_BYTES)
UNCOMPRESSED_BLEND_52_HEADER = b"BLENDER17-01v0502"

PROJECT_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FRAME_PATH = re.compile(r"^scenes/([a-z0-9]+(?:-[a-z0-9]+)*)/frames/frame-(\d{6})\.png$")
BLEND_PATH = re.compile(r"^scenes/([a-z0-9]+(?:-[a-z0-9]+)*)/scene\.blend$")
RESOURCE_INPUT = re.compile(r"^resource-[a-z0-9]+(?:-[a-z0-9]+)*$")
EXPECTED_INPUT_IDS = {"storyboard", "validated-plan"}
PNG_FORMAT = "PNG_RGB8_SRGB"
ZIP_EOCD = struct.Struct("<4s4H2LH")
ZIP_CENTRAL = struct.Struct("<4s6H3L5H2L")
ZIP_LOCAL = struct.Struct("<4s5H3L2H")


class ScenePackageValidationError(ValueError):
    """The package is invalid, incomplete, corrupt, or inconsistent."""


@dataclass(frozen=True)
class ScenePackageLimits:
    """Injectable budgets for low-cost boundary tests; production defaults are fixed above."""

    max_archive_bytes: int = MAX_ARCHIVE_BYTES
    max_expanded_total_bytes: int = MAX_EXPANDED_TOTAL_BYTES
    max_json_bytes: int = MAX_JSON_BYTES
    max_blend_bytes: int = MAX_BLEND_BYTES
    max_png_bytes: int = MAX_PNG_BYTES
    max_compression_ratio: int = MAX_COMPRESSION_RATIO


DEFAULT_LIMITS = ScenePackageLimits()


@dataclass(frozen=True)
class ScenePackageContext:
    """Expected values derived by the caller from independently validated inputs/manifest."""

    project_id: str
    scene_id: str
    run_id: str
    stage_01_fingerprint: str
    stage_02_fingerprint: str
    input_hashes: dict
    runtime: dict
    expected_render_seed: int
    manifest_scene_package_sha256: str


@dataclass(frozen=True)
class ScenePackageResult:
    archive_sha256: str
    project_id: str
    scene_id: str
    run_id: str
    member_count: int
    payload_count: int
    context_verified: bool
    execution_verified: bool = False
    blend_openability_verified: bool = False


@dataclass(frozen=True)
class _CentralMember:
    name: str
    flags: int
    method: int
    modified_time: int
    modified_date: int
    crc: int
    compressed_size: int
    uncompressed_size: int
    local_offset: int
    create_system: int
    version_made: int
    version_needed: int
    external_attr: int
    data_offset: int
    data_end: int


def _fail(message):
    raise ScenePackageValidationError(message)


def _exact_object(value, fields, label):
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    actual = set(value)
    expected = set(fields)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        _fail(f"{label} schema mismatch (missing={missing}, unknown={extra})")
    return value


def _is_int(value):
    return type(value) is int


def _is_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _require_int(value, label, minimum, maximum=None):
    if not _is_int(value) or value < minimum or (maximum is not None and value > maximum):
        _fail(f"{label} must be an integer in the permitted range")
    return value


def _require_sha256(value, label):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_slug(value, label):
    if not isinstance(value, str) or not PROJECT_SLUG.fullmatch(value):
        _fail(f"{label} must be an ASCII slug")
    return value


def _require_uuid(value, label):
    if not isinstance(value, str):
        _fail(f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        _fail(f"{label} must be a canonical UUID")
    if str(parsed) != value:
        _fail(f"{label} must be a lowercase canonical UUID")
    return value


def _json_object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value):
    _fail(f"JSON contains non-standard numeric constant: {value}")


def _parse_json(data, label):
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_json_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ScenePackageValidationError):
            raise
        raise ScenePackageValidationError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{label} root must be an object")
    return value


def _read_exact_at(stream, offset, length):
    stream.seek(offset)
    result = bytearray()
    while len(result) < length:
        block = stream.read(min(length - len(result), IO_CHUNK_BYTES))
        if not block:
            _fail("ZIP structure is truncated")
        if not isinstance(block, (bytes, bytearray, memoryview)):
            _fail("ZIP source must be a binary seekable stream")
        result.extend(block)
    return bytes(result)


def _archive_digest(stream, limits):
    try:
        position = stream.tell()
        stream.seek(0, 2)
        size = stream.tell()
        if not _is_int(size) or size < ZIP_EOCD.size or size > limits.max_archive_bytes:
            _fail("ZIP archive exceeds the 512 MiB input limit or is too small")
        stream.seek(0)
        digest = hashlib.sha256()
        count = 0
        while count < size:
            block = stream.read(min(IO_CHUNK_BYTES, size - count))
            if not block:
                _fail("ZIP archive changed or ended during bounded hashing")
            count += len(block)
            if count > size:
                _fail("ZIP stream returned more data than requested")
            digest.update(block)
        if stream.read(1):
            _fail("ZIP archive has bytes beyond the measured input size")
        stream.seek(position)
        return size, digest.hexdigest()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, ScenePackageValidationError):
            raise
        raise ScenePackageValidationError("ZIP source must be readable and seekable") from exc


def _validate_zip_member_name(raw_name):
    if not raw_name or b"\x00" in raw_name:
        _fail("ZIP member name is empty or contains NUL")
    try:
        name = raw_name.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise ScenePackageValidationError("ZIP member paths must be ASCII") from exc
    if (
        name.startswith("/") or name.startswith("\\") or "\\" in name
        or ":" in name or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        _fail("ZIP member path is absolute, traversing, or malformed")
    return name


def _validate_dos_datetime(modified_time, modified_date):
    year = 1980 + ((modified_date >> 9) & 0x7F)
    month = (modified_date >> 5) & 0x0F
    day = modified_date & 0x1F
    hour = (modified_time >> 11) & 0x1F
    minute = (modified_time >> 5) & 0x3F
    second = (modified_time & 0x1F) * 2
    try:
        datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise ScenePackageValidationError("ZIP DOS timestamp is malformed") from exc


def _validate_file_attributes(create_system, external_attr, internal_attr, name):
    if internal_attr != 0:
        _fail("ZIP member uses unsupported internal attributes")
    dos_attributes = external_attr & 0xFFFF
    if dos_attributes & (0x08 | 0x10 | 0x40 | 0x400):  # volume, directory, device, or reparse point
        _fail("ZIP volume, directory, device, and reparse-point members are forbidden")
    if create_system == 3:
        mode = (external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type not in (0, stat.S_IFREG):
            _fail("ZIP symlink and special-file members are forbidden")
    if name.endswith("/"):
        _fail("ZIP directory entries are forbidden")


def _preflight_directory(stream, archive_size, limits):
    eocd_offset = archive_size - ZIP_EOCD.size
    raw_eocd = _read_exact_at(stream, eocd_offset, ZIP_EOCD.size)
    signature, disk, cd_disk, disk_entries, total_entries, cd_size, cd_offset, comment_size = ZIP_EOCD.unpack(raw_eocd)
    if signature != b"PK\x05\x06" or comment_size != 0:
        _fail("ZIP must have a fixed end record with no archive comment or trailing data")
    if disk != 0 or cd_disk != 0 or disk_entries != MAX_MEMBER_COUNT or total_entries != MAX_MEMBER_COUNT:
        _fail("ZIP must be a single-disk archive with exactly 27 members")
    if 0xFFFF in (disk_entries, total_entries) or 0xFFFFFFFF in (cd_size, cd_offset):
        _fail("ZIP64 archives are unsupported")
    if cd_size < MAX_MEMBER_COUNT * ZIP_CENTRAL.size or cd_size > MAX_CENTRAL_DIRECTORY_BYTES:
        _fail("ZIP central directory exceeds its bounded metadata budget")
    if cd_offset + cd_size != eocd_offset or cd_offset < 0:
        _fail("ZIP central-directory offsets are inconsistent or contain a preamble")

    members = []
    seen_names = set()
    cursor = cd_offset
    total_uncompressed = 0
    for _ in range(MAX_MEMBER_COUNT):
        fixed = _read_exact_at(stream, cursor, ZIP_CENTRAL.size)
        fields = ZIP_CENTRAL.unpack(fixed)
        (signature, version_made, version_needed, flags, method, modified_time, modified_date,
         crc, compressed_size, uncompressed_size, name_size, extra_size, file_comment_size,
         disk_start, internal_attr, external_attr, local_offset) = fields
        if signature != b"PK\x01\x02":
            _fail("ZIP central directory has a malformed member record")
        if name_size == 0 or name_size > MAX_MEMBER_NAME_BYTES or extra_size != 0 or file_comment_size != 0:
            _fail("ZIP member name or optional metadata length is invalid")
        if disk_start != 0 or flags != 0 or method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            _fail("ZIP member uses encryption, unsupported flags, disks, or compression method")
        create_system = version_made >> 8
        made_version = version_made & 0xFF
        if create_system not in (0, 3) or made_version not in (10, 20):
            _fail("ZIP creator/version metadata is unsupported")
        required_version = 10 if method == zipfile.ZIP_STORED else 20
        if version_needed != required_version:
            _fail("ZIP member version-needed metadata is incompatible")
        if 0xFFFFFFFF in (compressed_size, uncompressed_size, local_offset):
            _fail("ZIP64 member metadata is unsupported")
        _validate_dos_datetime(modified_time, modified_date)

        name_offset = cursor + ZIP_CENTRAL.size
        raw_name = _read_exact_at(stream, name_offset, name_size)
        name = _validate_zip_member_name(raw_name)
        folded_name = name.casefold()
        if folded_name in seen_names:
            _fail("ZIP contains duplicate or case-insensitive colliding names")
        seen_names.add(folded_name)
        if name not in ("package.json", "provenance.json") and not (
            BLEND_PATH.fullmatch(name) or FRAME_PATH.fullmatch(name)
        ):
            _fail("ZIP member name is outside the scene-package allowlist")
        _validate_file_attributes(create_system, external_attr, internal_attr, name)

        maximum = limits.max_json_bytes if name in ("package.json", "provenance.json") else (
            limits.max_blend_bytes if name.endswith(".blend") else limits.max_png_bytes
        )
        if uncompressed_size <= 0 or uncompressed_size > maximum:
            _fail("ZIP member declared size is empty or exceeds its type limit")
        if compressed_size <= 0 or uncompressed_size > compressed_size * limits.max_compression_ratio:
            _fail("ZIP member exceeds the maximum compression ratio")
        total_uncompressed += uncompressed_size
        if total_uncompressed > limits.max_expanded_total_bytes:
            _fail("ZIP declared expansion exceeds the 1 GiB total limit")

        local_fixed = _read_exact_at(stream, local_offset, ZIP_LOCAL.size)
        local = ZIP_LOCAL.unpack(local_fixed)
        (local_signature, local_version, local_flags, local_method, local_time, local_date,
         local_crc, local_compressed, local_uncompressed, local_name_size, local_extra_size) = local
        if local_signature != b"PK\x03\x04":
            _fail("ZIP local file header is malformed")
        if local_extra_size != 0 or local_name_size != name_size:
            _fail("ZIP local header optional metadata or name length is inconsistent")
        local_name = _read_exact_at(stream, local_offset + ZIP_LOCAL.size, local_name_size)
        if (
            local_name != raw_name or local_version != version_needed or local_flags != flags
            or local_method != method or local_time != modified_time or local_date != modified_date
            or local_crc != crc or local_compressed != compressed_size or local_uncompressed != uncompressed_size
        ):
            _fail("ZIP local and central member metadata disagree")
        data_offset = local_offset + ZIP_LOCAL.size + local_name_size
        data_end = data_offset + compressed_size
        if data_offset < 0 or data_end > cd_offset:
            _fail("ZIP member data overlaps or reaches the central directory")

        members.append(_CentralMember(
            name, flags, method, modified_time, modified_date, crc, compressed_size,
            uncompressed_size, local_offset, create_system, version_made, version_needed,
            external_attr, data_offset, data_end,
        ))
        cursor = name_offset + name_size
    if cursor != cd_offset + cd_size:
        _fail("ZIP central directory has unaccounted bytes or member records")

    ranges = sorted(members, key=lambda member: member.local_offset)
    expected_offset = 0
    for member in ranges:
        if member.local_offset != expected_offset:
            _fail("ZIP local records overlap or contain unexplained gaps/preamble")
        expected_offset = member.data_end
    if expected_offset != cd_offset:
        _fail("ZIP contains unexplained data before its central directory")
    return members


def _zip_infos_match(archive, members):
    infos = archive.infolist()
    if len(infos) != MAX_MEMBER_COUNT:
        _fail("ZIP must contain exactly 27 file members")
    by_name = {}
    for info, member in zip(infos, members):
        if info.filename != member.name or info.orig_filename != member.name:
            _fail("zipfile member names disagree with the preflighted directory")
        if (
            info.file_size != member.uncompressed_size or info.compress_size != member.compressed_size
            or info.CRC != member.crc or info.compress_type != member.method
            or info.flag_bits != member.flags or info.header_offset != member.local_offset
            or info.create_system != member.create_system or info.external_attr != member.external_attr
        ):
            _fail("zipfile metadata disagrees with the bounded central-directory preflight")
        by_name[member.name] = info
    return by_name


def _validate_package_json(value, limits):
    _exact_object(value, {
        "package_version", "record_type", "project_id", "stage_id", "scene_id",
        "timeline", "render", "payloads",
    }, "package.json")
    if not _is_int(value["package_version"]) or value["package_version"] != 1:
        _fail("package.json package_version must be integer 1")
    if value["record_type"] != "blender-scene-package":
        _fail("package.json record_type is unsupported")
    project_id = _require_slug(value["project_id"], "package.json project_id")
    if value["stage_id"] != "02-blender-scenes":
        _fail("package.json stage_id must be 02-blender-scenes")
    scene_id = _require_slug(value["scene_id"], "package.json scene_id")

    timeline = _exact_object(value["timeline"], {
        "source_start_frame", "source_end_frame_exclusive", "fps_num", "fps_den", "frame_count",
    }, "package.json timeline")
    fixed_timeline = {
        "source_start_frame": 0, "source_end_frame_exclusive": 24,
        "fps_num": 12, "fps_den": 1, "frame_count": 24,
    }
    for field, expected in fixed_timeline.items():
        if not _is_int(timeline[field]) or timeline[field] != expected:
            _fail(f"package.json timeline.{field} conflicts with the fixed 12 FPS [0,24) contract")

    render = _exact_object(value["render"], {"width", "height", "image_format"}, "package.json render")
    if (
        not _is_int(render["width"]) or render["width"] != 540
        or not _is_int(render["height"]) or render["height"] != 960
        or render["image_format"] != PNG_FORMAT
    ):
        _fail("package.json render does not match the 540x960 PNG_RGB8_SRGB profile")

    payloads = value["payloads"]
    if not isinstance(payloads, list) or len(payloads) != 25:
        _fail("package.json payloads must list scene.blend and exactly 24 PNG frames")
    expected_blend = f"scenes/{scene_id}/scene.blend"
    expected_frames = [f"scenes/{scene_id}/frames/frame-{frame:06d}.png" for frame in range(24)]
    parsed_payloads = []
    first = _exact_object(payloads[0], {"role", "path", "size_bytes", "sha256"}, "package.json payloads[0]")
    if first["role"] != "scene-blend" or first["path"] != expected_blend:
        _fail("package.json first payload must be the declared scene.blend")
    size = _require_int(first["size_bytes"], "scene.blend size_bytes", len(UNCOMPRESSED_BLEND_52_HEADER), limits.max_blend_bytes)
    digest = _require_sha256(first["sha256"], "scene.blend sha256")
    parsed_payloads.append({"path": expected_blend, "size_bytes": size, "sha256": digest, "role": "scene-blend"})

    for frame, item in enumerate(payloads[1:]):
        item = _exact_object(item, {"role", "path", "source_frame", "size_bytes", "sha256"}, f"package.json payloads[{frame + 1}]")
        if item["role"] != "frame-png" or not _is_int(item["source_frame"]) or item["source_frame"] != frame:
            _fail("package.json frame payload order/source_frame must be exactly 0 through 23")
        if item["path"] != expected_frames[frame]:
            _fail("package.json frame path does not match its source_frame")
        size = _require_int(item["size_bytes"], f"frame {frame} size_bytes", 1, limits.max_png_bytes)
        digest = _require_sha256(item["sha256"], f"frame {frame} sha256")
        parsed_payloads.append({
            "path": expected_frames[frame], "source_frame": frame,
            "size_bytes": size, "sha256": digest, "role": "frame-png",
        })
    return project_id, scene_id, parsed_payloads


def _validate_provenance(value):
    _exact_object(value, {
        "provenance_version", "record_type", "run_id", "project_id", "stage_id", "scene_id",
        "runtime", "inputs", "render", "observed",
    }, "provenance.json")
    if not _is_int(value["provenance_version"]) or value["provenance_version"] != 1:
        _fail("provenance.json provenance_version must be integer 1")
    if value["record_type"] != "productive-local-blender-scenes":
        _fail("provenance.json record_type is unsupported")
    run_id = _require_uuid(value["run_id"], "provenance.json run_id")
    project_id = _require_slug(value["project_id"], "provenance.json project_id")
    if value["stage_id"] != "02-blender-scenes":
        _fail("provenance.json stage_id must be 02-blender-scenes")
    scene_id = _require_slug(value["scene_id"], "provenance.json scene_id")

    runtime = _exact_object(value["runtime"], {
        "blender_version", "blender_version_string", "blender_version_cycle",
        "blender_executable_sha256", "worker_version", "worker_sha256",
    }, "provenance.json runtime")
    version = runtime["blender_version"]
    if (
        not isinstance(version, list) or len(version) != 3
        or any(not _is_int(part) for part in version)
        or version[0] != 5 or version[1] != 2 or not 0 <= version[2] <= 99
        or runtime["blender_version_string"] != f"{version[0]}.{version[1]}.{version[2]}"
        or runtime["blender_version_cycle"] != "release"
    ):
        _fail("provenance Blender runtime version must be an observed Blender 5.2.x release")
    _require_sha256(runtime["blender_executable_sha256"], "Blender executable sha256")
    _require_sha256(runtime["worker_sha256"], "Blender worker sha256")
    if not _is_int(runtime["worker_version"]) or runtime["worker_version"] != 1:
        _fail("provenance worker_version must be integer 1")

    inputs = _exact_object(value["inputs"], {
        "validated_plan_sha256", "stage_01_fingerprint", "stage_02_fingerprint", "input_hashes",
    }, "provenance.json inputs")
    for field in ("validated_plan_sha256", "stage_01_fingerprint", "stage_02_fingerprint"):
        _require_sha256(inputs[field], f"provenance inputs.{field}")
    input_hashes = inputs["input_hashes"]
    if not isinstance(input_hashes, list) or len(input_hashes) != 3:
        _fail("provenance input_hashes must contain storyboard, validated-plan, and one scene-spec resource")
    parsed_inputs = {}
    ordered_ids = []
    for index, item in enumerate(input_hashes):
        item = _exact_object(item, {"id", "sha256"}, f"provenance input_hashes[{index}]")
        input_id = item["id"]
        if not isinstance(input_id, str) or not (
            input_id in EXPECTED_INPUT_IDS or RESOURCE_INPUT.fullmatch(input_id)
        ):
            _fail("provenance input id is not an expected Stage 02 input")
        if input_id in parsed_inputs:
            _fail("provenance input_hashes contains duplicate IDs")
        ordered_ids.append(input_id)
        parsed_inputs[input_id] = _require_sha256(item["sha256"], f"provenance input {input_id} sha256")
    if set(parsed_inputs) & EXPECTED_INPUT_IDS != EXPECTED_INPUT_IDS or len(parsed_inputs) != 3:
        _fail("provenance input_hashes must include both fixed inputs and exactly one scene-spec resource")
    if ordered_ids != sorted(ordered_ids):
        _fail("provenance input_hashes must be sorted by id")
    if parsed_inputs["validated-plan"] != inputs["validated_plan_sha256"]:
        _fail("provenance validated_plan_sha256 disagrees with input_hashes")

    render = _exact_object(value["render"], {
        "policy_id", "engine", "device", "width", "height", "fps_num", "fps_den",
        "source_start_frame", "source_end_frame_exclusive", "samples", "threads", "seed",
        "denoising", "image_format",
    }, "provenance.json render")
    expected_render = {
        "policy_id": "blender-scene-policy-v1", "engine": "CYCLES", "device": "CPU",
        "width": 540, "height": 960, "fps_num": 12, "fps_den": 1,
        "source_start_frame": 0, "source_end_frame_exclusive": 24, "samples": 8,
        "threads": 2, "denoising": False, "image_format": PNG_FORMAT,
    }
    for field, expected in expected_render.items():
        current = render[field]
        if isinstance(expected, bool):
            matches = type(current) is bool and current is expected
        elif isinstance(expected, int):
            matches = _is_int(current) and current == expected
        else:
            matches = current == expected
        if not matches:
            _fail(f"provenance render.{field} conflicts with blender-scene-policy-v1")
    _require_int(render["seed"], "provenance render.seed", 0, 2147483647)

    observed = _exact_object(value["observed"], {
        "frames", "minimum_camera_target_distance_m", "render_seconds", "worker_seconds",
    }, "provenance.json observed")
    frames = observed["frames"]
    if not isinstance(frames, list) or any(not _is_int(frame) for frame in frames) or frames != list(range(24)):
        _fail("provenance observed.frames must be exactly integers 0 through 23")
    for field in ("minimum_camera_target_distance_m", "render_seconds", "worker_seconds"):
        metric = observed[field]
        if not _is_number(metric) or metric < 0:
            _fail(f"provenance observed.{field} must be a finite non-negative number")
    if observed["minimum_camera_target_distance_m"] <= 0.01:
        _fail("provenance minimum camera-target distance must exceed 0.01 m")
    return project_id, scene_id, run_id, runtime, inputs, render, observed


def _validate_context(context, package_values, provenance_values, archive_sha256):
    if context is None:
        return False
    if not isinstance(context, ScenePackageContext):
        _fail("expected_context must be a ScenePackageContext created from validated external data")
    project_id, scene_id, _ = package_values
    prov_project, prov_scene, run_id, _, inputs, _, _ = provenance_values
    if (
        _require_slug(context.project_id, "context project_id") != project_id
        or context.project_id != prov_project
        or _require_slug(context.scene_id, "context scene_id") != scene_id
        or context.scene_id != prov_scene
    ):
        _fail("scene package project/scene IDs do not match expected context")
    if _require_uuid(context.run_id, "context run_id") != run_id:
        _fail("scene package run_id does not match expected manifest context")
    stage_01 = _require_sha256(context.stage_01_fingerprint, "context stage_01_fingerprint")
    stage_02 = _require_sha256(context.stage_02_fingerprint, "context stage_02_fingerprint")
    if inputs["stage_01_fingerprint"] != stage_01 or inputs["stage_02_fingerprint"] != stage_02:
        _fail("scene package stage fingerprints do not match expected context")
    if not isinstance(context.input_hashes, dict):
        _fail("context input_hashes must be an object")
    expected_inputs = {}
    for input_id, digest in context.input_hashes.items():
        if not isinstance(input_id, str):
            _fail("context input IDs must be strings")
        expected_inputs[input_id] = _require_sha256(digest, f"context input {input_id} sha256")
    actual_inputs = {item["id"]: item["sha256"] for item in inputs["input_hashes"]}
    if expected_inputs != actual_inputs:
        _fail("scene package input hashes do not match expected project/storyboard/validated-plan context")
    if inputs["validated_plan_sha256"] != expected_inputs.get("validated-plan"):
        _fail("scene package validated-plan hash does not match expected context")
    expected_seed = _require_int(
        context.expected_render_seed, "context expected_render_seed", 0, 2147483647,
    )
    if provenance_values[5]["seed"] != expected_seed:
        _fail("scene package provenance render.seed does not match the validated Scene Spec context")
    if not isinstance(context.runtime, dict):
        _fail("context runtime must contain independently observed tool identity")
    runtime = provenance_values[3]
    if context.runtime != runtime:
        _fail("scene package Blender executable/worker identity does not match expected context")
    expected_archive = _require_sha256(context.manifest_scene_package_sha256, "context manifest scene-package sha256")
    if archive_sha256 != expected_archive:
        _fail("scene package archive SHA-256 does not match expected manifest artifact")
    return True


class _HashingReader:
    def __init__(self, stream, info, member, limits, total_state):
        self.stream = stream
        self.info = info
        self.member = member
        self.limits = limits
        self.total_state = total_state
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        if not _is_int(size) or size < 0:
            _fail("scene-package member reads must be explicitly bounded")
        block = self.stream.read(min(size, IO_CHUNK_BYTES))
        if not block:
            return b""
        self.count += len(block)
        self.total_state[0] += len(block)
        member_limit = self.limits.max_json_bytes if self.member.name in ("package.json", "provenance.json") else (
            self.limits.max_blend_bytes if self.member.name.endswith(".blend") else self.limits.max_png_bytes
        )
        if self.count > member_limit or self.count > self.info.file_size:
            _fail("actual ZIP member expansion exceeds its declared/type size limit")
        if self.total_state[0] > self.limits.max_expanded_total_bytes:
            _fail("actual ZIP expansion exceeds the 1 GiB total limit")
        if self.count > self.info.compress_size * self.limits.max_compression_ratio:
            _fail("actual ZIP member exceeds the maximum compression ratio")
        self.digest.update(block)
        return block


def _consume_member(archive, info, member, expected_size, expected_sha, limits, total_state, kind):
    if info.file_size != expected_size or info.file_size <= 0:
        _fail(f"ZIP member {member.name} declared size disagrees with package.json")
    if info.file_size > expected_size:
        _fail(f"ZIP member {member.name} exceeds the package.json size")
    try:
        with archive.open(info, "r") as raw:
            reader = _HashingReader(raw, info, member, limits, total_state)
            collected = bytearray() if kind == "json" else None
            if kind == "png":
                try:
                    verify_png_rgb8(reader)
                except PngValidationError as exc:
                    raise ScenePackageValidationError(f"PNG member {member.name} failed strict RGB8 decoding") from exc
            elif kind == "blend":
                header = bytearray()
                while len(header) < len(UNCOMPRESSED_BLEND_52_HEADER):
                    block = reader.read(len(UNCOMPRESSED_BLEND_52_HEADER) - len(header))
                    if not block:
                        _fail("scene.blend is shorter than its 17-byte uncompressed header")
                    header.extend(block)
                if bytes(header) != UNCOMPRESSED_BLEND_52_HEADER:
                    _fail("scene.blend uncompressed Blender 5.2 header is invalid")
                while reader.read(IO_CHUNK_BYTES):
                    pass
            else:
                while True:
                    block = reader.read(IO_CHUNK_BYTES)
                    if not block:
                        break
                    collected.extend(block)
            if reader.count != expected_size:
                _fail(f"ZIP member {member.name} actual size disagrees with package.json")
            digest = reader.digest.hexdigest()
            if expected_sha is not None and digest != expected_sha:
                _fail(f"ZIP member {member.name} SHA-256 disagrees with package.json")
            return (bytes(collected) if collected is not None else None), reader.count, digest
    except ScenePackageValidationError:
        raise
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, ValueError, TypeError, zlib.error) as exc:
        raise ScenePackageValidationError(f"ZIP member {member.name} failed full integrity read") from exc


def _validate_limits(limits):
    if not isinstance(limits, ScenePackageLimits):
        _fail("limits must be ScenePackageLimits")
    for field in (
        "max_archive_bytes", "max_expanded_total_bytes", "max_json_bytes", "max_blend_bytes",
        "max_png_bytes", "max_compression_ratio",
    ):
        value = getattr(limits, field)
        if not _is_int(value) or value <= 0:
            _fail(f"limits.{field} must be a positive integer")
    ceilings = {
        "max_archive_bytes": MAX_ARCHIVE_BYTES,
        "max_expanded_total_bytes": MAX_EXPANDED_TOTAL_BYTES,
        "max_json_bytes": MAX_JSON_BYTES,
        "max_blend_bytes": MAX_BLEND_BYTES,
        "max_png_bytes": MAX_PNG_BYTES,
        "max_compression_ratio": MAX_COMPRESSION_RATIO,
    }
    if any(getattr(limits, field) > ceiling for field, ceiling in ceilings.items()):
        _fail("injected test limits may lower, but never raise, the scene-package security ceilings")


def validate_scene_package(stream, *, expected_context=None, limits=DEFAULT_LIMITS):
    """Validate a seekable binary ZIP stream without extracting or writing files.

    The stream must begin at offset zero. Its position is restored before return/error.
    A successful result proves archive/member integrity and schema consistency only;
    Blender execution or `.blend` openability is never attested by this validator.
    """
    if limits is None:
        limits = DEFAULT_LIMITS
    _validate_limits(limits)
    try:
        original_position = stream.tell()
    except (AttributeError, OSError) as exc:
        raise ScenePackageValidationError("ZIP source must be a seekable binary stream") from exc
    if original_position != 0:
        _fail("ZIP stream must be positioned at byte zero")

    try:
        archive_size, initial_archive_sha = _archive_digest(stream, limits)
        members = _preflight_directory(stream, archive_size, limits)
        total_state = [0]
        try:
            with zipfile.ZipFile(stream, mode="r", allowZip64=False) as archive:
                infos = _zip_infos_match(archive, members)
                package_info = infos["package.json"]
                provenance_info = infos["provenance.json"]
                if package_info.file_size > limits.max_json_bytes or provenance_info.file_size > limits.max_json_bytes:
                    _fail("package.json or provenance.json exceeds the 1 MiB JSON limit")

                package_member = next(item for item in members if item.name == "package.json")
                provenance_member = next(item for item in members if item.name == "provenance.json")
                package_bytes, _, _ = _consume_member(
                    archive, package_info, package_member, package_info.file_size, None,
                    limits, total_state, "json",
                )
                provenance_bytes, _, _ = _consume_member(
                    archive, provenance_info, provenance_member, provenance_info.file_size, None,
                    limits, total_state, "json",
                )
                package_doc = _parse_json(package_bytes, "package.json")
                provenance_doc = _parse_json(provenance_bytes, "provenance.json")
                package_values = _validate_package_json(package_doc, limits)
                provenance_values = _validate_provenance(provenance_doc)

                project_id, scene_id, payloads = package_values
                prov_project, prov_scene, run_id, _, provenance_inputs, provenance_render, _ = provenance_values
                if project_id != prov_project or scene_id != prov_scene:
                    _fail("package.json and provenance.json IDs disagree")
                if (
                    package_doc["render"]["width"] != provenance_render["width"]
                    or package_doc["render"]["height"] != provenance_render["height"]
                    or package_doc["render"]["image_format"] != provenance_render["image_format"]
                    or package_doc["timeline"]["fps_num"] != provenance_render["fps_num"]
                    or package_doc["timeline"]["fps_den"] != provenance_render["fps_den"]
                    or package_doc["timeline"]["source_start_frame"] != provenance_render["source_start_frame"]
                    or package_doc["timeline"]["source_end_frame_exclusive"] != provenance_render["source_end_frame_exclusive"]
                ):
                    _fail("package.json and provenance.json render/timeline semantics disagree")

                expected_names = {
                    "package.json", "provenance.json",
                    f"scenes/{scene_id}/scene.blend",
                    *(f"scenes/{scene_id}/frames/frame-{frame:06d}.png" for frame in range(24)),
                }
                if {member.name for member in members} != expected_names:
                    _fail("ZIP members do not match the exact scene-package inventory")
                for member in members:
                    blend_match = BLEND_PATH.fullmatch(member.name)
                    frame_match = FRAME_PATH.fullmatch(member.name)
                    if (blend_match and blend_match.group(1) != scene_id) or (frame_match and frame_match.group(1) != scene_id):
                        _fail("ZIP payload path scene ID disagrees with the package index")

                context_verified = _validate_context(
                    expected_context, package_values, provenance_values, initial_archive_sha,
                )
                for entry in payloads:
                    member = next(item for item in members if item.name == entry["path"])
                    info = infos[entry["path"]]
                    kind = "blend" if entry["role"] == "scene-blend" else "png"
                    _consume_member(
                        archive, info, member, entry["size_bytes"], entry["sha256"],
                        limits, total_state, kind,
                    )
        except ScenePackageValidationError:
            raise
        except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, ValueError, TypeError, zlib.error, struct.error) as exc:
            raise ScenePackageValidationError("ZIP archive structure or member integrity is invalid") from exc

        final_size, final_archive_sha = _archive_digest(stream, limits)
        if final_size != archive_size or final_archive_sha != initial_archive_sha:
            _fail("ZIP archive changed while it was being validated")
        return ScenePackageResult(
            archive_sha256=final_archive_sha,
            project_id=project_id,
            scene_id=scene_id,
            run_id=run_id,
            member_count=MAX_MEMBER_COUNT,
            payload_count=len(payloads),
            context_verified=context_verified,
        )
    except ScenePackageValidationError:
        raise
    except (OSError, ValueError, TypeError, EOFError, zipfile.BadZipFile, RuntimeError, struct.error, zlib.error) as exc:
        raise ScenePackageValidationError("scene-package ZIP is invalid") from exc
    finally:
        try:
            stream.seek(original_position)
        except (AttributeError, OSError):
            pass
