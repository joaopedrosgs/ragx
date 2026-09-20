#!/usr/bin/env python3
"""gen_efst_names.py — emit the EFST id -> readable status name table.

The status store tracks buffs/debuffs by their EFST id (from ZC_EFST_SET_ENTER /
ZC_MSG_STATE_CHANGE3), which is just a number. rAthena has no human-readable
status name in a data file — the `efst_type` enum constant is the only source —
so this pulls the enum and prettifies each constant (EFST_INC_AGI -> "Inc Agi")
into data/efst_names.json for the HUD and the `status` debug readout.

Plain C enum from EFST_BLANK = -1, running on, so a counter is the only correct
way to read it (same as the skill-unit enum).

Usage:
    python tools/gen_efst_names.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/efst_names.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ENUM_RE = re.compile(r"enum\s+efst_type\s*:\s*\w+\s*\{(.*?)\n\};", re.S)
MEMBER_RE = re.compile(r"^\s*(EFST_[A-Z0-9_]+)\s*(?:=\s*(-?\d+))?\s*,?")


def prettify(const: str) -> str:
    """EFST_INC_AGI -> 'Inc Agi'; keep it identifiable, not perfect."""
    body = const[len("EFST_"):] if const.startswith("EFST_") else const
    return " ".join(w.capitalize() for w in body.split("_"))


def parse_enum(status_hpp: Path) -> dict[int, str]:
    text = status_hpp.read_text(encoding="utf-8", errors="replace")
    body = ENUM_RE.search(text)
    if body is None:
        raise SystemExit("gen_efst_names: efst_type enum not found")
    out: dict[int, str] = {}
    nxt = 0
    for line in body.group(1).splitlines():
        code = line.split("//", 1)[0]
        m = MEMBER_RE.match(code)
        if m is None:
            continue
        name, raw = m.group(1), m.group(2)
        if raw is not None:
            nxt = int(raw)
        # skip the -1 sentinel and any *_MAX marker
        if nxt >= 0 and not name.endswith("_MAX"):
            out[nxt] = prettify(name)
        nxt += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/efst_names.json")
    args = ap.parse_args()

    names = parse_enum(Path(args.rathena) / "src" / "map" / "status.hpp")
    for eid, exp in [(0, "Provoke"), (1, "Endure"), (10, "Blessing"), (12, "Inc Agi")]:
        got = names.get(eid)
        print("  %-8s %d -> %r (expected %r)"
              % ("ok" if got == exp else "MISMATCH", eid, got, exp))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({str(k): v for k, v in sorted(names.items())}, indent=0),
        encoding="utf-8")
    print("wrote %d EFST names -> %s" % (len(names), out))


if __name__ == "__main__":
    main()
