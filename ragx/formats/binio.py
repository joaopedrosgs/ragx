"""Little-endian binary reader used by all format parsers."""

from __future__ import annotations

import struct


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def skip(self, count: int) -> None:
        self.pos += count

    def bytes(self, count: int) -> bytes:
        value = self.data[self.pos: self.pos + count]
        if len(value) != count:
            raise EOFError(f"wanted {count} bytes at {self.pos}, file has {len(self.data)}")
        self.pos += count
        return value

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def i16(self) -> int:
        (value,) = struct.unpack_from("<h", self.data, self.pos)
        self.pos += 2
        return value

    def u16(self) -> int:
        (value,) = struct.unpack_from("<H", self.data, self.pos)
        self.pos += 2
        return value

    def i32(self) -> int:
        (value,) = struct.unpack_from("<i", self.data, self.pos)
        self.pos += 4
        return value

    def u32(self) -> int:
        (value,) = struct.unpack_from("<I", self.data, self.pos)
        self.pos += 4
        return value

    def f32(self) -> float:
        (value,) = struct.unpack_from("<f", self.data, self.pos)
        self.pos += 4
        return value

    def vec2(self) -> tuple[float, float]:
        value = struct.unpack_from("<2f", self.data, self.pos)
        self.pos += 8
        return value

    def vec3(self) -> tuple[float, float, float]:
        value = struct.unpack_from("<3f", self.data, self.pos)
        self.pos += 12
        return value

    def vec4(self) -> tuple[float, float, float, float]:
        value = struct.unpack_from("<4f", self.data, self.pos)
        self.pos += 16
        return value

    def f32s(self, count: int) -> tuple[float, ...]:
        value = struct.unpack_from(f"<{count}f", self.data, self.pos)
        self.pos += 4 * count
        return value

    def fixed_string(self, length: int, encoding: str = "cp949") -> str:
        """Read a fixed-size, null-terminated string (CP949 by default)."""
        raw = self.bytes(length)
        end = raw.find(b"\0")
        if end >= 0:
            raw = raw[:end]
        return raw.decode(encoding, errors="replace")
