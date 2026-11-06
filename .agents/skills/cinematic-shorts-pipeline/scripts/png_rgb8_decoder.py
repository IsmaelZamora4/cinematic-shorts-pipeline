#!/usr/bin/env python3
"""Strict, bounded, read-only validator for the Blender MVP PNG profile."""

import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXPECTED_WIDTH = 540
EXPECTED_HEIGHT = 960
CHANNELS = 3
BYTES_PER_PIXEL = CHANNELS
ROW_DATA_BYTES = EXPECTED_WIDTH * CHANNELS
ROW_BYTES = ROW_DATA_BYTES + 1  # filter byte plus RGB samples
EXPECTED_DECODED_BYTES = EXPECTED_HEIGHT * ROW_BYTES

# Per file, including PNG signature/chunk framing. Reads are smaller and bounded.
MAX_PNG_BYTES = 32 * 1024 * 1024
IO_CHUNK_BYTES = 64 * 1024
MAX_WORKING_ROW_BYTES = 2 * ROW_DATA_BYTES
MAX_TEXT_CHUNK_BYTES = 4096
MAX_EXIF_CHUNK_BYTES = 4096

_ALLOWED_AUXILIARY_CHUNKS = {
    b"cHRM", b"eXIf", b"gAMA", b"oFFs", b"pHYs", b"sRGB", b"tEXt", b"tIME",
}
_SINGLETON_CHUNKS = {b"cHRM", b"eXIf", b"gAMA", b"oFFs", b"pHYs", b"sRGB", b"tIME"}
_SRGB_CHROMATICITIES = (31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
_TIFF_FIELD_TYPE_BYTES = {
    1: 1,  2: 1,  3: 2,  4: 4,  5: 8,  6: 1,
    7: 1,  8: 2,  9: 4, 10: 8, 11: 4, 12: 8,
}


class PngValidationError(ValueError):
    """The stream is not a complete PNG in the supported RGB8 profile."""


class _BoundedReader:
    def __init__(self, stream):
        self.stream = stream
        self.bytes_read = 0

    def read_exact(self, length):
        if length < 0 or length > MAX_PNG_BYTES - self.bytes_read:
            raise PngValidationError("PNG exceeds the 32 MiB input limit")
        result = bytearray()
        while len(result) < length:
            block = self.stream.read(min(length - len(result), IO_CHUNK_BYTES))
            if not block:
                raise PngValidationError("PNG is truncated")
            if not isinstance(block, (bytes, bytearray, memoryview)):
                raise PngValidationError("PNG stream must return binary data")
            result.extend(block)
            self.bytes_read += len(block)
            if self.bytes_read > MAX_PNG_BYTES:
                raise PngValidationError("PNG exceeds the 32 MiB input limit")
        return bytes(result)

    def read_chunk_data(self, length, crc, consumer=None, collect=False):
        collected = bytearray() if collect else None
        remaining = length
        while remaining:
            block = self.read_exact(min(remaining, IO_CHUNK_BYTES))
            crc = zlib.crc32(block, crc)
            if consumer is not None:
                consumer(block)
            if collected is not None:
                collected.extend(block)
            remaining -= len(block)
        expected_crc = struct.unpack(">I", self.read_exact(4))[0]
        if (crc & 0xFFFFFFFF) != expected_crc:
            raise PngValidationError("PNG chunk CRC mismatch")
        return bytes(collected) if collected is not None else None


def _paeth(left, up, upper_left):
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left


class _RasterDecoder:
    """Incrementally consumes zlib output and retains only adjacent scanlines."""

    def __init__(self):
        self.zlib = zlib.decompressobj()
        self.output_bytes = 0
        self.rows = 0
        self.pending = bytearray()
        self.previous = bytearray(ROW_DATA_BYTES)
        self.has_previous = False

    def feed(self, compressed):
        if self.zlib.eof and compressed:
            raise PngValidationError("PNG IDAT contains bytes after the zlib stream")
        pending_input = compressed
        while pending_input:
            max_output = min(IO_CHUNK_BYTES, EXPECTED_DECODED_BYTES - self.output_bytes + 1)
            try:
                decoded = self.zlib.decompress(pending_input, max_output)
            except zlib.error as exc:
                raise PngValidationError("PNG zlib payload or checksum is corrupt") from exc
            pending_input = self.zlib.unconsumed_tail
            if self.zlib.unused_data:
                raise PngValidationError("PNG IDAT contains trailing compressed data")
            if decoded:
                self.output_bytes += len(decoded)
                if self.output_bytes > EXPECTED_DECODED_BYTES:
                    raise PngValidationError("PNG expands beyond the exact raster size")
                self._consume_scanlines(decoded)
            if not pending_input:
                break

    def _consume_scanlines(self, decoded):
        self.pending.extend(decoded)
        while len(self.pending) >= ROW_BYTES:
            row = bytearray(self.pending[:ROW_BYTES])
            del self.pending[:ROW_BYTES]
            self._reconstruct(row)
            self.rows += 1
            if self.rows > EXPECTED_HEIGHT:
                raise PngValidationError("PNG contains too many scanlines")

    def _reconstruct(self, encoded):
        filter_type = encoded[0]
        if filter_type not in (0, 1, 2, 3, 4):
            raise PngValidationError("PNG scanline uses an unsupported filter")
        row = encoded[1:]
        if len(row) != ROW_DATA_BYTES:
            raise PngValidationError("PNG scanline has an invalid length")
        previous = self.previous
        for index in range(ROW_DATA_BYTES):
            left = row[index - BYTES_PER_PIXEL] if index >= BYTES_PER_PIXEL else 0
            up = previous[index] if self.has_previous else 0
            upper_left = previous[index - BYTES_PER_PIXEL] if self.has_previous and index >= BYTES_PER_PIXEL else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            else:
                predictor = _paeth(left, up, upper_left)
            row[index] = (row[index] + predictor) & 0xFF
        self.previous = row
        self.has_previous = True

    def finish(self):
        if not self.zlib.eof:
            raise PngValidationError("PNG zlib stream is incomplete")
        if self.zlib.unused_data or self.zlib.unconsumed_tail:
            raise PngValidationError("PNG zlib stream has unconsumed data")
        if self.pending:
            raise PngValidationError("PNG final scanline is truncated")
        if self.rows != EXPECTED_HEIGHT or self.output_bytes != EXPECTED_DECODED_BYTES:
            raise PngValidationError("PNG does not contain exactly 960 RGB8 scanlines")


def _valid_keyword(payload):
    separator = payload.find(b"\x00")
    if separator < 1 or separator > 79 or payload.find(b"\x00", separator + 1) != -1:
        return False
    keyword = payload[:separator]
    if keyword[0] == 32 or keyword[-1] == 32 or b"  " in keyword:
        return False
    return all(32 <= value <= 126 or 161 <= value <= 255 for value in keyword)


def _validate_exif(data):
    """Validate bounded TIFF headers, IFD tables, and directly referenced values."""
    if len(data) < 8:
        raise PngValidationError("PNG eXIf TIFF header is truncated")
    if data[:2] == b"II":
        endian = "<"
    elif data[:2] == b"MM":
        endian = ">"
    else:
        raise PngValidationError("PNG eXIf TIFF byte order is invalid")
    if struct.unpack_from(endian + "H", data, 2)[0] != 42:
        raise PngValidationError("PNG eXIf TIFF magic is invalid")

    def validate_ifd(offset, inspect_value_references):
        if offset < 8 or offset + 2 > len(data):
            raise PngValidationError("PNG eXIf IFD offset is outside its payload")
        entry_count = struct.unpack_from(endian + "H", data, offset)[0]
        entries_start = offset + 2
        entries_end = entries_start + entry_count * 12
        if entries_end + 4 > len(data):
            raise PngValidationError("PNG eXIf IFD table is truncated")

        if inspect_value_references:
            for index in range(entry_count):
                entry_offset = entries_start + index * 12
                field_type = struct.unpack_from(endian + "H", data, entry_offset + 2)[0]
                value_count = struct.unpack_from(endian + "I", data, entry_offset + 4)[0]
                field_bytes = _TIFF_FIELD_TYPE_BYTES.get(field_type)
                if field_bytes is None:
                    raise PngValidationError("PNG eXIf IFD contains an unsupported TIFF field type")
                value_bytes = value_count * field_bytes
                if value_bytes > 4:
                    value_offset = struct.unpack_from(endian + "I", data, entry_offset + 8)[0]
                    if value_offset < 8 or value_offset + value_bytes > len(data):
                        raise PngValidationError("PNG eXIf value reference is outside its payload")

        next_ifd_offset = struct.unpack_from(endian + "I", data, entries_end)[0]
        return next_ifd_offset

    first_ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
    next_ifd_offset = validate_ifd(first_ifd_offset, True)
    if next_ifd_offset:
        # Validate one referenced table only; do not follow an arbitrary IFD chain.
        validate_ifd(next_ifd_offset, False)


def _validate_auxiliary(kind, data, seen):
    if kind not in _ALLOWED_AUXILIARY_CHUNKS:
        raise PngValidationError("PNG contains an unsupported ancillary chunk")
    if kind in _SINGLETON_CHUNKS and kind in seen:
        raise PngValidationError("PNG contains a duplicate singleton chunk")
    if kind == b"eXIf":
        _validate_exif(data)
    if kind == b"oFFs":
        if len(data) != 9:
            raise PngValidationError("PNG oFFs chunk has an invalid length")
        _, _, unit = struct.unpack(">iiB", data)
        if unit not in (0, 1):
            raise PngValidationError("PNG oFFs chunk has an invalid unit")
    if kind == b"cHRM":
        if len(data) != 32 or any(value > 100000 for value in struct.unpack(">8I", data)):
            raise PngValidationError("PNG cHRM chunk has invalid data")
    if kind == b"gAMA" and (len(data) != 4 or struct.unpack(">I", data)[0] == 0):
        raise PngValidationError("PNG gAMA chunk has invalid data")
    if kind == b"sRGB" and (len(data) != 1 or data[0] > 3):
        raise PngValidationError("PNG sRGB chunk has invalid data")
    if kind == b"pHYs" and (len(data) != 9 or data[8] not in (0, 1)):
        raise PngValidationError("PNG pHYs chunk has invalid data")
    if kind == b"tEXt" and (len(data) > MAX_TEXT_CHUNK_BYTES or not _valid_keyword(data)):
        raise PngValidationError("PNG tEXt chunk has invalid data")
    if kind == b"tIME":
        if len(data) != 7:
            raise PngValidationError("PNG tIME chunk has an invalid length")
        year, month, day, hour, minute, second = struct.unpack(">H5B", data)
        if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59 and second <= 60):
            raise PngValidationError("PNG tIME chunk has invalid fields")
        if year < 1:
            raise PngValidationError("PNG tIME chunk has an invalid year")
    seen.add(kind)


def verify_png_rgb8(stream):
    """Validate one supported PNG from a binary stream without retaining its raster.

    Accepted output is fixed at 540x960, RGB, 8-bit, non-interlaced. Only the
    explicit cHRM/eXIf/gAMA/oFFs/pHYs/sRGB/tEXt/tIME ancillary allowlist is
    accepted. eXIf is capped at 4 KiB and structurally checked without following
    unbounded TIFF pointer chains; all other ancillary chunks retain the 32-byte
    default limit, except tEXt which retains its existing 4 KiB limit.
    The function reads at most 64 KiB per call and retains two 1,620-byte rows,
    a bounded zlib output block, and bounded chunk metadata.
    """
    reader = _BoundedReader(stream)
    if reader.read_exact(8) != PNG_SIGNATURE:
        raise PngValidationError("invalid PNG signature")

    seen = set()
    got_ihdr = False
    got_idat = False
    idat_ended = False
    got_iend = False
    got_plte = False
    decoder = _RasterDecoder()
    chunks = 0
    color_metadata = {}

    while not got_iend:
        chunk_header = reader.read_exact(8)
        length, kind = struct.unpack(">I4s", chunk_header)
        chunks += 1
        if chunks > 100000:
            raise PngValidationError("PNG has too many chunks")
        if not all((65 <= byte <= 90) or (97 <= byte <= 122) for byte in kind):
            raise PngValidationError("PNG chunk type is malformed")
        if kind[2] & 0x20:
            raise PngValidationError("PNG chunk reserved bit is invalid")
        crc = zlib.crc32(kind)

        if not got_ihdr:
            if kind != b"IHDR" or length != 13:
                raise PngValidationError("IHDR must be the first unique 13-byte chunk")
            ihdr = reader.read_chunk_data(length, crc, collect=True)
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
            if (width, height) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
                raise PngValidationError("PNG dimensions must be exactly 540x960")
            if (depth, color, compression, filtering, interlace) != (8, 2, 0, 0, 0):
                raise PngValidationError("PNG must use non-interlaced truecolor RGB8 profile")
            got_ihdr = True
            seen.add(kind)
            continue

        if kind == b"IHDR":
            raise PngValidationError("PNG contains duplicate IHDR")
        if kind == b"IDAT":
            if idat_ended:
                raise PngValidationError("PNG IDAT chunks must be consecutive")
            got_idat = True
            reader.read_chunk_data(length, crc, consumer=decoder.feed)
            continue

        if got_idat:
            idat_ended = True
            decoder.finish()

        if kind == b"IEND":
            if length != 0 or not got_idat:
                raise PngValidationError("IEND must be empty and follow IDAT")
            expected_crc = struct.unpack(">I", reader.read_exact(4))[0]
            if (crc & 0xFFFFFFFF) != expected_crc:
                raise PngValidationError("PNG chunk CRC mismatch")
            got_iend = True
            break

        if kind == b"PLTE":
            if got_idat or got_plte or not 3 <= length <= 768 or length % 3:
                raise PngValidationError("PNG PLTE chunk is invalid for this profile")
            reader.read_chunk_data(length, crc)
            seen.add(kind)
            got_plte = True
            continue

        if kind[0] & 0x20 == 0:
            raise PngValidationError("PNG contains an unknown critical chunk")
        if kind == b"oFFs" and got_idat:
            raise PngValidationError("PNG oFFs chunk must precede IDAT")
        if (got_idat or got_plte) and kind in {b"cHRM", b"gAMA", b"pHYs", b"sRGB"}:
            raise PngValidationError("PNG color/physical metadata must precede PLTE and IDAT")
        if kind == b"eXIf":
            allowed_max = MAX_EXIF_CHUNK_BYTES
        elif kind == b"tEXt":
            allowed_max = MAX_TEXT_CHUNK_BYTES
        else:
            allowed_max = 32
        if length > allowed_max:
            raise PngValidationError("PNG ancillary chunk exceeds its size limit")
        data = reader.read_chunk_data(length, crc, collect=True)
        _validate_auxiliary(kind, data, seen)
        if kind in {b"cHRM", b"gAMA", b"sRGB"}:
            color_metadata[kind] = data

    if b"sRGB" in seen:
        if b"gAMA" in color_metadata and struct.unpack(">I", color_metadata[b"gAMA"])[0] != 45455:
            raise PngValidationError("PNG gAMA conflicts with its sRGB declaration")
        if b"cHRM" in color_metadata and struct.unpack(">8I", color_metadata[b"cHRM"]) != _SRGB_CHROMATICITIES:
            raise PngValidationError("PNG cHRM conflicts with its sRGB declaration")

    trailing = stream.read(1)
    if trailing:
        raise PngValidationError("PNG has trailing bytes after IEND")
    if reader.bytes_read > MAX_PNG_BYTES:
        raise PngValidationError("PNG exceeds the 32 MiB input limit")
    return {
        "width": EXPECTED_WIDTH,
        "height": EXPECTED_HEIGHT,
        "bit_depth": 8,
        "color_type": 2,
        "decoded_bytes": decoder.output_bytes,
        "row_count": decoder.rows,
        "chunks": chunks,
    }
