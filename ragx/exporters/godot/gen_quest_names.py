#!/usr/bin/env python3
"""gen_quest_names.py — emit the quest id -> title table.

Quest packets carry identifiers and progress. rAthena's quest_db.yml supplies
fallback titles; the client GRF's questid2display.txt supplies narrative prose
and objective summaries, exported separately as data/quest_details.json.

Read straight rather than through a YAML parser, for the reason `gen_packets.py`
gives: this has to run on a fresh checkout with nothing installed but the
standard library, and the two fields wanted here are flat scalars on known keys
at a known indent.

Usage:
    python tools/gen_quest_names.py --rathena C:/Users/pedro/Documents/rathena \
        --out data/quest_names.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ID_RE = re.compile(r"^  - Id:\s*(\d+)\s*$")
TITLE_RE = re.compile(r"^\s{4}Title:\s*(.+?)\s*$")


def parse(path: Path) -> dict[int, str]:
    """id -> title, in file order.

    `- Id:` is anchored at the two-space list level the file uses for quests, and
    `Title:` at four. That is what keeps the nested `- Id:` under a quest's
    `Targets:` — which is a target index, not a quest — from opening a new entry
    and stealing the next title.
    """
    out: dict[int, str] = {}
    current: int | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = ID_RE.match(line)
        if m is not None:
            current = int(m.group(1))
            out.setdefault(current, "")
            continue
        if current is None:
            continue
        m = TITLE_RE.match(line)
        if m is not None and not out[current]:
            out[current] = m.group(1).strip('"').strip("'")
    return out


def extract_details(client: str) -> dict[str, dict[str, str]]:
    """Read native quest prose through ragx; server packets only carry progress."""
    from ragx.client import open_stack
    with open_stack(client) as archive:
        raw = archive.read(r"data\questid2display.txt")
    # This LATAM client uses Windows Western encoding; prefer UTF-8 when supplied.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return parse_details(text)


def parse_details(text: str) -> dict[str, dict[str, str]]:
    text = re.sub(r"(?m)^//[^\n]*", "", text)
    entries = re.finditer(r"(?m)^\s*(\d+)#([^#]*)#([^#]*)#([^#]*)#([^#]*)#([^#]*)#", text)
    return {m[1]: {"title": m[2].strip(), "description": m[5].strip(),
                    "summary": m[6].strip()} for m in entries}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default="C:/Users/pedro/Documents/rathena")
    ap.add_argument("--out", default="data/quest_names.json")
    ap.add_argument("--client", default="C:/Gravity/Ragnarok")
    ap.add_argument("--details-out", default="data/quest_details.json")
    args = ap.parse_args()

    src = Path(args.rathena) / "db" / "re" / "quest_db.yml"
    if not src.exists():
        raise SystemExit("gen_quest_names: %s not found" % src)
    rows = parse(src)
    titled = {i: t for i, t in rows.items() if t}
    if not titled:
        raise SystemExit("gen_quest_names: parsed no titles — has the file shape changed?")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({str(i): titled[i] for i in sorted(titled)},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    print("wrote %d quest titles (%d ids parsed) -> %s" % (len(titled), len(rows), out))
    if len(titled) != len(rows):
        print("  %d entries carry no Title and were dropped" % (len(rows) - len(titled)))
    details = extract_details(args.client)
    Path(args.details_out).write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
    print("wrote %d quest descriptions -> %s" % (len(details), args.details_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
