import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragx.commands import sprites_cmd


class Reader:
    def __init__(self, files):
        self.files = files

    def read(self, name):
        return self.files[name]


def sprite(pixel=1):
    palette = bytes([0, 0, 0, 0, 255, 0, 0, 0]) + bytes(1016)
    return b'SP\x01\x02' + struct.pack('<HHHHH', 1, 0, 1, 1, 1) + bytes([pixel]) + palette


def animation(x=0):
    return b'AC\x00\x01' + struct.pack('<H', 1) + bytes(10) + struct.pack('<I', 1) + bytes(32) + struct.pack('<IiiiI', 1, x, 0, 0, 0)


class SpriteBuildTests(unittest.TestCase):
    def test_shared_sheet_is_decoded_once_and_act_change_does_not_repack(self):
        files = {'data\\sprite\\robe.spr': sprite(), 'data\\sprite\\a.act': animation(),
                 'data\\sprite\\b.act': animation(1)}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reader = Reader(files)
            with patch.object(sprites_cmd, 'decode_spr_frames', wraps=sprites_cmd.decode_spr_frames) as decode:
                for name in ('a', 'b'):
                    sprites_cmd.export_one(reader, 'data\\sprite\\' + name + '.act', 'data\\sprite\\robe.spr', root)
                self.assertEqual(decode.call_count, 1)
            stamp = (root / 'robe.png').stat().st_mtime_ns
            files['data\\sprite\\b.act'] = animation(5)
            with patch.object(sprites_cmd, 'decode_spr_frames', side_effect=AssertionError('sheet was rebuilt')):
                sprites_cmd.export_one(Reader(files), 'data\\sprite\\b.act', 'data\\sprite\\robe.spr', root)
            self.assertEqual(stamp, (root / 'robe.png').stat().st_mtime_ns)
            meta = json.loads((root / 'b.json').read_text())
            self.assertEqual(meta['actions'][0]['frames'][0]['layers'][0][0], 5)

    def test_replacing_source_repairs_existing_sheet(self):
        files = {'data\\sprite\\robe.spr': sprite(), 'data\\sprite\\a.act': animation()}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sprites_cmd.export_one(Reader(files), 'data\\sprite\\a.act', 'data\\sprite\\robe.spr', root)
            original = (root / 'robe.png').read_bytes()
            files['data\\sprite\\robe.spr'] = sprite(2)
            sprites_cmd.export_one(Reader(files), 'data\\sprite\\a.act', 'data\\sprite\\robe.spr', root)
            self.assertNotEqual(original, (root / 'robe.png').read_bytes())


if __name__ == '__main__':
    unittest.main()
