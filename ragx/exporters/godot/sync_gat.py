#!/usr/bin/env python3
"""sync_gat.py — make the client's walk grid agree with the server's collision.

The client is server-authoritative about movement, but its walk grid was not:
`nav/<map>_gat.bin` comes from the client GRF, while rAthena decides what is
walkable from its own `map_cache.dat`. Where the two disagree the client happily
paths onto a cell the server considers a wall, asks to walk there, and rAthena
**refuses in silence** — no ack, no ZC_STOPMOVE. The client walks on prediction
while the server leaves the character standing, and from then on the session is
desynced: no new units, nothing culled, warps never arrive. (`LocalPlayer`'s walk
watchdog recovers from it, but recovering from a self-inflicted wound is worse
than not inflicting it.)

They disagree more than you would hope. Measured against this checkout:

    prt_in   0 cells      geffen  8 (0.01%)     yuno   53 (0.03%)
    prontera 1286 (1.05%)  morocc  1780 (1.74%)

The maps rAthena loads from the base cache agree with the GRF almost exactly;
the handful in `db/re/map_cache.dat`, which overrides it, are stale and are
exactly the ones that refuse walks.

So take the types from the server and keep everything else. A GAT cell carries
BOTH a type and a height; the map cache stores only the type (`mapcache.cpp`:
`m->cells[xy] = type`), and heights are needed for rendering, so this rewrites
the type block in place and leaves the height block alone.

The other direction — regenerating the server's cache from the GRF — is the
alternative, but it rewrites server collision data, and rAthena's own `mapcache`
tool cannot read this GRF anyway (`grfio_reads: data\\prontera.gat not found`
for every map, though ragnadot reads the same archive fine).

Usage:
    python tools/sync_gat.py --rathena <checkout>            # report only
    python tools/sync_gat.py --rathena <checkout> --write    # apply
    python tools/sync_gat.py --rathena <checkout> --write prontera morocc

`nav/` is generated and gitignored, so this is safe to re-run; re-exporting a map
overwrites it with GRF types again, so run this after an export.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

RGAT_HEADER = 14           # 'RGAT' + u8 version + u8 flags + u32 w + u32 h
# rAthena map_gat2cell (src/map/map.cpp): only 1 (wall) and 5 (gap) are not walkable.
WALKABLE = {0, 2, 3, 4, 6}


def load_caches(rathena: Path) -> dict[str, tuple[int, int, bytes]]:
    """map -> (xs, ys, raw type bytes). Later files do NOT override earlier ones:
    rAthena reads db/import, then db/re, then db/ and keeps the first hit, so the
    RE cache wins for the maps it carries. Mirror that precedence here."""
    out: dict[str, tuple[int, int, bytes]] = {}
    for rel in ("db/import/map_cache.dat", "db/re/map_cache.dat", "db/map_cache.dat"):
        path = rathena / rel
        if not path.is_file():
            continue
        blob = path.read_bytes()
        if len(blob) < 8:
            continue
        _, count = struct.unpack_from("<IH", blob, 0)
        off = 8
        for _ in range(count):
            if off + 20 > len(blob):
                break
            name = blob[off:off + 12].split(b"\0")[0].decode("ascii", "replace")
            xs, ys, ln = struct.unpack_from("<hhi", blob, off + 12)
            if name and name not in out and ln > 0:
                try:
                    out[name] = (xs, ys, zlib.decompress(blob[off + 20:off + 20 + ln]))
                except zlib.error:
                    pass
            off += 20 + ln
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rathena", type=Path, required=True)
    ap.add_argument("--assets", type=Path, default=Path("."))
    ap.add_argument("--write", action="store_true", help="apply (default: report only)")
    ap.add_argument("maps", nargs="*", help="limit to these maps (default: all exported)")
    args = ap.parse_args()

    caches = load_caches(args.rathena)
    if not caches:
        print("no map_cache.dat found under --rathena", file=sys.stderr)
        return 1

    nav = args.assets / "nav"
    paths = sorted(nav.glob("*_gat.bin"))
    if args.maps:
        wanted = {m.lower() for m in args.maps}
        paths = [p for p in paths if p.name[:-len("_gat.bin")].lower() in wanted]
    if not paths:
        print(f"no nav/*_gat.bin under {args.assets}", file=sys.stderr)
        return 1

    changed = total_diff = skipped = 0
    for path in paths:
        name = path.name[: -len("_gat.bin")]
        entry = caches.get(name)
        if entry is None:
            skipped += 1
            continue
        sxs, sys_, scells = entry
        blob = bytearray(path.read_bytes())
        if bytes(blob[:4]) != b"RGAT":
            skipped += 1
            continue
        w = struct.unpack_from("<I", blob, 6)[0]
        h = struct.unpack_from("<I", blob, 10)[0]
        n = w * h
        if (w, h) != (sxs, sys_) or len(scells) != n:
            print(f"  {name}: SKIP, dimensions differ "
                  f"(client {w}x{h}, server {sxs}x{sys_})")
            skipped += 1
            continue
        ours = bytes(blob[RGAT_HEADER:RGAT_HEADER + n])
        # Only walkability matters here; a differing type that is walkable on both
        # sides (0 vs 3, say) changes nothing the player can feel.
        diff = sum(1 for i in range(n)
                   if (ours[i] in WALKABLE) != (scells[i] in WALKABLE))
        if diff == 0:
            continue
        changed += 1
        total_diff += diff
        print(f"  {name}: {diff} cells ({100.0 * diff / n:.2f}%) disagree")
        if args.write:
            blob[RGAT_HEADER:RGAT_HEADER + n] = scells   # heights left untouched
            path.write_bytes(bytes(blob))

    verb = "synced" if args.write else "would sync"
    print(f"{verb} {changed} map(s), {total_diff} cells; "
          f"{skipped} not in the server cache (left alone)")
    if not args.write and changed:
        print("re-run with --write to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
