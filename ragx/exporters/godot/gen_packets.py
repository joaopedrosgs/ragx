#!/usr/bin/env python3
"""gen_packets.py — emit the opcode -> byte-length table for our PACKETVER.

Parses rAthena's src/map/clif_packetdb.hpp with a small PACKETVER-aware C
preprocessor (the only conditionals in that file are PACKETVER comparisons plus
the header guard), resolving every `packet(id,len)` / `parseable_packet(id,len,
func,...)` line active at PACKETVER 20211103 (renewal). Writes the length table
the client's framer needs so it can skip packets it has no decoder for instead
of desyncing.

Length is the total bytes incl. the 2-byte opcode, or -1 for variable-length
packets (whose bytes 2..4 carry the total length). `sizeof(struct PACKET_*)`
lengths are resolved from the packed struct definitions in packets.hpp /
packets_struct.hpp / map/packets.hpp.

Usage:
    python tools/gen_packets.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/packet_lengths.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# The version this run resolves. `set_version` moves it; everything downstream
# reads the globals, so nothing else has to be threaded through.
PACKETVER = 20211103
FLAVOUR = "re"
VARIABLE = -1   # length sentinel for length-prefixed (variable) packets
# Renewal build: RE_NUM = PACKETVER, MAIN/ZERO = 0 (src/config/packets.hpp).
SYMBOLS = {
    "PACKETVER_MAIN_NUM": 0,
    "PACKETVER_RE_NUM": PACKETVER,
    "PACKETVER_ZERO_NUM": 0,
    "PACKETVER": PACKETVER,
}

## rAthena builds one of three flavours, and the #if chains test the flavour's
## OWN counter — `PACKETVER_MAIN_NUM >= 20190522 || PACKETVER_RE_NUM >= 20190508`.
## The other two are 0, so a renewal build never satisfies a MAIN-only branch
## even at a much later date. Getting this wrong silently selects a different
## packet, which is the whole class of bug this file exists to prevent.
FLAVOURS = ("re", "main", "zero")


def set_version(packetver: int, flavour: str = "re") -> None:
    """Point the whole module at one rAthena build."""
    global PACKETVER, FLAVOUR
    if flavour not in FLAVOURS:
        raise SystemExit("flavour must be one of %s" % (FLAVOURS,))
    PACKETVER = packetver
    FLAVOUR = flavour
    SYMBOLS["PACKETVER"] = packetver
    SYMBOLS["PACKETVER_MAIN_NUM"] = packetver if flavour == "main" else 0
    SYMBOLS["PACKETVER_RE_NUM"] = packetver if flavour == "re" else 0
    SYMBOLS["PACKETVER_ZERO_NUM"] = packetver if flavour == "zero" else 0
    DEFINED_MACROS.clear()
    DEFINED_MACROS.update(_BASE_MACROS)
    # `defined(PACKETVER_ZERO)` and friends are what the #elif chains fall back
    # on when there is no numeric comparison, so the flavour has to answer them
    # too — not just the *_NUM counters.
    DEFINED_MACROS.add({"re": "PACKETVER_RE", "main": "PACKETVER_MAIN",
                        "zero": "PACKETVER_ZERO"}[flavour])
    DEFINED_MACROS.add({"re": "PACKETVER_RE_NUM", "main": "PACKETVER_MAIN_NUM",
                        "zero": "PACKETVER_ZERO_NUM"}[flavour])
    if flavour == "re":
        DEFINED_MACROS.add("RENEWAL")


_SUBST = sorted(SYMBOLS, key=len, reverse=True)   # longest first

# Macros that are #defined in our build (renewal + default config), for #ifdef
# and defined(). Value-bearing ones (PACKETVER*_NUM) are also in SYMBOLS.
# Macros defined regardless of flavour. `set_version` adds the flavour's own.
_BASE_MACROS = {"HOTKEY_SAVING", "PACKETVER"}
DEFINED_MACROS = {"HOTKEY_SAVING", "PACKETVER", "PACKETVER_RE", "PACKETVER_RE_NUM", "RENEWAL"}


def eval_cond(expr: str) -> bool:
    """Evaluate a PACKETVER #if/#elif expression."""
    e = expr
    # defined(X) -> 1/0 first (before X gets substituted to a value).
    e = re.sub(r"\bdefined\s*\(\s*(\w+)\s*\)",
               lambda m: "1" if (m.group(1) in SYMBOLS or m.group(1) in DEFINED_MACROS) else "0", e)
    for name in _SUBST:
        e = re.sub(r"\b" + name + r"\b", str(SYMBOLS[name]), e)
    # Zero out any leftover identifier (stray macro) BEFORE inserting or/and,
    # so we don't clobber the boolean keywords. || && aren't identifiers.
    e = re.sub(r"\b[A-Za-z_]\w*\b", "0", e)
    e = e.replace("||", " or ").replace("&&", " and ")
    try:
        return bool(eval(e, {"__builtins__": {}}, {}))
    except Exception:
        return False


class Pre:
    """Line-level conditional tracker for #if/#elif/#else/#endif + guards."""

    def __init__(self) -> None:
        self.stack: list[dict] = []   # {active, taken}

    def active(self) -> bool:
        return all(f["active"] for f in self.stack)

    def handle(self, line: str) -> bool:
        m = re.match(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)", line)
        if not m:
            return False
        kind, rest = m.group(1), m.group(2).strip()
        if kind in ("if", "ifdef", "ifndef"):
            parent = all(f["active"] for f in self.stack)   # enclosing scope
            if kind == "if":
                val = eval_cond(rest)
            else:
                defined = rest in SYMBOLS or rest in DEFINED_MACROS
                val = defined if kind == "ifdef" else not defined
            self.stack.append({"active": parent and val, "taken": val})
        elif kind in ("elif", "else"):
            # parent = enclosing scope, EXCLUDING the current #if frame
            parent = all(f["active"] for f in self.stack[:-1])
            f = self.stack[-1]
            val = (not f["taken"]) and (eval_cond(rest) if kind == "elif" else True)
            f["active"] = parent and val
            f["taken"] = f["taken"] or val
        elif kind == "endif":
            if self.stack:
                self.stack.pop()
        return True


# ---- struct sizing for sizeof(PACKET_*) lengths ----------------------------

TYPE_SIZE = {
    "int8": 1, "uint8": 1, "char": 1, "bool": 1,
    "int16": 2, "uint16": 2, "short": 2,
    "int32": 4, "uint32": 4, "int": 4, "float": 4, "long": 4,
    "int64": 8, "uint64": 8, "double": 8,
}


## Array counts that rAthena itself #defines inside PACKETVER blocks, so they
## must be resolved per version rather than pinned. `MAX_HOTKEYS` is 27 before
## 20090603, 36 before 20090617 and 38 after; `MAX_HOTKEYS_PACKET` steps on its
## own, flavour-specific dates. Hardcoding them was correct for one pinned
## version and silently wrong for any other, which is exactly the failure this
## whole file is built to catch.
VERSIONED_CONSTS = ("MAX_HOTKEYS", "MAX_HOTKEYS_PACKET", "MAX_HOTKEYS_DB")

CONST_FILES = ["src/common/mmo.hpp", "src/map/packets_struct.hpp",
               "src/map/packets.hpp"]

DEFINE_INT = re.compile(r"^\s*#\s*define\s+(\w+)\s+(\d+)\s*$")


def resolve_consts(rathena: Path | None = None) -> dict[str, int]:
    """The constants struct sizing needs, with the PACKETVER-gated ones read
    from the backend at the version currently set."""
    consts = {"NAME_LENGTH": 24, "MAP_NAME_LENGTH": 12, "MAP_NAME_LENGTH_EXT": 16,
              "WEB_AUTH_TOKEN_LENGTH": 17, "MESSAGE_SIZE": 80, "CHAT_SIZE_MAX": 281,
              "MAX_CHAT_TILE": 1000, "PACKET_LEN": 0,
              "MAX_ITEM_OPTIONS": 5, "MAX_SLOTS": 4, "MAX_PACKET_POS": 3,
              # Fallbacks for the >= 20090617 era, replaced below when the
              # backend is readable. Kept so a missing checkout degrades to the
              # modern values rather than to a KeyError.
              "MAX_HOTKEYS": 38, "MAX_HOTKEYS_PACKET": 38, "MAX_HOTKEYS_DB": 76}
    if rathena is None:
        return consts
    for rel in CONST_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        pre = Pre()
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            if pre.handle(line):
                continue
            if not pre.active():
                continue
            m = DEFINE_INT.match(line)
            # First active definition wins: the #if chain is ordered so the
            # branch that matches this version comes first.
            if m and m.group(1) in VERSIONED_CONSTS and m.group(1) not in _seen(consts):
                consts[m.group(1)] = int(m.group(2))
                _seen(consts).add(m.group(1))
    consts.pop("__seen__", None)
    return consts


def _seen(consts: dict) -> set:
    return consts.setdefault("__seen__", set())


HEADER_FILES = ["src/common/packets.hpp", "src/map/packets.hpp", "src/map/packets_struct.hpp"]


FIELD_RE = re.compile(r"\s*(?:struct\s+)?(\w+)\s+\w+\s*(?:\[\s*([^\]]*?)\s*\])?\s*;")


def _collect_structs(rathena: Path) -> dict[str, list]:
    """{ struct_name: [(type_token, array_spec)] } for ALL structs (PACKET_* and
    sub-structs), PACKETVER-resolved. array_spec: None (scalar), '' (flexible),
    or the size token. The first active variant of each struct wins."""
    out: dict[str, list] = {}
    for rel in HEADER_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        pre = Pre()
        i = 0
        while i < len(text):
            if pre.handle(text[i]):
                i += 1
                continue
            m = re.match(r"\s*struct\s+(\w+)\s*\{", text[i])
            if m and pre.active():
                name = m.group(1)
                fields: list = []
                i += 1
                sp = Pre()
                while i < len(text) and not re.match(r"\s*\}", text[i]):
                    if sp.handle(text[i]):
                        i += 1
                        continue
                    if sp.active():
                        fm = FIELD_RE.match(text[i])
                        if fm:
                            fields.append((fm.group(1), fm.group(2)))
                    i += 1
                out.setdefault(name, fields)
            i += 1
    return out


DEFINE_RE = re.compile(
    r"DEFINE_PACKET_HEADER\(\s*(\w+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)")


def collect_packet_names(rathena: Path) -> dict[int, str]:
    """opcode -> rAthena's own packet name, for the branches active at our
    PACKETVER.

    These must be preprocessed rather than grepped: the same name maps to
    different opcodes across client versions (ZC_EFST_SET_ENTER is 0x984 for us
    and 0x8ff on older clients), so a plain scan would pick whichever definition
    happened to come first in the file.

    Only the length table is needed to keep the framer in step; this is what makes
    a coverage report legible — "ZC_COUPLESTATUS x30" instead of a bare opcode.
    """
    out: dict[int, str] = {}
    for rel in HEADER_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        pre = Pre()
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            if pre.handle(line):
                continue
            if not pre.active():
                continue
            m = DEFINE_RE.search(line)
            if m:
                raw = m.group(2)
                op = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
                out.setdefault(op, m.group(1))   # first active definition wins
    return out


DOC_FILES = ["src/map/clif.cpp", "src/char/char_clif.cpp", "src/login/loginclif.cpp"]
# e.g. `/// 0141 <status id>.L <base status>.L <plus status>.L (ZC_COUPLESTATUS)`
DOC_NAME_RE = re.compile(
    r"^\s*///\s*([0-9a-fA-F]{4})\b.*\(([A-Z][A-Z0-9_]+)\)\s*$")


def collect_doc_names(rathena: Path) -> dict[int, str]:
    """Fallback names for the classic packets, read from the `///` doc comments
    that head each sender in clif.cpp.

    Those packets predate DEFINE_PACKET_HEADER and are registered with literal
    opcodes, so the comment is the only place rAthena spells their name at all —
    it is where ZC_COUPLESTATUS (0x0141), the single most frequent unconsumed
    packet in a live capture, comes from.

    Comments carry no PACKETVER guards, so this is strictly a fallback: a real
    definition always wins, and where several client generations share an opcode
    the first spelling in the file is kept.
    """
    out: dict[int, str] = {}
    for rel in DOC_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            m = DOC_NAME_RE.match(line)
            if m:
                out.setdefault(int(m.group(1), 16), m.group(2))
    return out


def struct_sizes(rathena: Path) -> dict[str, int]:
    """Recursive packed sizeof, PACKETVER-resolved. -1 if a flexible array
    appears anywhere; a struct with an unresolvable field type is omitted."""
    consts = resolve_consts(rathena)
    fields_by = _collect_structs(rathena)
    memo: dict[str, int] = {}

    def size_of(name: str, stack: frozenset) -> int:
        if name in memo:
            return memo[name]
        if name in stack:
            return None
        stack = stack | {name}
        total = 0
        for typ, arr in fields_by.get(name, []):
            if typ in TYPE_SIZE:
                esz = TYPE_SIZE[typ]
            elif typ in fields_by:
                esz = size_of(typ, stack)
            else:
                esz = None
            if arr == "":
                memo[name] = VARIABLE
                return VARIABLE
            count = 1
            if arr is not None:
                count = consts.get(arr)
                if count is None:
                    try:
                        count = int(arr)
                    except ValueError:
                        count = None
            if esz is None or esz == VARIABLE or count is None:
                memo[name] = None
                return None
            total += esz * count
        memo[name] = total
        return total

    result: dict[str, int] = {}
    for name in fields_by:
        s = size_of(name, frozenset())
        if s is not None:
            result[name] = s
    return result


DEFHDR = re.compile(r"DEFINE_PACKET_(?:HEADER|ID)2?\(\s*(\w+)\s*,\s*(0[xX][0-9a-fA-F]+)\s*\)")


#: The second member of a length-prefixed packet: `int16 packetLength;` and its
#: spellings (`PacketLength`, `packetSize`, `len`).
LENGTH_FIELD_RE = re.compile(
    r"^\s*u?int16\s+(?:packet_?)?(?:length|len|size)\s*;", re.IGNORECASE)


def length_prefixed_structs(rathena: Path) -> set[str]:
    """Packet structs whose second member is a 16-bit length word.

    Only these can be variable-length on the wire: the framer reads bytes 2..4
    as the length, so a struct without that word has nowhere to put one. This
    is what decides whether a clif_packetdb `-1` may overrule a struct size --
    see build(). The first active variant of each struct wins, as in
    _collect_structs.
    """
    found: set[str] = set()
    seen: set[str] = set()
    for rel in HEADER_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        pre = Pre()
        i = 0
        while i < len(text):
            if pre.handle(text[i]):
                i += 1
                continue
            m = re.match(r"\s*struct\s+(PACKET_\w+)\s*\{", text[i])
            if m and pre.active() and m.group(1) not in seen:
                name = m.group(1)
                seen.add(name)
                members: list[str] = []
                i += 1
                sp = Pre()
                while i < len(text) and not re.match(r"\s*\}", text[i]):
                    if not sp.handle(text[i]) and sp.active() and text[i].strip().endswith(";"):
                        members.append(text[i])
                    i += 1
                if len(members) >= 2 and LENGTH_FIELD_RE.match(members[1]):
                    found.add(name)
            i += 1
    return found


def define_header_structs(rathena: Path) -> dict[int, str]:
    """opcode -> the PACKET_* struct its active DEFINE_PACKET_HEADER names."""
    out: dict[int, str] = {}
    for rel in HEADER_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        pre = Pre()
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            if pre.handle(line):
                continue
            if not pre.active():
                continue
            m = DEFHDR.search(line)
            if m:
                out.setdefault(int(m.group(2), 0), "PACKET_" + m.group(1))
    return out


def define_header_packets(rathena: Path, sizes: dict[str, int]) -> dict[int, int]:
    """opcode -> length for the newer typed packets declared with
    DEFINE_PACKET_HEADER(NAME, 0xID), active at our PACKETVER."""
    table: dict[int, int] = {}
    for rel in HEADER_FILES:
        fp = rathena / rel
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        pre = Pre()
        for line in text:
            if pre.handle(line):
                continue
            if not pre.active():
                continue
            m = DEFHDR.search(line)
            if m:
                name, opcode = m.group(1), int(m.group(2), 0)
                size = sizes.get("PACKET_" + name)
                if size is not None:
                    table[opcode] = size
    return table


# ---- main table extraction -------------------------------------------------

PKT = re.compile(r"\b(?:parseable_)?packet\(\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*([^,)]+)")


def build(rathena: Path) -> dict[int, int]:
    sizes = struct_sizes(rathena)
    # 1) the hardcoded clif_packetdb length table (some entries are stale for
    #    packets that later gained a versioned struct).
    clif: dict[int, int] = {}
    pdb = (rathena / "src/map/clif_packetdb.hpp").read_text(encoding="utf-8", errors="replace").splitlines()
    pre = Pre()
    unresolved: list[str] = []
    for line in pdb:
        if pre.handle(line):
            continue
        if not pre.active():
            continue
        m = PKT.search(line)
        if not m:
            continue
        opcode = int(m.group(1), 0)
        lentok = m.group(2).strip()
        length = None
        if re.fullmatch(r"-?\d+", lentok):
            length = int(lentok)
        else:
            sm = re.search(r"sizeof\(\s*struct\s+(PACKET_\w+)\s*\)", line)   # full line: sizeof has its own ')'
            if sm:
                length = sizes.get(sm.group(1))
        if length is None:
            unresolved.append("0x%04X %s" % (opcode, lentok))
            continue
        clif[opcode] = length

    # 2) the PACKETVER-resolved DEFINE_PACKET_* structs are the modern source of
    #    truth; let them override the hardcoded table -- except that a clif -1
    #    survives when the struct HAS a length word. Those are packets whose
    #    struct is fixed but whose sender grows them by hand (ZC_GUILD_POSITION
    #    appends a name only when there is one), so the struct size would be
    #    wrong and the -1 is right.
    #
    #    It used to survive unconditionally, and that was a desync: clif_packetdb
    #    still carries 2008 placeholders (`packet(0x02f3..0x02fd, -1)`), and
    #    0x02F7 is ZC_UPDATE_GDID on main >= 20220216 -- a fixed 47-byte struct
    #    with no length word. Framed as variable, its guildId was read as a
    #    length and the session dropped the moment the character joined a guild.
    table = dict(clif)
    lengthed = length_prefixed_structs(rathena)
    structs = define_header_structs(rathena)
    for opcode, size in define_header_packets(rathena, sizes).items():
        if clif.get(opcode) == -1 and size != -1 and structs.get(opcode) in lengthed:
            continue
        table[opcode] = size
    if unresolved:
        print("  (%d unresolved: %s)" % (len(unresolved), ", ".join(unresolved[:6])))
    return table


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/packet_lengths.json")
    ap.add_argument("--names-out", default="data/packet_names.json")
    ap.add_argument("--packetver", type=int, default=PACKETVER,
                    help="the rAthena PACKETVER to resolve (default: the pin)")
    ap.add_argument("--flavour", default=FLAVOUR, choices=FLAVOURS,
                    help="which counter the #if chains see: re, main or zero")
    args = ap.parse_args()
    set_version(args.packetver, args.flavour)
    print("resolving PACKETVER %d (%s)" % (PACKETVER, FLAVOUR))
    table = build(Path(args.rathena))
    # sanity anchors
    for op, exp in [(0x0064, 55), (0x02EB, 13), (0x0283, 6), (0x0888, 19), (0x008E, -1)]:
        got = table.get(op)
        flag = "ok" if got == exp else "MISMATCH"
        print("  %-8s 0x%04X -> %s (expected %d)" % (flag, op, got, exp))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({str(k): v for k, v in sorted(table.items())}, indent=0), encoding="utf-8")
    print("wrote %d packet lengths -> %s" % (len(table), out))

    names = collect_packet_names(Path(args.rathena))
    defined = len(names)
    for op, nm in collect_doc_names(Path(args.rathena)).items():
        names.setdefault(op, nm)   # definitions win; comments only fill the gaps
    print("  %d named by DEFINE_PACKET_HEADER, %d more from doc comments"
          % (defined, len(names) - defined))
    for op, exp in [(0x0088, "ZC_STOPMOVE"), (0x00C0, "ZC_EMOTION"),
                    (0x0141, "ZC_COUPLESTATUS")]:
        got = names.get(op)
        print("  %-8s 0x%04X -> %s (expected %s)"
              % ("ok" if got == exp else "MISMATCH", op, got, exp))
    names_out = Path(args.names_out)
    names_out.write_text(
        json.dumps({str(k): v for k, v in sorted(names.items())}, indent=0),
        encoding="utf-8")
    print("wrote %d packet names -> %s" % (len(names), names_out))


if __name__ == "__main__":
    main()
