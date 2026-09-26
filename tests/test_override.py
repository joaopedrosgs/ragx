import json
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ragx.commands import override_cmd


class Reader:
    """A GRF stand-in: normalized names -> bytes, with fingerprints."""

    def __init__(self, files):
        self.files = files

    def read(self, name):
        return self.files[name]

    def __contains__(self, name):
        return name in self.files

    def namelist(self):
        return list(self.files)

    def fingerprint(self, name):
        return "fp-" + str(len(self.files[name]))


def sprite():
    # v2.1 SPR: one 2x1 indexed frame (indices 1, 0) with an RLE body.
    palette = bytes([0, 0, 0, 0, 200, 10, 20, 0]) + bytes(1016)
    rle = bytes([1, 0, 1])
    return (b'SP\x01\x02' + struct.pack('<HHHH', 1, 0, 2, 1) + struct.pack('<H', len(rle)) + rle
            + palette)


def animation():
    # v2.1 ACT: one action, one frame, one layer, event 0 -> "slime.wav".
    layer = struct.pack('<iiiI', 3, -4, 0, 0) + bytes([255, 255, 255, 255]) \
        + struct.pack('<fii', 1.0, 0, 0)
    frame = bytes(32) + struct.pack('<I', 1) + layer + struct.pack('<i', 0)
    events = struct.pack('<I', 1) + b'slime.wav'.ljust(40, b'\0')
    return b'AC\x01\x02' + struct.pack('<H', 1) + bytes(10) + struct.pack('<I', 1) + frame + events


def files():
    return {
        'data\\sprite\\몬스터\\slime.spr': sprite(),
        'data\\sprite\\몬스터\\slime.act': animation(),
        'data\\wav\\slime.wav': b'RIFFfake',
    }


class OverrideTests(unittest.TestCase):
    def test_extracts_the_selected_dependency_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'content' / 'mob' / '9999'
            result = override_cmd.extract(Reader(files()), '몬스터/slime', dest)
            self.assertEqual(result['frames'], 1)
            self.assertEqual(result['sounds'], 1)
            document = json.loads((dest / 'animation.json').read_text(encoding='utf-8'))
            self.assertEqual(document['format'], override_cmd.FORMAT)
            self.assertEqual(document['frames'], ['frames/000.png'])
            self.assertEqual(document['actions'][0]['frames'][0]['layers'], [[3, -4, 0, 0]])
            self.assertEqual(document['sounds'], {'slime.wav': 'sounds/slime.wav'})
            pixels = Image.open(dest / 'frames' / '000.png').convert('RGBA')
            self.assertEqual(pixels.getpixel((0, 0)), (200, 10, 20, 255))
            self.assertEqual(pixels.getpixel((1, 0))[3], 0)  # index 0 is transparent
            self.assertEqual((dest / 'sounds' / 'slime.wav').read_bytes(), b'RIFFfake')
            provenance = json.loads((dest / 'provenance.json').read_text(encoding='utf-8'))
            self.assertIn('data\\sprite\\몬스터\\slime.act', provenance['sources'])
            self.assertTrue(all(v.startswith('fp-') for v in provenance['sources'].values()))

    def test_never_overwrites_authored_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'mob'
            dest.mkdir()
            (dest / 'edited.png').write_bytes(b'mine')
            with self.assertRaises(FileExistsError):
                override_cmd.extract(Reader(files()), '몬스터/slime', dest)
            self.assertEqual((dest / 'edited.png').read_bytes(), b'mine')

    def test_failure_leaves_no_partial_folder(self):
        broken = files()
        broken['data\\sprite\\몬스터\\slime.spr'] = b'SP\x01\x02' + bytes(3)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'mob'
            with self.assertRaises(Exception):
                override_cmd.extract(Reader(broken), '몬스터/slime', dest)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_sound_is_reported_not_fatal(self):
        partial = files()
        del partial['data\\wav\\slime.wav']
        with tempfile.TemporaryDirectory() as tmp:
            result = override_cmd.extract(Reader(partial), '몬스터/slime', Path(tmp) / 'm')
            self.assertEqual(result['missing_sounds'], ['slime.wav'])


if __name__ == '__main__':
    unittest.main()
