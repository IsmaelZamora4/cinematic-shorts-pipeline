import struct
import sys
import unittest
from unittest import mock
import zlib
from pathlib import Path

from test_pipeline import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import png_rgb8_decoder as decoder


WIDTH = 540
HEIGHT = 960
ROW_BYTES = 1 + WIDTH * 3
RASTER_BYTES = HEIGHT * ROW_BYTES
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _filter_row(raw, previous, filter_type):
    result = bytearray(len(raw))
    for i, value in enumerate(raw):
        left = raw[i - 3] if i >= 3 else 0
        up = previous[i] if previous else 0
        upper_left = previous[i - 3] if previous and i >= 3 else 0
        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = up
        elif filter_type == 3:
            predictor = (left + up) // 2
        else:
            p = left + up - upper_left
            distances = (abs(p - left), abs(p - up), abs(p - upper_left))
            predictor = (left, up, upper_left)[distances.index(min(distances))]
        result[i] = (value - predictor) & 0xFF
    return bytes([filter_type]) + result


def make_png(filters=None, *, split_idat=1, width=WIDTH, height=HEIGHT,
             bit_depth=8, color_type=2, compression=0, filtering=0, interlace=0, compressed=None,
             corrupt_crc=False, trailing=b"", extra_chunks=()):
    filters = filters or [0] * HEIGHT
    rows = []
    previous = b""
    for y in range(height):
        row = bytes((x * 7 + y * 11) & 0xFF for x in range(width * 3))
        rows.append(_filter_row(row, previous, filters[y % len(filters)]))
        previous = row
    packed = b"".join(rows)
    if compressed is None:
        compressed = zlib.compress(packed)
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, compression, filtering, interlace)
    parts = [SIGNATURE, chunk(b"IHDR", ihdr), *extra_chunks]
    pieces = [compressed[i:i + split_idat] for i in range(0, len(compressed), split_idat)] or [b""]
    for index, piece in enumerate(pieces):
        item = chunk(b"IDAT", piece)
        if corrupt_crc and index == 0:
            item = item[:-1] + bytes([item[-1] ^ 0x80])
        parts.append(item)
    parts.extend((chunk(b"IEND", b""), trailing))
    return b"".join(parts), packed


class BoundedStream:
    def __init__(self, data):
        self.data = data
        self.offset = 0
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("decoder attempted an unbounded read")
        result = self.data[self.offset:self.offset + size]
        self.offset += len(result)
        return result


class PngRgb8DecoderTests(unittest.TestCase):
    def assert_rejected(self, png, error=ValueError):
        with self.assertRaises(error):
            decoder.verify_png_rgb8(BoundedStream(png))

    def test_accepts_each_filter_and_split_idat(self):
        png, _ = make_png(filters=[0, 1, 2, 3, 4], split_idat=97)
        result = decoder.verify_png_rgb8(BoundedStream(png))
        self.assertEqual((WIDTH, HEIGHT), (result["width"], result["height"]))
        self.assertEqual(RASTER_BYTES, result["decoded_bytes"])

    def test_accepts_explicit_standard_blender_auxiliary_chunks(self):
        ancillary = [
            chunk(b"sRGB", b"\x00"),
            chunk(b"gAMA", struct.pack(">I", 45455)),
            chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)),
            chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
            chunk(b"tEXt", b"Software\x00Blender"),
            chunk(b"tIME", struct.pack(">H5B", 2026, 11, 6, 12, 0, 0)),
        ]
        png, _ = make_png(extra_chunks=ancillary)
        decoder.verify_png_rgb8(BoundedStream(png))

    def test_accepts_text_chunk_with_empty_text_and_valid_crc(self):
        png, _ = make_png(extra_chunks=[chunk(b"tEXt", b"Comment\x00")])
        decoder.verify_png_rgb8(BoundedStream(png))

    def test_rejects_text_chunk_with_extra_nul_and_valid_crc(self):
        png, _ = make_png(extra_chunks=[chunk(b"tEXt", b"Comment\x00first\x00second")])
        self.assert_rejected(png)

    def test_rejects_bad_signature_ihdr_dimensions_and_color_profiles(self):
        valid, _ = make_png()
        self.assert_rejected(b"X" + valid[1:])
        for options in (
            {"width": WIDTH - 1}, {"height": HEIGHT - 1}, {"bit_depth": 16},
            {"color_type": 6}, {"color_type": 3}, {"interlace": 1},
            {"compression": 1}, {"filtering": 1},
        ):
            with self.subTest(options=options):
                png, _ = make_png(**options)
                self.assert_rejected(png)

    def test_rejects_duplicate_or_misordered_ihdr_and_nonempty_iend(self):
        valid, _ = make_png()
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
        duplicate_ihdr = SIGNATURE + ihdr + ihdr + valid[len(SIGNATURE) + len(ihdr):]
        self.assert_rejected(duplicate_ihdr)
        misplaced = SIGNATURE + chunk(b"IDAT", b"") + valid[len(SIGNATURE):]
        self.assert_rejected(misplaced)
        nonempty_iend = valid[:-12] + chunk(b"IEND", b"x")
        self.assert_rejected(nonempty_iend)
        self.assert_rejected(valid[:-12])
        no_idat = SIGNATURE + ihdr + chunk(b"IEND", b"")
        self.assert_rejected(no_idat)

    def test_rejects_corrupt_chunk_crc_and_zlib_even_when_crc_is_recomputed(self):
        png, packed = make_png(corrupt_crc=True)
        self.assert_rejected(png)
        damaged = bytearray(zlib.compress(packed))
        damaged[len(damaged) // 2] ^= 0x40
        png, _ = make_png(compressed=bytes(damaged))
        self.assert_rejected(png)

    def test_checks_crc_for_header_and_end_chunks(self):
        valid, _ = make_png()
        damaged_ihdr = bytearray(valid)
        damaged_ihdr[29] ^= 1
        self.assert_rejected(bytes(damaged_ihdr))
        damaged_iend = bytearray(valid)
        damaged_iend[-1] ^= 1
        self.assert_rejected(bytes(damaged_iend))

    def test_rejects_truncated_stream_bad_adler_trailing_stream_and_extra_rows(self):
        png, packed = make_png()
        compressed = zlib.compress(packed)
        self.assert_rejected(make_png(compressed=compressed[:-2])[0])
        bad_adler = compressed[:-1] + bytes([compressed[-1] ^ 1])
        self.assert_rejected(make_png(compressed=bad_adler)[0])
        self.assert_rejected(make_png(compressed=compressed + b"extra")[0])
        self.assert_rejected(make_png(compressed=zlib.compress(packed + b"x"))[0])
        self.assert_rejected(make_png(compressed=zlib.compress(packed[:-ROW_BYTES]))[0])
        self.assert_rejected(make_png(compressed=zlib.compress(packed + bytes(ROW_BYTES)))[0])

    def test_rejects_invalid_filter_and_trailing_file_data(self):
        invalid_rows = bytes([5]) + bytes(WIDTH * 3)
        packed = invalid_rows + bytes(ROW_BYTES * (HEIGHT - 1))
        self.assert_rejected(make_png(compressed=zlib.compress(packed))[0])
        self.assert_rejected(make_png(trailing=b"extra")[0])

    def test_rejects_unknown_chunks_nonconsecutive_idat_and_duplicate_metadata(self):
        unknown_critical = chunk(b"ABCD", b"")
        self.assert_rejected(make_png(extra_chunks=[unknown_critical])[0])
        unknown_ancillary = chunk(b"vpAg", b"")
        self.assert_rejected(make_png(extra_chunks=[unknown_ancillary])[0])
        bad_reserved_bit = chunk(b"abca", b"")
        self.assert_rejected(make_png(extra_chunks=[bad_reserved_bit])[0])
        palette = chunk(b"PLTE", bytes((index % 256 for index in range(48))))
        self.assertTrue(decoder.verify_png_rgb8(BoundedStream(make_png(extra_chunks=[palette])[0])))
        self.assert_rejected(make_png(extra_chunks=[palette, palette])[0])
        png, packed = make_png()
        ihdr_end = len(SIGNATURE) + 25
        first_idat_length = struct.unpack(">I", png[ihdr_end:ihdr_end + 4])[0]
        first_idat_end = ihdr_end + 12 + first_idat_length
        between = chunk(b"tEXt", b"key\x00value")
        broken_order = png[:first_idat_end] + between + png[first_idat_end:]
        self.assert_rejected(broken_order)
        dup_srgb = [chunk(b"sRGB", b"\x00"), chunk(b"sRGB", b"\x01")]
        self.assert_rejected(make_png(extra_chunks=dup_srgb)[0])
        bad_srgb = [chunk(b"sRGB", b"\x04")]
        self.assert_rejected(make_png(extra_chunks=bad_srgb)[0])
        conflicting_gamma = [chunk(b"sRGB", b"\x00"), chunk(b"gAMA", struct.pack(">I", 40000))]
        self.assert_rejected(make_png(extra_chunks=conflicting_gamma)[0])

    def test_rejects_truncation_and_input_over_limit(self):
        valid, _ = make_png()
        for truncated in (valid[:0], valid[:7], valid[:33], valid[:-20]):
            with self.subTest(length=len(truncated)):
                self.assert_rejected(truncated)
        with mock.patch.object(decoder, "MAX_PNG_BYTES", len(valid) - 1):
            self.assert_rejected(valid)

    def test_reads_only_bounded_chunks_and_keeps_only_two_working_rows(self):
        png, _ = make_png()
        stream = BoundedStream(png)
        decoder.verify_png_rgb8(stream)
        self.assertLessEqual(max(stream.read_sizes), decoder.IO_CHUNK_BYTES)
        self.assertEqual(2 * WIDTH * 3, decoder.MAX_WORKING_ROW_BYTES)

    def test_rejects_expansion_above_exact_raster_size(self):
        too_much = bytes(RASTER_BYTES + 100)
        self.assert_rejected(make_png(compressed=zlib.compress(too_much))[0])


if __name__ == "__main__":
    unittest.main()
