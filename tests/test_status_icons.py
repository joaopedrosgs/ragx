import struct
import unittest

from ragx.commands.status_icons_cmd import decode_js_string, icon_file, merge, parse_robrowser
from ragx.lua import rebase_chunk


class RoBrowserStatusTableTests(unittest.TestCase):
    def test_byte_escapes_decode_as_one_cp949_run(self):
        # roBrowser writes Korean file names as \xNN escapes of their CP949 bytes.
        name = '수자쿠'
        escaped = ''.join('\\x%02x' % b for b in name.encode('cp949'))
        self.assertEqual(decode_js_string(escaped + '.tga'), name + '.tga')
        self.assertEqual(decode_js_string('i_accuracy'), 'i_accuracy')
        self.assertEqual(decode_js_string("it\\'s"), "it's")

    def test_icon_names_normalise_to_the_grfs_lower_case_tga(self):
        self.assertEqual(icon_file('SETMDEF.TGA'), 'setmdef.tga')
        self.assertEqual(icon_file('i_accuracy'), 'i_accuracy.tga')

    def test_entries_resolve_through_the_constant_table(self):
        const = 'export default {\n\tBLANK: -1,\n\tANGELUS: 9,\n\tBLESSING: 10,\n\tINC_AGI: 12,\n};'
        info = (
            "StatusInfo[SC.BLESSING] = {\n\ticon: 'efst_blessing.tga',\n\thaveTimeLimit: 1\n};\n"
            "StatusInfo[SC.INC_AGI] = {\n\thaveTimeLimit: 1,\n\ticon: 'i_agi'\n};\n"
            "StatusInfo[SC.UNKNOWN] = {\n\ticon: 'nope.tga'\n};\n"
            "StatusInfo[SC.ANGELUS] = {\n\tdescript: []\n};\n"
        )
        self.assertEqual(parse_robrowser(const, info),
                         {10: 'efst_blessing.tga', 12: 'i_agi.tga'})

    def test_the_client_table_wins_and_every_row_names_its_source(self):
        rows = merge({12: (1, 'client_agi.tga')},
                     {10: 'rb_blessing.tga', 12: 'rb_agi.tga'})
        self.assertEqual(rows[12], {'file': 'client_agi.tga', 'priority': 1, 'source': 'client'})
        self.assertEqual(rows[10], {'file': 'rb_blessing.tga', 'priority': 0, 'source': 'robrowser'})


def _chunk(size_t: int, strings: list[bytes]) -> bytes:
    """A minimal Lua 5.1 chunk: an empty main function holding string constants."""
    fmt = '<I' if size_t == 4 else '<Q'
    body = bytearray(b'\x1bLua' + bytes([0x51, 0, 1, 4, size_t, 4, 8, 0]))
    body += struct.pack(fmt, 0)              # source name (empty)
    body += struct.pack('<ii', 0, 0)         # linedefined, lastlinedefined
    body += bytes([0, 0, 2, 2])              # nups, numparams, is_vararg, maxstacksize
    body += struct.pack('<i', 0)             # code
    body += struct.pack('<i', len(strings))  # constants
    for text in strings:
        body += bytes([4]) + struct.pack(fmt, len(text)) + text
    body += struct.pack('<iiii', 0, 0, 0, 0)  # protos, lineinfo, locvars, upvalues
    return bytes(body)


class LuaChunkTests(unittest.TestCase):
    def test_a_32_bit_chunk_rebases_to_the_host_layout_byte_for_byte(self):
        self.assertEqual(rebase_chunk(_chunk(4, [b'EFST_BLESSING', b'']), 8),
                         _chunk(8, [b'EFST_BLESSING', b'']))

    def test_plain_text_and_matching_layouts_pass_through(self):
        self.assertEqual(rebase_chunk(b'x = 1'), b'x = 1')
        chunk = _chunk(8, [b'a'])
        self.assertIs(rebase_chunk(chunk, 8), chunk)

    def test_an_unknown_header_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            rebase_chunk(b'\x1bLua' + bytes([0x52, 0, 1, 4, 4, 4, 8, 0]))
        with self.assertRaises(ValueError):
            rebase_chunk(_chunk(4, [b'a']) + b'trailing', 8)


if __name__ == '__main__':
    unittest.main()
