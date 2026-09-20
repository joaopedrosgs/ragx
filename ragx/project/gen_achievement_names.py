#!/usr/bin/env python3
"""gen_achievement_names.py — emit the achievement id -> name/group table.

ZC_ALL_ACH_LIST and ZC_ACH_UPDATE identify achievements by id and nothing else,
and — unusually — **the client ships no name table for them**. It has the whole
achievement interface under `data/texture/유저인터페이스/achievement/` and not one
data file: 104 files in the GRF match "achiev" and every one is a bitmap. So the
official client is fed these names by its own localisation build, which we do not
have.

rAthena does have them. `db/re/achievement_db.yml` carries a `Name:` for every
entry, which makes the server the only source either side of the wire, the same
way it is for EFST names and clif messages.

Read straight rather than through a YAML parser, for the reason `gen_packets.py`
gives: this runs on a fresh checkout with nothing installed but the standard
library, and the two fields wanted here are flat scalars on known keys. A real
parser would buy nothing and cost a dependency.

Usage:
    python tools/gen_achievement_names.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/achievement_names.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ID_RE = re.compile(r"^\s*-\s*Id:\s*(\d+)\s*$")
NAME_RE = re.compile(r"^\s*Name:\s*(.+?)\s*$")
GROUP_RE = re.compile(r"^\s*Group:\s*(\w+)\s*$")


def parse(path: Path) -> dict[int, dict[str, str]]:
    """id -> {name, group}, in file order.

    An entry ends where the next `- Id:` begins, so a `Name:` is attributed to
    whichever id most recently opened. That is also why `Targets:` cannot confuse
    it: the nested `- Id:` under a target is indented deeper and the pattern
    anchors on the two-space list level the file uses for achievements.
    """
    out: dict[int, dict[str, str]] = {}
    current: int | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = ID_RE.match(line)
        if m is not None and line.startswith("  - Id:"):
            current = int(m.group(1))
            out.setdefault(current, {"name": "", "group": ""})
            continue
        if current is None:
            continue
        m = NAME_RE.match(line)
        if m is not None and not out[current]["name"]:
            # Names are plain scalars in this file; strip the quoting YAML allows.
            out[current]["name"] = m.group(1).strip('"').strip("'")
            continue
        m = GROUP_RE.match(line)
        if m is not None and not out[current]["group"]:
            out[current]["group"] = m.group(1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/achievement_names.json")
    args = ap.parse_args()

    src = Path(args.rathena) / "db" / "re" / "achievement_db.yml"
    if not src.exists():
        raise SystemExit("gen_achievement_names: %s not found" % src)
    rows = parse(src)
    named = {i: r for i, r in rows.items() if r["name"]}
    if not named:
        raise SystemExit("gen_achievement_names: parsed no names — has the file shape changed?")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {str(i): named[i] for i in sorted(named)},
        ensure_ascii=False, indent=0), encoding="utf-8")
    print("wrote %d achievement names (%d ids parsed) -> %s"
          % (len(named), len(rows), out))
    if len(named) != len(rows):
        print("  %d entries carry no Name and were dropped" % (len(rows) - len(named)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
