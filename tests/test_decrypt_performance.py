"""Compare optimized header decryption with the original block traversal."""
import random
import struct
import unittest
from ragx import grf


class DecryptTests(unittest.TestCase):
    def test_header_only_matches_full_traversal_including_partial_tail(self):
        rng = random.Random(20211103)
        for size in (0, 1, 7, 8, 159, 160, 161, 1024, 65539):
            raw = rng.randbytes(size)
            expected = bytearray(raw)
            full = size // 8 * 8
            for number in range(full // 8):
                if number < 20:
                    block = struct.unpack_from('>Q', expected, number * 8)[0]
                    struct.pack_into('>Q', expected, number * 8, grf.decode_des_block(block))
            if full != size:
                tail = raw[full:] + bytes(8 - (size - full))
                value = grf.decode_des_block(int.from_bytes(tail, 'big')).to_bytes(8, 'big')
                expected[full:] = value[:size-full]
            self.assertEqual(grf.decrypt_entry(grf.FLAG_HEADER_DES, size, raw), bytes(expected))


if __name__ == '__main__':
    unittest.main()
