"""Generate item-id -> weapon sprite type from the pinned rAthena database.

Modern LOOK_WEAPON uses item ids when View is absent (pc.cpp update_look).
Only presentation metadata is exported; equipment rules stay on the server.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def weapon_types(header: str) -> dict[str, int]:
    enum = header.split("enum weapon_type", 1)[1].split("MAX_WEAPON_TYPE", 1)[0]
    return {name: index for index, name in enumerate(re.findall(r"\bW_(\w+)\s*,", enum))}


def collect(text: str, records: dict[str, dict[str, str]]) -> None:
    current = None
    for line in text.splitlines():
        item = re.match(r"^  - Id:\s*(\d+)", line)
        if item:
            current = records.setdefault(item[1], {})
        elif current is not None:
            field = re.match(r"^    (Type|SubType|View):\s*(\w+)", line)
            if field:
                current[field[1]] = field[2]


def generate(root: Path) -> dict[str, int]:
    types = weapon_types((root / "src/map/pc.hpp").read_text(encoding="utf-8"))
    records: dict[str, dict[str, str]] = {}
    for relative in ["db/re/item_db_equip.yml", "db/import/item_db.yml"]:
        source = root / relative
        if source.exists():
            collect(source.read_text(encoding="utf-8"), records)
    out = {}
    for item, fields in records.items():
        if fields.get("Type") != "Weapon":
            continue
        subtype = fields.get("SubType", "Fist").upper()
        out[item] = int(fields.get("View", "0")) or types[subtype]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rathena", type=Path, default=Path("../rathena"))
    parser.add_argument("--out", type=Path, default=Path("data/weapon_item_views.json"))
    args = parser.parse_args()
    views = generate(args.rathena)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(views, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(f"weapon views: {len(views)} items -> {args.out}")


if __name__ == "__main__":
    main()
