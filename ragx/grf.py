"""GRF archive reader.

Supports GRF version 0x200 (the only version used by modern clients,
including the LATAM client) and the legacy 0x102/0x103 layouts.

Details that matter:
- File names inside the archive are encoded in CP949 (Korean). We decode
  them and index files by the lowercased, backslash-normalized path.
- Some entries are DES-encrypted (flag 0x02 = "mixcrypt", flag 0x04 =
  "header DES"). The cipher is a broken single-round DES with a zero key
  that Gravity ships; the implementation below is a direct port of
  korangar's `mixcrypt.rs` (itself derived from the RustCrypto `des`
  crate).
- Entry offsets are relative to the end of the 46-byte header.

Thread safety: `GrfArchive.read()` may be called from multiple threads;
each call opens its own view into a shared memory-map.
"""

from __future__ import annotations

import mmap
import os
import struct
import threading
import zlib
from dataclasses import dataclass

HEADER_SIZE = 46
HEADER_MAGIC = b"Master of Magic\0"
# Newer clients (late 2024+, including the LATAM client) use a revised
# format with 64-bit offsets and this magic instead. GRF Editor calls the
# version 0x300; the last two magic bytes vary, so only 14 are compared.
HEADER_MAGIC_V3 = b"Event Horizon\0"

# Entry flags
FLAG_FILE = 0x01
FLAG_MIXCRYPT = 0x02
FLAG_HEADER_DES = 0x04

_HEADER_BLOCKS = 0x14  # first 0x14 blocks are always encrypted
_BLOCK = 8

_MASK64 = 0xFFFFFFFFFFFFFFFF

_S_BOXES = [
    [
        0x0E, 0x00, 0x04, 0x0F, 0x0D, 0x07, 0x01, 0x04, 0x02, 0x0E, 0x0F, 0x02, 0x0B, 0x0D, 0x08, 0x01,
        0x03, 0x0A, 0x0A, 0x06, 0x06, 0x0C, 0x0C, 0x0B, 0x05, 0x09, 0x09, 0x05, 0x00, 0x03, 0x07, 0x08,
        0x04, 0x0F, 0x01, 0x0C, 0x0E, 0x08, 0x08, 0x02, 0x0D, 0x04, 0x06, 0x09, 0x02, 0x01, 0x0B, 0x07,
        0x0F, 0x05, 0x0C, 0x0B, 0x09, 0x03, 0x07, 0x0E, 0x03, 0x0A, 0x0A, 0x00, 0x05, 0x06, 0x00, 0x0D,
    ],
    [
        0x0F, 0x03, 0x01, 0x0D, 0x08, 0x04, 0x0E, 0x07, 0x06, 0x0F, 0x0B, 0x02, 0x03, 0x08, 0x04, 0x0E,
        0x09, 0x0C, 0x07, 0x00, 0x02, 0x01, 0x0D, 0x0A, 0x0C, 0x06, 0x00, 0x09, 0x05, 0x0B, 0x0A, 0x05,
        0x00, 0x0D, 0x0E, 0x08, 0x07, 0x0A, 0x0B, 0x01, 0x0A, 0x03, 0x04, 0x0F, 0x0D, 0x04, 0x01, 0x02,
        0x05, 0x0B, 0x08, 0x06, 0x0C, 0x07, 0x06, 0x0C, 0x09, 0x00, 0x03, 0x05, 0x02, 0x0E, 0x0F, 0x09,
    ],
    [
        0x0A, 0x0D, 0x00, 0x07, 0x09, 0x00, 0x0E, 0x09, 0x06, 0x03, 0x03, 0x04, 0x0F, 0x06, 0x05, 0x0A,
        0x01, 0x02, 0x0D, 0x08, 0x0C, 0x05, 0x07, 0x0E, 0x0B, 0x0C, 0x04, 0x0B, 0x02, 0x0F, 0x08, 0x01,
        0x0D, 0x01, 0x06, 0x0A, 0x04, 0x0D, 0x09, 0x00, 0x08, 0x06, 0x0F, 0x09, 0x03, 0x08, 0x00, 0x07,
        0x0B, 0x04, 0x01, 0x0F, 0x02, 0x0E, 0x0C, 0x03, 0x05, 0x0B, 0x0A, 0x05, 0x0E, 0x02, 0x07, 0x0C,
    ],
    [
        0x07, 0x0D, 0x0D, 0x08, 0x0E, 0x0B, 0x03, 0x05, 0x00, 0x06, 0x06, 0x0F, 0x09, 0x00, 0x0A, 0x03,
        0x01, 0x04, 0x02, 0x07, 0x08, 0x02, 0x05, 0x0C, 0x0B, 0x01, 0x0C, 0x0A, 0x04, 0x0E, 0x0F, 0x09,
        0x0A, 0x03, 0x06, 0x0F, 0x09, 0x00, 0x00, 0x06, 0x0C, 0x0A, 0x0B, 0x01, 0x07, 0x0D, 0x0D, 0x08,
        0x0F, 0x09, 0x01, 0x04, 0x03, 0x05, 0x0E, 0x0B, 0x05, 0x0C, 0x02, 0x07, 0x08, 0x02, 0x04, 0x0E,
    ],
    [
        0x02, 0x0E, 0x0C, 0x0B, 0x04, 0x02, 0x01, 0x0C, 0x07, 0x04, 0x0A, 0x07, 0x0B, 0x0D, 0x06, 0x01,
        0x08, 0x05, 0x05, 0x00, 0x03, 0x0F, 0x0F, 0x0A, 0x0D, 0x03, 0x00, 0x09, 0x0E, 0x08, 0x09, 0x06,
        0x04, 0x0B, 0x02, 0x08, 0x01, 0x0C, 0x0B, 0x07, 0x0A, 0x01, 0x0D, 0x0E, 0x07, 0x02, 0x08, 0x0D,
        0x0F, 0x06, 0x09, 0x0F, 0x0C, 0x00, 0x05, 0x09, 0x06, 0x0A, 0x03, 0x04, 0x00, 0x05, 0x0E, 0x03,
    ],
    [
        0x0C, 0x0A, 0x01, 0x0F, 0x0A, 0x04, 0x0F, 0x02, 0x09, 0x07, 0x02, 0x0C, 0x06, 0x09, 0x08, 0x05,
        0x00, 0x06, 0x0D, 0x01, 0x03, 0x0D, 0x04, 0x0E, 0x0E, 0x00, 0x07, 0x0B, 0x05, 0x03, 0x0B, 0x08,
        0x09, 0x04, 0x0E, 0x03, 0x0F, 0x02, 0x05, 0x0C, 0x02, 0x09, 0x08, 0x05, 0x0C, 0x0F, 0x03, 0x0A,
        0x07, 0x0B, 0x00, 0x0E, 0x04, 0x01, 0x0A, 0x07, 0x01, 0x06, 0x0D, 0x00, 0x0B, 0x08, 0x06, 0x0D,
    ],
    [
        0x04, 0x0D, 0x0B, 0x00, 0x02, 0x0B, 0x0E, 0x07, 0x0F, 0x04, 0x00, 0x09, 0x08, 0x01, 0x0D, 0x0A,
        0x03, 0x0E, 0x0C, 0x03, 0x09, 0x05, 0x07, 0x0C, 0x05, 0x02, 0x0A, 0x0F, 0x06, 0x08, 0x01, 0x06,
        0x01, 0x06, 0x04, 0x0B, 0x0B, 0x0D, 0x0D, 0x08, 0x0C, 0x01, 0x03, 0x04, 0x07, 0x0A, 0x0E, 0x07,
        0x0A, 0x09, 0x0F, 0x05, 0x06, 0x00, 0x08, 0x0F, 0x00, 0x0E, 0x05, 0x02, 0x09, 0x03, 0x02, 0x0C,
    ],
    [
        0x0D, 0x01, 0x02, 0x0F, 0x08, 0x0D, 0x04, 0x08, 0x06, 0x0A, 0x0F, 0x03, 0x0B, 0x07, 0x01, 0x04,
        0x0A, 0x0C, 0x09, 0x05, 0x03, 0x06, 0x0E, 0x0B, 0x05, 0x00, 0x00, 0x0E, 0x0C, 0x09, 0x07, 0x02,
        0x07, 0x02, 0x0B, 0x01, 0x04, 0x0E, 0x01, 0x07, 0x09, 0x04, 0x0C, 0x0A, 0x0E, 0x08, 0x02, 0x0D,
        0x00, 0x0F, 0x06, 0x0C, 0x0A, 0x09, 0x0D, 0x00, 0x0F, 0x03, 0x03, 0x05, 0x05, 0x06, 0x08, 0x0B,
    ],
]


def _delta_swap(a: int, delta: int, mask: int) -> int:
    b = (a ^ (a >> delta)) & mask
    return a ^ b ^ ((b << delta) & _MASK64)


def _ip(m: int) -> int:
    m = _delta_swap(m, 9, 0x0055005500550055)
    m = _delta_swap(m, 18, 0x0000333300003333)
    m = _delta_swap(m, 36, 0x000000000F0F0F0F)
    m = _delta_swap(m, 24, 0x00000000FF00FF00)
    return _delta_swap(m, 24, 0x000000FF000000FF)


def _fp(m: int) -> int:
    m = _delta_swap(m, 24, 0x000000FF000000FF)
    m = _delta_swap(m, 24, 0x00000000FF00FF00)
    m = _delta_swap(m, 36, 0x000000000F0F0F0F)
    m = _delta_swap(m, 18, 0x0000333300003333)
    return _delta_swap(m, 9, 0x0055005500550055)


def _expand(block: int) -> int:
    b1 = (block << 31) & 0x8000000000000000
    b2 = (block >> 1) & 0x7C00000000000000
    b3 = (block >> 3) & 0x03F0000000000000
    b4 = (block >> 5) & 0x000FC00000000000
    b5 = (block >> 7) & 0x00003F0000000000
    b6 = (block >> 9) & 0x000000FC00000000
    b7 = (block >> 11) & 0x00000003F0000000
    b8 = (block >> 13) & 0x000000000FC00000
    b9 = (block >> 15) & 0x00000000003E0000
    b10 = (block >> 47) & 0x0000000000010000
    return b1 | b2 | b3 | b4 | b5 | b6 | b7 | b8 | b9 | b10


def _rotl64(v: int, r: int) -> int:
    return ((v << r) | (v >> (64 - r))) & _MASK64


def _sbox_substitute(value: int) -> int:
    out = 0
    for index in range(8):
        val = (value >> (58 - index * 6)) & 0x3F
        out |= _S_BOXES[index][val] << (60 - index * 4)
    return out


def _pbox_permute(block: int) -> int:
    block = _rotl64(block, 44)
    b1 = (block & 0x0000000000200000) << 32
    b2 = (block & 0x0000000000480000) << 13
    b3 = (block & 0x0000088000000000) << 12
    b4 = (block & 0x0000002020120000) << 25
    b5 = (block & 0x0000000442000000) << 14
    b6 = (block & 0x0000000001800000) << 37
    b7 = (block & 0x0000000004000000) << 24
    b8 = ((block & 0x0000020280015000) * 0x0000020080800083) & 0x02000A6400000000
    b9 = (_rotl64(block, 29) & 0x01001400000000AA) * 0x0000210210008081 & 0x0902C01200000000
    b10 = ((block & 0x0000000910040000) * 0x0000000C04000020) & 0x8410010000000000
    return (b1 | b2 | b3 | b4 | b5 | b6 | b7 | b8 | b9 | b10) & _MASK64


def _feistel(right: int) -> int:
    # Key is zero in Gravity's scheme, so the XOR with the round key is a no-op.
    return _pbox_permute(_sbox_substitute(_expand(right)))


def _des_round(value: int) -> int:
    left = value & (0xFFFFFFFF << 32)
    right = (value << 32) & _MASK64
    return right | ((_feistel(right) ^ left) >> 32)


def decode_des_block(block: int) -> int:
    block = _ip(block)
    block = _des_round(block)
    # Gravity accidentally swapped the sides.
    block = _rotl64(block, 32)
    return _fp(block)


_SCRAMBLE_SUBST = {
    0x00: 0x2B, 0x2B: 0x00, 0x01: 0x68, 0x68: 0x01, 0x48: 0x77, 0x77: 0x48,
    0x60: 0xFF, 0xFF: 0x60, 0x6C: 0x80, 0x80: 0x6C, 0xB9: 0xC0, 0xC0: 0xB9,
    0xEB: 0xFE, 0xFE: 0xEB,
}

_SHUFFLE = (3, 4, 6, 0, 1, 2, 5)


def _scramble_block(block: bytearray, start: int) -> None:
    copy = bytes(block[start:start + 8])
    for index, position in enumerate(_SHUFFLE):
        block[start + index] = copy[position]
    block[start + 7] = _SCRAMBLE_SUBST.get(copy[7], copy[7])


def _count_digits(size: int) -> int:
    digits = 0
    while size > 0:
        size //= 10
        digits += 1
    return max(digits, 1)


def _encryption_cycle(digits: int) -> int:
    if digits < 3:
        return 3
    if digits < 5:
        return digits + 1
    if digits < 7:
        return digits + 9
    return digits + 15


def decrypt_entry(flags: int, compressed_size: int, data: bytes) -> bytes:
    """Decrypt a GRF entry payload in-memory if its flags require it."""
    if flags & FLAG_MIXCRYPT:
        only_header = False
        cycle = _encryption_cycle(_count_digits(compressed_size))
    elif flags & FLAG_HEADER_DES:
        only_header = True
        cycle = 0
    else:
        return data

    buffer = bytearray(data)
    full = (len(buffer) // _BLOCK) * _BLOCK
    remainder = len(buffer) - full
    _decrypt_blocks(buffer, only_header, cycle, limit=full)
    if remainder:
        # Mirror korangar: the trailing partial block is zero-padded and
        # decrypted as a standalone block 0 (so it always gets DES applied).
        tail = bytearray(buffer[full:]) + bytearray(_BLOCK - remainder)
        _decrypt_blocks(tail, only_header, cycle)
        buffer[full:] = tail[:remainder]
    return bytes(buffer)


def _decrypt_blocks(buffer: bytearray, only_header: bool, cycle: int, limit: int | None = None) -> None:
    end = len(buffer) if limit is None else limit
    non_des = 0
    unpack = struct.Struct(">Q")
    for block_number in range(end // _BLOCK):
        start = block_number * _BLOCK
        des_block = block_number < _HEADER_BLOCKS or (not only_header and cycle and block_number % cycle == 0)
        if des_block:
            (value,) = unpack.unpack_from(buffer, start)
            unpack.pack_into(buffer, start, decode_des_block(value))
        elif not only_header:
            if non_des == 7:
                _scramble_block(buffer, start)
                non_des = 0
            non_des += 1


def _nibble_swap(block: bytearray, start: int) -> None:
    for i in range(start, start + 8):
        block[i] = ((block[i] >> 4) | (block[i] << 4)) & 0xFF


def decode_file_name(name: bytes) -> bytes:
    """Decode an obfuscated GRF v0x10x file name (nibble swap + DES per block)."""
    buffer = bytearray(name)
    unpack = struct.Struct(">Q")
    for start in range(0, len(buffer) - len(buffer) % 8, 8):
        _nibble_swap(buffer, start)
        (value,) = unpack.unpack_from(buffer, start)
        unpack.pack_into(buffer, start, decode_des_block(value))
    end = buffer.find(b"\0")
    return bytes(buffer[:end if end >= 0 else len(buffer)])


# GRF v0x10x: these extensions only have their first 20 blocks DES-encrypted;
# everything else uses the full mixcrypt scheme.
_V1_HEADER_ONLY_EXTENSIONS_STR = (".gnd", ".gat", ".act", ".str")


@dataclass(frozen=True, slots=True)
class GrfEntry:
    path: str  # decoded, lowercased, backslash-separated
    compressed_size: int
    compressed_size_aligned: int
    uncompressed_size: int
    flags: int
    offset: int  # relative to end of header
    v1: bool = False  # GRF 0x10x entry (always encrypted, scheme by extension)


class GrfArchive:
    """Read-only view of a GRF archive, indexed by lowercase path."""

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        self._file = open(self.path, "rb")
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self._lock = threading.Lock()
        self.entries: dict[str, GrfEntry] = {}
        self._parse_header_and_table()

    def close(self) -> None:
        self._mmap.close()
        self._file.close()

    def __enter__(self) -> "GrfArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _parse_header_and_table(self) -> None:
        header = self._mmap[:HEADER_SIZE]
        if header[:16] != HEADER_MAGIC and header[:14] != HEADER_MAGIC_V3:
            raise ValueError(f"{self.path}: not a GRF archive")
        version = struct.unpack_from("<I", header, 42)[0]
        self.version = version
        major = (version >> 8) & 0xFF

        if major == 3 and header[35] == header[36] == header[37] == 0:
            # 64-bit layout: int64 table offset at 30, raw file count at 38.
            table_offset = struct.unpack_from("<q", header, 30)[0]
            self._parse_table(table_offset, entry_size_64=True, skip_reserved=True)
        elif major in (2, 3):
            table_offset = struct.unpack_from("<I", header, 30)[0]
            self._parse_table(table_offset, entry_size_64=False, skip_reserved=False)
        elif major == 1:
            table_offset, seed, file_count = struct.unpack_from("<IiI", header, 30)
            self._parse_table_v1(table_offset, file_count - seed - 7)
        else:
            raise NotImplementedError(f"{self.path}: GRF version {version:#x} is not supported")

    def _parse_table_v1(self, table_offset: int, file_count: int) -> None:
        table = self._mmap[HEADER_SIZE + table_offset:]
        entries = self.entries
        unpack = struct.Struct("<iiiBI").unpack_from
        offset = 0
        for _ in range(file_count):
            (block_length,) = struct.unpack_from("<I", table, offset)
            name_length = table[offset] - 6
            name_bytes = decode_file_name(table[offset + 6: offset + 6 + name_length])
            trailer = offset + block_length + 4
            v0, comp_aligned_raw, uncomp, flags, position = unpack(table, trailer)
            offset = trailer + 17
            if not flags & FLAG_FILE:
                continue
            comp = v0 - uncomp - 715
            comp_aligned = comp_aligned_raw - 37579
            path = self._decode_name(name_bytes)
            entries[path] = GrfEntry(path, comp, comp_aligned, uncomp, flags, position, v1=True)

    def _parse_table(self, table_offset: int, entry_size_64: bool, skip_reserved: bool) -> None:
        base = HEADER_SIZE + table_offset
        if skip_reserved:
            base += 4  # v0x300 has 4 reserved bytes (always 0) before the sizes
        compressed_size, uncompressed_size = struct.unpack_from("<II", self._mmap, base)
        raw = self._mmap[base + 8: base + 8 + compressed_size]
        table = zlib.decompress(raw)
        if len(table) != uncompressed_size:
            raise ValueError(f"{self.path}: file table size mismatch")

        entries = self.entries
        find = table.find
        if entry_size_64:
            unpack = struct.Struct("<IIIBq").unpack_from
            trailer = 21
        else:
            unpack = struct.Struct("<IIIBI").unpack_from
            trailer = 17

        pos = 0
        length = len(table)
        while pos < length:
            end = find(b"\0", pos)
            if end < 0:
                break
            name_bytes = table[pos:end]
            pos = end + 1
            comp, comp_aligned, uncomp, flags, offset = unpack(table, pos)
            pos += trailer
            if not flags & FLAG_FILE:
                continue  # directory entry
            path = self._decode_name(name_bytes)
            entries[path] = GrfEntry(path, comp, comp_aligned, uncomp, flags, offset)

    @staticmethod
    def _decode_name(name_bytes: bytes) -> str:
        try:
            name = name_bytes.decode("cp949")
        except UnicodeDecodeError:
            name = name_bytes.decode("cp949", errors="replace")
        return name.lower().replace("/", "\\")

    def __contains__(self, path: str) -> bool:
        return normalize_path(path) in self.entries

    def namelist(self) -> list[str]:
        return list(self.entries)

    def read(self, path: str) -> bytes:
        """Return the decompressed content of an archived file."""
        entry = self.entries.get(normalize_path(path))
        if entry is None:
            raise FileNotFoundError(f"{path} not in {self.path}")
        start = HEADER_SIZE + entry.offset
        data = self._mmap[start: start + entry.compressed_size_aligned]
        if entry.v1:
            # Version 0x10x archives encrypt every file; the scheme depends
            # on the file extension.
            if entry.path.endswith(_V1_HEADER_ONLY_EXTENSIONS_STR):
                data = decrypt_entry(FLAG_HEADER_DES, entry.compressed_size, data)
            else:
                data = decrypt_entry(FLAG_MIXCRYPT, entry.compressed_size, data)
        else:
            data = decrypt_entry(entry.flags, entry.compressed_size, data)
        if entry.compressed_size == entry.uncompressed_size:
            # Stored uncompressed (rare but legal).
            try:
                return zlib.decompress(data[:entry.compressed_size])
            except zlib.error:
                return data[:entry.uncompressed_size]
        return zlib.decompress(data[:entry.compressed_size])


def normalize_path(path: str) -> str:
    return path.lower().replace("/", "\\")


class GrfStack:
    """Several archives layered like the client does (later = higher priority)."""

    def __init__(self, paths: list[str | os.PathLike]):
        self.archives = [GrfArchive(p) for p in paths]

    def close(self) -> None:
        for archive in self.archives:
            archive.close()

    def __enter__(self) -> "GrfStack":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def read(self, path: str) -> bytes:
        key = normalize_path(path)
        for archive in reversed(self.archives):
            entry = archive.entries.get(key)
            if entry is not None:
                return archive.read(key)
        raise FileNotFoundError(path)

    def __contains__(self, path: str) -> bool:
        key = normalize_path(path)
        return any(key in archive.entries for archive in self.archives)

    def namelist(self) -> list[str]:
        names: set[str] = set()
        for archive in self.archives:
            names.update(archive.entries)
        return sorted(names)
