"""Build one bounded scene-package ZIP from the fixed Blender staging inventory."""

import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import zipfile

from scene_package_validator import MAX_ARCHIVE_BYTES, MAX_BLEND_BYTES, MAX_PNG_BYTES


CHUNK_BYTES = 64 * 1024


def _identity(entry):
    if not entry.st_ino:
        raise ValueError("filesystem does not provide staged file identity")
    return entry.st_dev, entry.st_ino


def _staged_regular_file(root, relative, maximum):
    path = Path(root)
    root_entry = path.lstat()
    if stat.S_ISLNK(root_entry.st_mode) or not stat.S_ISDIR(root_entry.st_mode) or getattr(root_entry, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("staging root is a link, reparse point, or non-directory")
    parts = PurePosixPath(relative).parts
    for index, component in enumerate(parts):
        path /= component
        entry = path.lstat()
        if stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"staged output is a link or reparse point: {relative}")
        if index < len(parts) - 1 and not stat.S_ISDIR(entry.st_mode):
            raise ValueError(f"staged output parent is not a directory: {relative}")
    if not stat.S_ISREG(entry.st_mode) or not 0 < entry.st_size <= maximum:
        raise ValueError(f"staged output is missing, non-regular, or oversized: {relative}")
    return path, entry


def _digest_staged(path, expected_entry, maximum):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        if _identity(os.fstat(source.fileno())) != _identity(expected_entry):
            raise ValueError(f"staged output identity changed: {path}")
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            total += len(chunk)
            if total > maximum:
                raise ValueError(f"staged output exceeds size limit: {path}")
            digest.update(chunk)
    if _identity(path.lstat()) != _identity(expected_entry) or total != expected_entry.st_size:
        raise ValueError(f"staged output changed while hashing: {path}")
    return total, digest.hexdigest()


def _write_member(archive, name, source, expected_entry):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 10
    info.extract_version = 10
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.file_size = expected_entry.st_size
    with source.open("rb") as reader, archive.open(info, "w") as writer:
        if _identity(os.fstat(reader.fileno())) != _identity(expected_entry):
            raise ValueError(f"staged output identity changed: {source}")
        total = 0
        for chunk in iter(lambda: reader.read(CHUNK_BYTES), b""):
            total += len(chunk)
            if total > expected_entry.st_size:
                raise ValueError(f"staged output grew while packaging: {source}")
            writer.write(chunk)
        if total != expected_entry.st_size:
            raise ValueError(f"staged output shrank while packaging: {source}")
    if _identity(source.lstat()) != _identity(expected_entry):
        raise ValueError(f"staged output identity changed while packaging: {source}")


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def build_scene_package(staging_root, archive_path, *, project_id, scene_id, run_id,
                        runtime, input_hashes, stage_01_fingerprint, stage_02_fingerprint,
                        render_seed, observed):
    """Write an exclusive ZIP; return its SHA-256. The caller validates it before publication."""
    names = [f"scenes/{scene_id}/scene.blend"] + [
        f"scenes/{scene_id}/frames/frame-{frame:06d}.png" for frame in range(24)
    ]
    payloads = []
    sources = []
    total_source_bytes = 0
    for index, name in enumerate(names):
        maximum = MAX_BLEND_BYTES if index == 0 else MAX_PNG_BYTES
        source, entry = _staged_regular_file(staging_root, name, maximum)
        size, digest = _digest_staged(source, entry, maximum)
        item = {"role": "scene-blend" if index == 0 else "frame-png", "path": name,
                "size_bytes": size, "sha256": digest}
        if index:
            item["source_frame"] = index - 1
        payloads.append(item)
        sources.append((name, source, entry))
        total_source_bytes += size
    package = {
        "package_version": 1, "record_type": "blender-scene-package",
        "project_id": project_id, "stage_id": "02-blender-scenes", "scene_id": scene_id,
        "timeline": {"source_start_frame": 0, "source_end_frame_exclusive": 24,
                     "fps_num": 12, "fps_den": 1, "frame_count": 24},
        "render": {"width": 540, "height": 960, "image_format": "PNG_RGB8_SRGB"},
        "payloads": payloads,
    }
    provenance = {
        "provenance_version": 1, "record_type": "productive-local-blender-scenes",
        "run_id": run_id, "project_id": project_id, "stage_id": "02-blender-scenes",
        "scene_id": scene_id, "runtime": runtime,
        "inputs": {"validated_plan_sha256": input_hashes["validated-plan"],
                   "stage_01_fingerprint": stage_01_fingerprint,
                   "stage_02_fingerprint": stage_02_fingerprint,
                   "input_hashes": [{"id": key, "sha256": value} for key, value in sorted(input_hashes.items())]},
        "render": {"policy_id": "blender-scene-policy-v1", "engine": "CYCLES", "device": "CPU",
                   "width": 540, "height": 960, "fps_num": 12, "fps_den": 1,
                   "source_start_frame": 0, "source_end_frame_exclusive": 24,
                   "samples": 8, "threads": 2, "seed": render_seed,
                   "denoising": False, "image_format": "PNG_RGB8_SRGB"},
        "observed": observed,
    }
    documents = [("package.json", _json_bytes(package)), ("provenance.json", _json_bytes(provenance))]
    if any(len(data) > 1024 * 1024 for _, data in documents) or total_source_bytes + sum(len(data) for _, data in documents) + 10000 > MAX_ARCHIVE_BYTES:
        raise ValueError("scene package exceeds ZIP or JSON size limit")
    with Path(archive_path).open("xb") as target:
        with zipfile.ZipFile(target, "w", allowZip64=False) as archive:
            for name, data in documents:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.create_version = 10
                info.extract_version = 10
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, data)
            for name, source, entry in sources:
                _write_member(archive, name, source, entry)
                if target.tell() > MAX_ARCHIVE_BYTES:
                    raise ValueError("scene package exceeds ZIP size limit")
        target.flush()
        os.fsync(target.fileno())
    digest = hashlib.sha256()
    with Path(archive_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
