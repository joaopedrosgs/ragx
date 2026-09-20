"""Load the client's compiled Lua 5.1 .lub files into a Python-hosted Lua.

The client lubs were compiled by a 32-bit Lua 5.1 (size_t = 4 bytes);
lupa's bundled 64-bit Lua 5.1 refuses that header. Lua 5.1 bytecode only
uses size_t for string length prefixes, so rebasing a chunk to the host
layout is a straightforward structural rewrite.
"""

from __future__ import annotations

import struct

LUA_SIGNATURE = b"\x1bLua"

_CONST_NIL = 0
_CONST_BOOL = 1
_CONST_NUMBER = 3
_CONST_STRING = 4


class _Rewriter:
    def __init__(self, data: bytes, src_size_t: int, dst_size_t: int):
        self.data = data
        self.pos = 0
        self.src_fmt = "<I" if src_size_t == 4 else "<Q"
        self.src_size = src_size_t
        self.dst_fmt = "<I" if dst_size_t == 4 else "<Q"
        self.out = bytearray()

    def copy(self, count: int) -> bytes:
        chunk = self.data[self.pos: self.pos + count]
        if len(chunk) != count:
            raise ValueError("truncated lua chunk")
        self.pos += count
        self.out += chunk
        return chunk

    def read_int(self) -> int:
        (value,) = struct.unpack_from("<i", self.data, self.pos)
        self.copy(4)
        return value

    def rewrite_string(self) -> None:
        (length,) = struct.unpack_from(self.src_fmt, self.data, self.pos)
        self.pos += self.src_size
        self.out += struct.pack(self.dst_fmt, length)
        self.copy(length)

    def rewrite_function(self) -> None:
        self.rewrite_string()  # source name
        self.copy(8)  # linedefined, lastlinedefined
        self.copy(4)  # nups, numparams, is_vararg, maxstacksize
        self.copy(4 * self.read_int())  # code
        for _ in range(self.read_int()):  # constants
            (tag,) = self.copy(1)
            if tag == _CONST_BOOL:
                self.copy(1)
            elif tag == _CONST_NUMBER:
                self.copy(8)
            elif tag == _CONST_STRING:
                self.rewrite_string()
            elif tag != _CONST_NIL:
                raise ValueError(f"unknown lua constant tag {tag}")
        for _ in range(self.read_int()):  # nested prototypes
            self.rewrite_function()
        self.copy(4 * self.read_int())  # debug: line info
        for _ in range(self.read_int()):  # debug: local variables
            self.rewrite_string()
            self.copy(8)
        for _ in range(self.read_int()):  # debug: upvalue names
            self.rewrite_string()


def rebase_chunk(data: bytes, dst_size_t: int = 8) -> bytes:
    """Rewrite a Lua 5.1 bytecode chunk for a host with a different size_t."""
    if data[:4] != LUA_SIGNATURE:
        return data  # plain-text lua, loadable as is
    version, fmt, endian, int_size, size_t_size, instr_size, num_size, integral = data[4:12]
    if version != 0x51 or fmt != 0 or endian != 1:
        raise ValueError(f"unsupported lua chunk header {data[4:12].hex()}")
    if (int_size, instr_size, num_size, integral) != (4, 4, 8, 0):
        raise ValueError(f"unsupported lua number layout {data[4:12].hex()}")
    if size_t_size == dst_size_t:
        return data
    rewriter = _Rewriter(data, size_t_size, dst_size_t)
    rewriter.copy(12)
    rewriter.out[8] = dst_size_t
    rewriter.rewrite_function()
    if rewriter.pos != len(data):
        raise ValueError(f"{len(data) - rewriter.pos} bytes after lua chunk")
    return bytes(rewriter.out)


class LuaEnv:
    """A Lua 5.1 state that loads .lub files straight from a GrfArchive."""

    def __init__(self, grf):
        import lupa.lua51 as lua51

        self.grf = grf
        # encoding=None: lua strings come back as bytes; the client tables
        # are CP949, decode at the call site with decode().
        self.runtime = lua51.LuaRuntime(encoding=None)
        self._loadstring = self.runtime.eval(b"loadstring")
        self._host_size_t = struct.calcsize("P")

    def load(self, grf_path: str, optional: bool = False,
             data_root: str = "data") -> bool:
        """Execute one Lua file from the GRF.

        ``grf_path`` is relative to ``Lua Files`` (for example
        ``datainfo/jobname``). Localised clients mirror that tree below roots
        such as ``data\\english``; ``data_root`` selects one of those mirrors
        without changing where the other files in this Lua state are loaded
        from.
        """
        full = (f"{data_root}\\luafiles514\\lua files\\"
                f"{grf_path.replace('/', chr(92))}.lub")
        try:
            data = self.grf.read(full)
        except (KeyError, FileNotFoundError):
            if optional:
                return False
            raise
        chunk = rebase_chunk(data, self._host_size_t)
        result = self._loadstring(chunk, b"@" + grf_path.encode())
        if isinstance(result, tuple):  # (nil, error message)
            raise ValueError(f"{grf_path}: {result[1]}")
        result()
        return True

    def call(self, name: str, *args):
        """Call a global lua function; returns None if it is not defined."""
        func = self.runtime.eval(name.encode())
        if func is None:
            return None
        return func(*args)

    def table(self, name: str):
        """Fetch a global table (or None)."""
        return self.runtime.eval(name.encode())


def decode(value, encoding: str = "cp949"):
    """Decode a lua byte string (client tables are CP949)."""
    if isinstance(value, bytes):
        return value.decode(encoding, errors="replace")
    return value
