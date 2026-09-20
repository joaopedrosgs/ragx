#!/usr/bin/env python3
"""gen_skill_units.py — emit the skill-unit id -> skill table.

ZC_SKILL_ENTRY5 (0x09CA) identifies a persistent ground unit (a Firewall tile, a
trap, Sanctuary, the Warp Portal...) by rAthena's `e_skill_unit_id`, NOT by the
skill that created it. The client needs the skill to pick a visual, so this walks
the two controlled sources and joins them:

  src/map/skill.hpp   enum e_skill_unit_id   UNT_FIREWALL -> 0x7f
  db/re/skill_db.yml  Unit: Id: Firewall     -> skill MG_FIREWALL (id 18)

The YAML spells the unit in rAthena's short form ("Firewall"), which its own
parser upper-cases behind a "UNT_" prefix; we normalise the same way.

The enum is a plain C enum: values run on from the last explicit assignment, and
there are a few explicit jumps (UNT_STAR_BURST, UNT_DEEPBLINDTRAP, the UNT_GD_*
guild auras), so a running counter is the only correct way to read it.

Usage:
    python tools/gen_skill_units.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/skill_units.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ENUM_RE = re.compile(r"enum\s+e_skill_unit_id\s*:\s*\w+\s*\{(.*?)\n\};", re.S)
# One enumerator: NAME, optionally "= <value>", up to the comma/comment.
MEMBER_RE = re.compile(r"^\s*(UNT_[A-Z0-9_]+)\s*(?:=\s*([0-9a-fA-Fx]+))?\s*,?", re.M)

# These wire ids intentionally describe an invisible/internal cell, not the
# skill that created it. Dummyskill is shared by Storm Gust, Thunder Storm and
# many unrelated skills; choosing the first database row made Storm Gust cells
# render Thunder Storm lightning.
NO_VISUAL_UNITS = {
    "UNT_DUMMYSKILL",
}


def parse_unit_enum(skill_hpp: Path) -> dict[str, int]:
    """UNT_* -> numeric value, honouring explicit assignments and run-on gaps."""
    text = skill_hpp.read_text(encoding="utf-8", errors="replace")
    body = ENUM_RE.search(text)
    if body is None:
        raise SystemExit("gen_skill_units: e_skill_unit_id enum not found")
    out: dict[str, int] = {}
    nxt = 0
    for line in body.group(1).splitlines():
        # strip trailing // comments so "UNT_TRAP, //TODO" still matches
        code = line.split("//", 1)[0]
        m = MEMBER_RE.match(code)
        if m is None:
            continue
        name, raw = m.group(1), m.group(2)
        if raw is not None:
            nxt = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        out[name] = nxt
        nxt += 1
    return out


def parse_skill_units(skill_db: Path) -> list[tuple[int, str, str]]:
    """(skill id, skill name, UNT_ name) for every skill that plants a unit.

    Read line-wise rather than with a YAML parser: skill_db.yml is ~40k lines and
    the two fields we need are unambiguous at fixed indentation.
    """
    rows: list[tuple[int, str, str]] = []
    skill_id, skill_name, in_unit = 0, "", False
    for line in skill_db.read_text(encoding="utf-8", errors="replace").splitlines():
        # rAthena annotates rows inline ("Id: 491 # Removed on kRO"); none of the
        # fields we read can legitimately contain a '#'.
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("- Id:"):
            skill_id = int(stripped.split(":", 1)[1].strip())
            skill_name, in_unit = "", False
        elif stripped.startswith("Name:") and not skill_name:
            skill_name = stripped.split(":", 1)[1].strip()
        elif stripped == "Unit:":
            in_unit = True
        elif in_unit and stripped.startswith("Id:"):
            unit = stripped.split(":", 1)[1].strip()
            rows.append((skill_id, skill_name, "UNT_" + unit.upper()))
            in_unit = False
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/skill_units.json")
    args = ap.parse_args()

    root = Path(args.rathena)
    enum = parse_unit_enum(root / "src" / "map" / "skill.hpp")
    rows = parse_skill_units(root / "db" / "re" / "skill_db.yml")

    table: dict[str, dict] = {}
    missing: list[str] = []
    for skill_id, skill_name, unt in rows:
        if unt in NO_VISUAL_UNITS:
            continue
        value = enum.get(unt)
        if value is None:
            missing.append("%s (%s)" % (unt, skill_name))
            continue
        # First skill wins: a couple of unit ids are shared (e.g. the generic
        # traps), and the earlier skill is the canonical owner of the visual.
        table.setdefault(str(value), {
            "unit": unt, "skill_id": skill_id, "skill": skill_name,
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(sorted(table.items(), key=lambda kv: int(kv[0]))),
                              indent=0), encoding="utf-8")
    print("gen_skill_units: %d unit ids from %d enum members / %d skill rows -> %s"
          % (len(table), len(enum), len(rows), out))
    if missing:
        print("  unmapped unit names (not in the enum): " + ", ".join(missing))


if __name__ == "__main__":
    main()
