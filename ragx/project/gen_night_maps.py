#!/usr/bin/env python3
"""gen_night_maps.py — which maps have a sky over them.

A day/night cycle must not darken an interior. RO's own answer to "is this map
outdoors" is the `nightenabled` mapflag, and rAthena ships the list: 277 maps in
`npc/mapflag/night.txt` plus 25 renewal-only ones in `npc/re/mapflag/night.txt`.

That is the authentic source and it is worth using rather than a heuristic on the
name. `prt_in` and `iz_int01` are interiors that no name rule catches reliably,
and guessing wrong means either a lit cave or a town whose night never falls.

The server only *sends* night (EFST_SKE) when `night_duration` is configured,
which it is not by default — so this list is what lets the client run the cycle
itself on exactly the maps the server would have.

Usage:
    python tools/gen_night_maps.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/night_maps.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# `alberta<tab>mapflag<tab>nightenabled`, with `//` comments to skip.
ROW_RE = re.compile(r"^([A-Za-z0-9_@]+)\s+mapflag\s+nightenabled\s*$")


def parse(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("//", 1)[0].strip()
        m = ROW_RE.match(line)
        if m is not None:
            out.add(m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/night_maps.json")
    args = ap.parse_args()

    root = Path(args.rathena)
    # Renewal is layered ON TOP of the base list, not instead of it — the same way
    # rAthena loads them — so a map named in either has a sky.
    maps = parse(root / "npc" / "mapflag" / "night.txt")
    maps |= parse(root / "npc" / "re" / "mapflag" / "night.txt")
    if not maps:
        raise SystemExit("gen_night_maps: parsed nothing — has the file shape changed?")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sorted(maps), indent=0), encoding="utf-8")
    print("wrote %d night-enabled maps -> %s" % (len(maps), out))
    for probe in ("prontera", "geffen", "prt_in"):
        print("  %-10s %s" % (probe, "outdoors" if probe in maps else "interior"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
