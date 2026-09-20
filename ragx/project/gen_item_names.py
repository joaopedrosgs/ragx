#!/usr/bin/env python3
"""gen_item_names.py — emit the item id -> display name table.

The client had almost no item names (icons/items.json holds only the handful of
items whose icons were exported), so inventory tooltips, shop rows and card names
came up blank. rAthena has every name in its item_db; this pulls them into
data/item_names.json so the UI can label an item by id.

item_db.yml is only a master that imports three files — usable, equip, etc — so
those are what carry the actual `Id`/`Name` pairs. Read line-wise rather than with
a YAML parser: the files are ~30k items and the two fields we need sit at a fixed
indentation right after each record header.

Usage:
    python tools/gen_item_names.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/item_names.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# The three real data files (item_db.yml just Imports these).
DB_FILES = [
    "db/re/item_db_usable.yml",
    "db/re/item_db_equip.yml",
    "db/re/item_db_etc.yml",
]


def parse_names(path: Path) -> dict[int, str]:
    """id -> display Name for one item_db file.

    A record starts with `  - Id:`; its `AegisName` then `Name` follow at
    four-space indent. Take the first `Name:` after each Id so a later `Name:` in
    some nested block (a job or trade sub-map) can never be mistaken for it.
    """
    out: dict[int, str] = {}
    item_id: int | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("- Id:"):
            # ids sometimes carry an inline comment ("Id: 102396 # note: ...")
            item_id = int(stripped.split(":", 1)[1].split("#", 1)[0].strip())
        elif item_id is not None and line.startswith("    Name:"):
            name = line.split(":", 1)[1].strip()
            # names can be quoted; drop a surrounding pair if present
            if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
                name = name[1:-1]
            out[item_id] = name
            item_id = None   # only the first Name per record
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/item_names.json")
    args = ap.parse_args()

    root = Path(args.rathena)
    names: dict[int, str] = {}
    for rel in DB_FILES:
        fp = root / rel
        if not fp.exists():
            print("  missing:", rel)
            continue
        part = parse_names(fp)
        names.update(part)
        print("  %-28s %d names" % (rel.rsplit("/", 1)[-1], len(part)))

    # anchors so a broken parse fails loudly rather than shipping empty
    for iid, exp in [(1101, "Sword"), (501, "Red Potion"), (4001, "Poring Card")]:
        got = names.get(iid)
        print("  %-8s %d -> %r (expected %r)"
              % ("ok" if got == exp else "MISMATCH", iid, got, exp))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({str(k): v for k, v in sorted(names.items())}, indent=0),
        encoding="utf-8")
    print("wrote %d item names -> %s" % (len(names), out))


if __name__ == "__main__":
    main()
