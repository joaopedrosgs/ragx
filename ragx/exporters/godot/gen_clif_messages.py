#!/usr/bin/env python3
"""gen_clif_messages.py — emit the ZC_MSG id -> English text table.

ZC_MSG (0x0291) is how the server explains a great many refusals: "you can't put
this item on", "you cannot carry more items because you are overweight", "[Bow]
must be equipped". It carries nothing but a **number**, which indexes the official
client's msgstringtable.txt — a file we do not extract, so without a table the
packet is unreadable and the refusal it explains looks like the action silently
doing nothing.

rAthena solves this for us: every value of `e_clif_messages` (src/map/clif.hpp)
carries the message's English text as the comment directly above it. That comment
IS the string table entry, maintained alongside the id, so this pulls both into
data/clif_messages.json rather than depending on a client file.

Values are explicit here (unlike the efst_type enum, which needs a running
counter), and a few members sit inside `#if PACKETVER` guards. Guarded ids are
kept: we only ever look up an id the server actually sent, so an entry for a
message this PACKETVER never sends costs one dictionary row and nothing else.

Usage:
    python tools/gen_clif_messages.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/clif_messages.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ENUM_RE = re.compile(r"enum\s+e_clif_messages\s*:\s*\w+\s*\{(.*?)\n\};", re.S)
MEMBER_RE = re.compile(r"^\s*(MSI_[A-Z0-9_]+)\s*=\s*(\d+)\s*,?")


def parse_enum(clif_hpp: Path) -> dict[int, dict[str, str]]:
    text = clif_hpp.read_text(encoding="utf-8", errors="replace")
    body = ENUM_RE.search(text)
    if body is None:
        raise SystemExit("gen_clif_messages: e_clif_messages enum not found")

    out: dict[int, dict[str, str]] = {}
    pending: list[str] = []          # comment lines seen since the last member
    for line in body.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            pending.append(stripped[2:].strip())
            continue
        m = MEMBER_RE.match(line)
        if m is None:
            # A blank line keeps the comment (they are separated by one); anything
            # else -- a #if, a #endif -- is not part of a message's description.
            if stripped and not stripped.startswith("#"):
                pending.clear()
            continue
        msg_id = int(m.group(2))
        # First definition wins: a PACKETVER-guarded pair reuses one id and the
        # texts agree, so taking the later one would only churn the output.
        if msg_id not in out:
            out[msg_id] = {"name": m.group(1), "text": " ".join(pending).strip()}
        pending.clear()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/clif_messages.json"))
    args = ap.parse_args()

    table = parse_enum(args.rathena / "src" / "map" / "clif.hpp")
    if not table:
        raise SystemExit("gen_clif_messages: enum parsed but empty")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({str(k): v for k, v in sorted(table.items())},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    missing = sum(1 for v in table.values() if not v["text"])
    print("gen_clif_messages: %d messages -> %s (%d with no description)"
          % (len(table), args.out, missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
