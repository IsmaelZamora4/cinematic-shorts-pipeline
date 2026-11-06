import hashlib
import io
import json
import stat
import struct
import sys
import unittest
import warnings
import zipfile

from test_pipeline import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import scene_package_validator as validator
from test_png_rgb8_decoder import chunk, make_png


PROJECT_ID = "bronze-orbit-demo"
SCENE_ID = "bronze-orbit"
RUN_ID = "26fd6247-1a97-4dbf-b76b-f1bb9670b7b2"
INPUT_HASHES = {
    "resource-scene-spec": hashlib.sha256(b"scene spec").hexdigest(),
    "storyboard": hashlib.sha256(b"storyboard").hexdigest(),
    "validated-plan": hashlib.sha256(b"validated plan").hexdigest(),
}
STAGE_01_FINGERPRINT = hashlib.sha256(b"stage 01").hexdigest()
STAGE_02_FINGERPRINT = hashlib.sha256(b"stage 02").hexdigest()
VALID_PNG, _ = make_png()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _default_payloads(png):
    payloads = {f"scenes/{SCENE_ID}/scene.blend": b"BLENDER17-01v0502" + bytes(128)}
    payloads.update({f"scenes/{SCENE_ID}/frames/frame-{frame:06d}.png": png for frame in range(24)})
    return payloads


def _package_index(payloads):
    entries = [{
        "role": "scene-blend", "path": f"scenes/{SCENE_ID}/scene.blend",
        "size_bytes": len(payloads[f"scenes/{SCENE_ID}/scene.blend"]),
        "sha256": _sha(payloads[f"scenes/{SCENE_ID}/scene.blend"]),
    }]
    entries.extend({
        "role": "frame-png", "path": path, "source_frame": frame,
        "size_bytes": len(payloads[path]), "sha256": _sha(payloads[path]),
    } for frame in range(24) for path in [f"scenes/{SCENE_ID}/frames/frame-{frame:06d}.png"])
    return {
        "package_version": 1,
        "record_type": "blender-scene-package",
        "project_id": PROJECT_ID,
        "stage_id": "02-blender-scenes",
        "scene_id": SCENE_ID,
        "timeline": {
            "source_start_frame": 0, "source_end_frame_exclusive": 24,
            "fps_num": 12, "fps_den": 1, "frame_count": 24,
        },
        "render": {"width": 540, "height": 960, "image_format": "PNG_RGB8_SRGB"},
        "payloads": entries,
    }


def _provenance():
    return {
        "provenance_version": 1,
        "record_type": "productive-local-blender-scenes",
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "stage_id": "02-blender-scenes",
        "scene_id": SCENE_ID,
        "runtime": {
            "blender_version": [5, 2, 3],
            "blender_version_string": "5.2.3",
            "blender_version_cycle": "release",
            "blender_executable_sha256": _sha(b"blender executable"),
            "worker_version": 1,
            "worker_sha256": _sha(b"worker"),
        },
        "inputs": {
            "validated_plan_sha256": INPUT_HASHES["validated-plan"],
            "stage_01_fingerprint": STAGE_01_FINGERPRINT,
            "stage_02_fingerprint": STAGE_02_FINGERPRINT,
            "input_hashes": [
                {"id": item, "sha256": digest} for item, digest in sorted(INPUT_HASHES.items())
            ],
        },
        "render": {
            "policy_id": "blender-scene-policy-v1",
            "engine": "CYCLES", "device": "CPU", "width": 540, "height": 960,
            "fps_num": 12, "fps_den": 1, "source_start_frame": 0,
            "source_end_frame_exclusive": 24, "samples": 8, "threads": 2,
            "seed": 31415, "denoising": False, "image_format": "PNG_RGB8_SRGB",
        },
        "observed": {
            "frames": list(range(24)),
            "minimum_camera_target_distance_m": 0.12,
            "render_seconds": 22.5,
            "worker_seconds": 24.0,
        },
    }


def _zip_records(payloads, package, provenance, *, package_raw=None, provenance_raw=None):
    records = [
        ("package.json", package_raw if package_raw is not None else _json_bytes(package), zipfile.ZIP_DEFLATED, 3, (stat.S_IFREG | 0o600) << 16, 0),
        ("provenance.json", provenance_raw if provenance_raw is not None else _json_bytes(provenance), zipfile.ZIP_DEFLATED, 3, (stat.S_IFREG | 0o600) << 16, 0),
    ]
    records.extend((name, value, zipfile.ZIP_DEFLATED, 3, (stat.S_IFREG | 0o600) << 16, 0) for name, value in payloads.items())
    return records


def _write_zip(records):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", allowZip64=False) as archive:
            for name, data, method, create_system, external_attr, flags in records:
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 6, 12, 0, 0))
                info.compress_type = method
                info.create_system = create_system
                info.external_attr = external_attr
                info.flag_bits = flags
                archive.writestr(info, data)
    return stream.getvalue()


def make_archive(*, package_change=None, provenance_change=None, payload_change=None,
                 transform_records=None, package_raw=None, provenance_raw=None):
    payloads = _default_payloads(VALID_PNG)
    if payload_change:
        payload_change(payloads)
    package = _package_index(payloads)
    provenance = _provenance()
    if package_change:
        package_change(package)
    if provenance_change:
        provenance_change(provenance)
    records = _zip_records(payloads, package, provenance, package_raw=package_raw, provenance_raw=provenance_raw)
    if transform_records:
        records = transform_records(records)
    return _write_zip(records)


def expected_context(archive_bytes):
    return validator.ScenePackageContext(
        project_id=PROJECT_ID,
        scene_id=SCENE_ID,
        run_id=RUN_ID,
        stage_01_fingerprint=STAGE_01_FINGERPRINT,
        stage_02_fingerprint=STAGE_02_FINGERPRINT,
        input_hashes=dict(INPUT_HASHES),
        runtime=dict(_provenance()["runtime"]),
        expected_render_seed=31415,
        manifest_scene_package_sha256=_sha(archive_bytes),
    )


def _replace_png_idat_data(png):
    output = bytearray(png[:8])
    offset = 8
    changed = False
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + length]
        if kind == b"IDAT" and not changed and data:
            damaged = bytearray(data)
            damaged[0] ^= 0x80
            output.extend(chunk(kind, bytes(damaged)))
            changed = True
        else:
            output.extend(chunk(kind, data))
        offset += 12 + length
    if not changed:
        raise AssertionError("fixture had no IDAT data")
    return bytes(output)


def _patch_member_flags(archive, name, flags):
    data = bytearray(archive)
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        info = source.getinfo(name)
        data[info.header_offset + 6:info.header_offset + 8] = struct.pack("<H", flags)
    name_bytes = name.encode("ascii")
    central_name = data.rfind(name_bytes)
    if central_name < 46:
        raise AssertionError("central-directory member not found")
    central_start = central_name - 46
    data[central_start + 8:central_start + 10] = struct.pack("<H", flags)
    return bytes(data)


def _patch_central_member_field(archive, name, offset, value):
    data = bytearray(archive)
    name_bytes = name.encode("ascii")
    central_name = data.rfind(name_bytes)
    if central_name < 46:
        raise AssertionError("central-directory member not found")
    central_start = central_name - 46
    data[central_start + offset:central_start + offset + len(value)] = value
    return bytes(data)


class ScenePackageValidatorTests(unittest.TestCase):
    def setUp(self):
        self.png = VALID_PNG

    def assert_rejected(self, archive_bytes, *, context=None, limits=None, pattern=None):
        with self.assertRaisesRegex(validator.ScenePackageValidationError, pattern or ".*"):
            validator.validate_scene_package(io.BytesIO(archive_bytes), expected_context=context, limits=limits)

    def test_validates_complete_package_and_external_context(self):
        archive = make_archive()
        stream = io.BytesIO(archive)
        result = validator.validate_scene_package(stream, expected_context=expected_context(archive))
        self.assertEqual(_sha(archive), result.archive_sha256)
        self.assertEqual(PROJECT_ID, result.project_id)
        self.assertEqual(SCENE_ID, result.scene_id)
        self.assertEqual(27, result.member_count)
        self.assertTrue(result.context_verified)
        self.assertFalse(result.execution_verified)
        self.assertFalse(result.blend_openability_verified)
        self.assertEqual(0, stream.tell())
        self.assertFalse(stream.closed)
        self.assertEqual(archive, stream.getvalue())

    def test_context_binds_scene_package_seed_to_validated_scene_spec(self):
        matching_archive = make_archive()
        matching_result = validator.validate_scene_package(
            io.BytesIO(matching_archive), expected_context=expected_context(matching_archive),
        )
        self.assertTrue(matching_result.context_verified)

        changed_archive = make_archive(provenance_change=lambda provenance: provenance["render"].update(seed=123456))
        with self.assertRaisesRegex(validator.ScenePackageValidationError, "seed|Scene Spec|context"):
            validator.validate_scene_package(
                io.BytesIO(changed_archive), expected_context=expected_context(changed_archive),
            )

    def test_context_rejects_invalid_expected_scene_spec_seeds(self):
        archive = make_archive()
        base_context = expected_context(archive)
        for invalid_seed in (True, 31415.0, "31415", -1, 2147483648):
            with self.subTest(seed=invalid_seed):
                context = validator.ScenePackageContext(
                    **{**base_context.__dict__, "expected_render_seed": invalid_seed},
                )
                with self.assertRaisesRegex(validator.ScenePackageValidationError, "expected_render_seed|seed"):
                    validator.validate_scene_package(io.BytesIO(archive), expected_context=context)

    def test_rejects_missing_and_additional_members(self):
        archive = make_archive()
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            records = [(info.filename, source.read(info), info.compress_type, 3, info.external_attr, 0) for info in source.infolist()]
        self.assert_rejected(_write_zip(records[:-1]), pattern="27|member|inventory")
        self.assert_rejected(_write_zip(records + [("extra.txt", b"x", zipfile.ZIP_STORED, 3, (stat.S_IFREG | 0o600) << 16, 0)]), pattern="27|member|inventory")

    def test_rejects_duplicate_and_case_insensitive_names(self):
        def duplicate(records):
            return records[:-1] + [records[2]]
        self.assert_rejected(make_archive(transform_records=duplicate), pattern="duplicate|collision")

        def case_collision(records):
            replaced = list(records)
            name, data, method, system, attr, flags = replaced[-1]
            replaced[-1] = ("PACKAGE.JSON", data, method, system, attr, flags)
            return replaced
        self.assert_rejected(make_archive(transform_records=case_collision), pattern="case-insensitive|duplicate|collision")

    def test_rejects_unsafe_member_names(self):
        for unsafe in ("../escape.png", "/absolute.png", "C:/drive.png", "//server/share", "scene//bad.png", "scene\\bad.png"):
            with self.subTest(name=unsafe):
                def replace(records, unsafe=unsafe):
                    changed = list(records)
                    name, *tail = changed[-1]
                    changed[-1] = (unsafe, *tail)
                    return changed
                self.assert_rejected(make_archive(transform_records=replace), pattern="path|member name|ASCII")

    def test_rejects_nul_in_raw_central_directory_name(self):
        archive = bytearray(make_archive())
        name = f"scenes/{SCENE_ID}/frames/frame-000000.png".encode("ascii")
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            info = source.getinfo(name.decode("ascii"))
            archive[info.header_offset + 30 + name.index(b"frame")] = 0
        central_index = archive.rfind(name)
        self.assertGreater(central_index, 0)
        archive[central_index + name.index(b"frame")] = 0
        self.assert_rejected(bytes(archive), pattern="NUL|name|path")

    def test_rejects_directory_symlink_and_special_file_metadata(self):
        for label, name, mode, create_system in (
            ("directory", f"scenes/{SCENE_ID}/scene.blend", stat.S_IFDIR | 0o755, 3),
            ("symlink", f"scenes/{SCENE_ID}/scene.blend", stat.S_IFLNK | 0o777, 3),
            ("fifo", f"scenes/{SCENE_ID}/scene.blend", stat.S_IFIFO | 0o600, 3),
        ):
            with self.subTest(kind=label):
                def mutate(records, name=name, mode=mode, create_system=create_system):
                    changed = list(records)
                    old, data, method, _, _, flags = changed[2]
                    changed[2] = (name, data, method, create_system, mode << 16, flags)
                    return changed
                self.assert_rejected(make_archive(transform_records=mutate), pattern="directory|regular|special|symbolic")

    def test_rejects_dos_volume_and_device_attributes(self):
        for label, attributes in (("volume", 0x08), ("device", 0x40)):
            with self.subTest(kind=label):
                def mutate(records, attributes=attributes):
                    changed = list(records)
                    name, data, method, _, _, flags = changed[2]
                    changed[2] = (name, data, method, 0, attributes, flags)
                    return changed
                self.assert_rejected(make_archive(transform_records=mutate), pattern="special|volume|device|attribute")

    def test_rejects_encryption_and_unsupported_compression(self):
        encrypted = _patch_member_flags(make_archive(), "package.json", 1)
        self.assert_rejected(encrypted, pattern="flag|encrypt")

        def bzip2(records):
            changed = list(records)
            name, data, _, system, attr, flags = changed[2]
            changed[2] = (name, data, zipfile.ZIP_BZIP2, system, attr, flags)
            return changed
        self.assert_rejected(make_archive(transform_records=bzip2), pattern="compression|method")

    def test_rejects_corrupt_local_header_metadata(self):
        archive = bytearray(make_archive())
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            info = source.getinfo("package.json")
            archive[info.header_offset + 6] = 1  # encryption flag in local header only
        self.assert_rejected(bytes(archive), pattern="local|flag|metadata")

    def test_rejects_inconsistent_central_directory_metadata(self):
        archive = make_archive()
        archive = _patch_central_member_field(archive, "package.json", 16, b"\\x00\\x00\\x00\\x00")
        self.assert_rejected(archive, pattern="local|central|metadata|CRC")

        archive = make_archive()
        archive = _patch_central_member_field(archive, "package.json", 24, b"\\xff\\xff\\xff\\x7f")
        self.assert_rejected(archive, pattern="size|limit|local|central|metadata")

    def test_rejects_trailing_archive_bytes_and_comments(self):
        archive = make_archive()
        self.assert_rejected(archive + b"trailing", pattern="end record|trailing|structure")

        archive = bytearray(make_archive())
        eocd = len(archive) - 22
        archive[eocd + 20:eocd + 22] = struct.pack("<H", 1)
        archive.extend(b"x")
        self.assert_rejected(bytes(archive), pattern="comment|end record|structure")

    def test_rejects_multidisk_zip64_and_central_extra_metadata(self):
        archive = bytearray(make_archive())
        eocd = len(archive) - 22
        multidisk = bytearray(archive)
        multidisk[eocd + 4:eocd + 6] = struct.pack("<H", 1)
        self.assert_rejected(bytes(multidisk), pattern="single-disk|27 members")
        zip64 = bytearray(archive)
        zip64[eocd + 10:eocd + 12] = struct.pack("<H", 0xFFFF)
        self.assert_rejected(bytes(zip64), pattern="27|ZIP64")
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            info = source.getinfo("package.json")
        central_name = archive.rfind(b"package.json")
        self.assertGreater(central_name, info.header_offset)
        central_offset = central_name - 46
        with_extra = bytearray(archive)
        with_extra[central_offset + 30:central_offset + 32] = struct.pack("<H", 1)
        self.assert_rejected(bytes(with_extra), pattern="optional metadata|length")

    def test_rejects_member_crc_corruption_when_read_through_eof(self):
        def store_blend(records):
            changed = list(records)
            name, data, _, system, attr, flags = changed[2]
            changed[2] = (name, data, zipfile.ZIP_STORED, system, attr, flags)
            return changed
        archive = bytearray(make_archive(transform_records=store_blend))
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            info = source.getinfo(f"scenes/{SCENE_ID}/scene.blend")
            name_len, extra_len = struct.unpack("<HH", archive[info.header_offset + 26:info.header_offset + 30])
            payload_offset = info.header_offset + 30 + name_len + extra_len
        archive[payload_offset] ^= 1
        self.assert_rejected(bytes(archive), pattern="CRC|corrupt|member")

    def test_rejects_payload_hash_mismatch(self):
        archive = make_archive(package_change=lambda package: package["payloads"][0].update(sha256="0" * 64))
        self.assert_rejected(archive, pattern="SHA-256|hash|payload")

    def test_rejects_corrupt_png_even_when_png_and_zip_crc_are_recalculated(self):
        corrupted = _replace_png_idat_data(self.png)
        archive = make_archive(payload_change=lambda payloads: payloads.__setitem__(
            f"scenes/{SCENE_ID}/frames/frame-000000.png", corrupted))
        self.assert_rejected(archive, pattern="PNG|zlib|checksum|chunk")

    def test_rejects_strict_json_errors_and_index_semantic_mutation(self):
        for raw in (b'{"x":1,"x":2}', b'{"package_version":NaN}', b"\xef\xbb\xbf{}"):
            archive = make_archive(package_raw=raw)
            self.assert_rejected(archive, pattern="JSON|duplicate|UTF-8|BOM|schema")
        archive = make_archive(package_change=lambda package: package["timeline"].update(fps_num=30))
        self.assert_rejected(archive, pattern="timeline|FPS|12")
        archive = make_archive(provenance_change=lambda provenance: provenance["runtime"].update(blender_version_string="5.3.0"))
        self.assert_rejected(archive, pattern="version|runtime|Blender")

    def test_rejects_unknown_json_fields(self):
        archive = make_archive(package_change=lambda package: package.update(unreviewed=True))
        self.assert_rejected(archive, pattern="unknown|schema|field")
        archive = make_archive(provenance_change=lambda provenance: provenance.update(machine_path="C:/private"))
        self.assert_rejected(archive, pattern="unknown|schema|field")

    def test_rejects_invalid_blend_header_even_when_index_hash_matches(self):
        archive = make_archive(payload_change=lambda payloads: payloads.__setitem__(
            f"scenes/{SCENE_ID}/scene.blend", b"not-a-blend" + bytes(128)))
        self.assert_rejected(archive, pattern="blend|header|Blender")

    def test_accepts_uncompressed_blender_52_17_byte_header(self):
        archive = make_archive(payload_change=lambda payloads: payloads.__setitem__(
            f"scenes/{SCENE_ID}/scene.blend", b"BLENDER17-01v0502" + bytes(128)))
        result = validator.validate_scene_package(
            io.BytesIO(archive), expected_context=expected_context(archive),
        )
        self.assertTrue(result.context_verified)

    def test_rejects_legacy_compressed_truncated_and_altered_blend_headers(self):
        headers = (
            b"BLENDER_v502",                         # former synthetic 12-byte fixture
            b"BLENDER17-01v050",                    # truncated 17-byte header
            bytes.fromhex("28b52ffda0410601000d3700aa47981149"),  # Zstandard frame
            b"BLENDER18-01v0502",                  # format marker
            b"BLENDER17-00v0502",                  # architecture marker
            b"BLENDER17-01V0502",                  # version marker
            b"BLENDER17-01v0503",                  # different Blender version
        )
        for header in headers:
            with self.subTest(header=header):
                payload = header if len(header) < 17 else header + bytes(128)
                archive = make_archive(payload_change=lambda payloads: payloads.__setitem__(
                    f"scenes/{SCENE_ID}/scene.blend", payload))
                self.assert_rejected(archive, pattern="blend|header|Blender")

    def test_enforces_limits_with_injected_small_budgets(self):
        archive = make_archive()
        limits = validator.ScenePackageLimits(max_archive_bytes=1024)
        self.assert_rejected(archive, limits=limits, pattern="archive|512 MiB|limit")
        limits = validator.ScenePackageLimits(max_expanded_total_bytes=1024)
        self.assert_rejected(archive, limits=limits, pattern="expanded|total|limit")
        limits = validator.ScenePackageLimits(max_compression_ratio=1)
        self.assert_rejected(archive, limits=limits, pattern="ratio|compression")
        limits = validator.ScenePackageLimits(max_json_bytes=8)
        self.assert_rejected(archive, limits=limits, pattern="JSON|size|limit")
        limits = validator.ScenePackageLimits(max_blend_bytes=100)
        self.assert_rejected(archive, limits=limits, pattern="blend|size|limit")
        limits = validator.ScenePackageLimits(max_png_bytes=100)
        self.assert_rejected(archive, limits=limits, pattern="PNG|size|limit")
        with self.assertRaises(validator.ScenePackageValidationError):
            validator.validate_scene_package(
                io.BytesIO(archive),
                limits=validator.ScenePackageLimits(max_archive_bytes=validator.MAX_ARCHIVE_BYTES + 1),
            )

    def test_expected_context_must_match_project_inputs_and_manifest_digest(self):
        archive = make_archive()
        context = expected_context(archive)
        self.assert_rejected(archive, context=validator.ScenePackageContext(
            **{**context.__dict__, "project_id": "other-project"}), pattern="context|project")
        self.assert_rejected(archive, context=validator.ScenePackageContext(
            **{**context.__dict__, "manifest_scene_package_sha256": "0" * 64}), pattern="manifest|archive|SHA-256")
        self.assert_rejected(archive, context=validator.ScenePackageContext(
            **{**context.__dict__, "run_id": "00000000-0000-0000-0000-000000000000"}), pattern="run_id|context")
        self.assert_rejected(archive, context=validator.ScenePackageContext(
            **{**context.__dict__, "stage_02_fingerprint": "0" * 64}), pattern="fingerprint|context")
        changed_inputs = dict(INPUT_HASHES, storyboard="0" * 64)
        self.assert_rejected(archive, context=validator.ScenePackageContext(
            **{**context.__dict__, "input_hashes": changed_inputs}), pattern="context|input|storyboard")
        changed_runtime = dict(context.runtime, worker_sha256="0" * 64)
        self.assert_rejected(archive, context=validator.ScenePackageContext(
            **{**context.__dict__, "runtime": changed_runtime}), pattern="context|executable|worker")


if __name__ == "__main__":
    unittest.main()
