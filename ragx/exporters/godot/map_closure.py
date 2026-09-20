#!/usr/bin/env python3
"""map_closure.py — which maps does a character actually need?

A map only works in the client if it has been exported (`maps/<name>.tscn`,
`terrains/`, and above all `nav/<name>_gat.bin`). Without the GAT there is no
walk grid at all: `MapGrid.find_path` returns empty and the character cannot
take a single step. That is not hypothetical — the dev accounts save at
`izlude_in`, which was never exported, so they logged in unable to move.

Exporting all ~1265 maps is not the answer (slow, huge, and mostly content a
new character will never see). What is wanted is the **dependency closure**:
start from a few seed maps and follow warps outward, so you export the set that
is actually reachable, and know that anywhere a player can walk to, works.

The graph comes from rAthena's own warp scripts, which are the same source the
server loads, so the closure matches where the server will actually send you:

    <map>,<x>,<y>,<facing>\twarp\t<name>\t<xs>,<ys>,<tomap>,<tox>,<toy>

`warp2` is the same shape (it differs only in triggering while hidden). Lines
commented out with `//` are skipped — the server does not load them either.

Usage:
    python tools/map_closure.py --rathena <checkout> --seeds prontera --depth 1
    python tools/map_closure.py --rathena <checkout> --have nav --depth 1
    python tools/map_closure.py --rathena <checkout> --seeds new_1-1 --depth 99

`--have <dir>` seeds from the maps already exported (reading `nav/*_gat.bin`),
which makes "what is one warp away from what I have?" a one-liner — the usual
question when deciding what to export next.

Prints the closure, and with `--missing` only the part not exported yet, as a
space-separated list ready to hand to the exporter:

    python -m ragnadot.godot_export $(python tools/map_closure.py ... --missing)
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path

# <map>,<x>,<y>,<dir>  warp|warp2  <name>  <xs>,<ys>,<tomap>,<tox>,<toy>
# Tabs are the field separator in rAthena scripts, but whitespace is tolerated
# here because a few community files use spaces.
WARP_RE = re.compile(
    r"^(?P<src>[\w@-]+)\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s+"
    r"warp2?\b[^\t ]*\s+\S+\s+"
    r"\d+\s*,\s*\d+\s*,\s*(?P<dst>[\w@-]+)\s*,\s*\d+\s*,\s*\d+",
    re.IGNORECASE,
)


def parse_warps(rathena: Path) -> dict[str, set[str]]:
    """map -> set of maps reachable from it by one warp."""
    edges: dict[str, set[str]] = {}
    roots = [rathena / "npc" / "warps", rathena / "npc" / "re" / "warps"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.txt"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                m = WARP_RE.match(line)
                if m:
                    src, dst = m.group("src").lower(), m.group("dst").lower()
                    edges.setdefault(src, set()).add(dst)
    return edges


def closure(edges: dict[str, set[str]], seeds: list[str], depth: int) -> dict[str, int]:
    """Maps reachable from `seeds` within `depth` warps, mapped to their distance."""
    seen: dict[str, int] = {s: 0 for s in seeds}
    queue: deque[str] = deque(seeds)
    while queue:
        cur = queue.popleft()
        d = seen[cur]
        if d >= depth:
            continue
        for nxt in sorted(edges.get(cur, ())):
            if nxt not in seen:
                seen[nxt] = d + 1
                queue.append(nxt)
    return seen


def exported(assets: Path) -> set[str]:
    """Maps that already have a walk grid — the thing that actually gates play."""
    nav = assets / "nav"
    return {p.name[: -len("_gat.bin")] for p in nav.glob("*_gat.bin")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rathena", type=Path, required=True,
                    help="rAthena checkout (its npc/warps are the graph)")
    ap.add_argument("--seeds", nargs="*", default=[],
                    help="maps to start from, e.g. prontera new_1-1")
    ap.add_argument("--have", type=Path,
                    help="assets root; seed from the maps already exported there")
    ap.add_argument("--depth", type=int, default=1,
                    help="how many warps out to follow (default 1)")
    ap.add_argument("--missing", action="store_true",
                    help="print only maps not exported yet (needs --have)")
    args = ap.parse_args()

    edges = parse_warps(args.rathena)
    if not edges:
        print("no warps parsed — is --rathena a real checkout?", file=sys.stderr)
        return 1

    seeds = [s.lower() for s in args.seeds]
    have: set[str] = set()
    if args.have:
        have = exported(args.have)
        seeds = sorted(set(seeds) | have)
    if not seeds:
        print("nothing to start from: pass --seeds and/or --have", file=sys.stderr)
        return 1

    reach = closure(edges, seeds, args.depth)
    if args.missing:
        if not args.have:
            print("--missing needs --have", file=sys.stderr)
            return 1
        out = sorted(m for m in reach if m not in have)
        print(" ".join(out))
        print(f"# {len(out)} missing of {len(reach)} reachable "
              f"within {args.depth} warp(s) of {len(seeds)} seed(s)", file=sys.stderr)
        return 0

    for m in sorted(reach, key=lambda k: (reach[k], k)):
        mark = "" if m in have else "  (not exported)"
        print(f"{reach[m]}  {m}{mark}")
    print(f"# {len(reach)} maps, {len(edges)} with outgoing warps", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
