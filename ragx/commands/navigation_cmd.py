"""Export the navigation tables the client's Navigation window searches.

The client's ``/navigation`` window lists every map, NPC and monster spawn it can
route to, read from ``luafiles514/lua files/navigation/navi_{map,npc,mob}_*.lub``.
The row layouts are the ones rAthena's generator writes (``src/map/navi.cpp``)::

    Navi_Map  { map, name, type, width, height }
    Navi_Npc  { map, id, type(101 npc / 102 shop), sprite, name, name2, x, y }
    Navi_Mob  { map, id, type(300 / 301 mvp), amount<<16 | sprite id, name,
                sprite name, level, element<<16 | size<<8 | race }

Two sources, and the first is preferred:

* ``--navi-dir``: the folder ``map-server-generator --generate-navi`` writes
  (``generated/clientside/data/luafiles514/lua files/navigation``). Those tables
  describe the SERVER the client talks to - its own NPCs, shops and spawns - with
  plain names. That is what the tables are for.
* the client's own copies. Their NPC and monster names are Gravity's obfuscated
  keys (``"\\x1c7QYYDA\\x1c"``), so from this source names are left empty and
  only positions survive.

Output: ``<out>/data/navigation.json``::

    {"maps": {"prontera": {"name": "...", "w": 312, "h": 392}},
     "npcs": [{"map": "prontera", "name": "Tool Dealer", "x": 134, "y": 221,
               "shop": true}],
     "mobs": [{"map": "prt_fild08", "name": "Poring", "sprite": "PORING",
               "level": 1, "amount": 70, "mvp": false}]}
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import client as client_mod
from ..lua import LuaEnv, decode

TABLES = {"maps": ("navi_map", "Navi_Map"), "npcs": ("navi_npc", "Navi_Npc"),
          "mobs": ("navi_mob", "Navi_Mob")}
## Client suffixes, newest-first preference, then rAthena's own.
SUFFIXES = ("krpri", "br", "kr", "sak", "")
NPC_SHOP = 102
MOB_MVP = 301
OBFUSCATED = "\x1c"


def _name(value) -> str:
    """A table name, or "" when it is one of the client's obfuscated keys."""
    text = str(value or "")
    return "" if text.startswith(OBFUSCATED) else text


def convert(maps: list, npcs: list, mobs: list) -> dict:
    """Plain row lists (each row a list in the table's order) -> the export."""
    out_maps: dict[str, dict] = {}
    for row in maps:
        code = str(row[0]).lower()
        out_maps[code] = {"name": _name(row[1]), "w": int(row[3]), "h": int(row[4])}
    out_npcs = []
    for row in npcs:
        out_npcs.append({"map": str(row[0]).lower(), "name": _name(row[4]),
                         "x": int(row[6]), "y": int(row[7]),
                         "shop": int(row[2]) == NPC_SHOP})
    out_mobs = []
    for row in mobs:
        out_mobs.append({"map": str(row[0]).lower(), "name": _name(row[4]),
                         "sprite": str(row[5]), "level": int(row[6]),
                         "amount": int(row[3]) >> 16, "mvp": int(row[2]) == MOB_MVP})
    out_npcs.sort(key=lambda r: (r["map"], r["name"], r["x"], r["y"]))
    out_mobs.sort(key=lambda r: (r["map"], r["name"]))
    return {"maps": dict(sorted(out_maps.items())), "npcs": out_npcs, "mobs": out_mobs}


def _rows(env: LuaEnv, table_name: str) -> list:
    table = env.table(table_name)
    if table is None:
        return []
    rows = []
    for key in sorted(table.keys()):
        row = table[key]
        rows.append([decode(row[i]) for i in sorted(row.keys())])
    return rows


def _load_dir(env: LuaEnv, folder: Path, stem: str) -> bool:
    for suffix in SUFFIXES:
        path = folder / (f"{stem}_{suffix}.lub" if suffix else f"{stem}.lub")
        if path.is_file():
            env.runtime.execute(path.read_bytes())
            return True
    return False


def _load_client(env: LuaEnv, stem: str) -> bool:
    for suffix in SUFFIXES:
        name = f"navigation/{stem}_{suffix}" if suffix else f"navigation/{stem}"
        if env.load(name, optional=True):
            return True
    return False


def export(grf, out: Path, navi_dir: Path | None = None) -> dict:
    env = LuaEnv(grf)
    loaded = {}
    for key, (stem, table_name) in TABLES.items():
        found = _load_dir(env, navi_dir, stem) if navi_dir else False
        if not found and grf is not None:
            found = _load_client(env, stem)
        loaded[key] = _rows(env, table_name) if found else []
    data = convert(loaded["maps"], loaded["npcs"], loaded["mobs"])
    target = out / "data" / "navigation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    named = sum(1 for n in data["npcs"] if n["name"])
    return {"maps": len(data["maps"]), "npcs": len(data["npcs"]), "named": named,
            "mobs": len(data["mobs"]), "path": target}


def run(args) -> int:
    navi_dir = Path(args.navi_dir) if args.navi_dir else None
    with client_mod.open_stack(args.client) as grf:
        summary = export(grf, Path(args.out), navi_dir)
    print(f"navigation: {summary['maps']} maps, {summary['npcs']} npcs "
          f"({summary['named']} named), {summary['mobs']} spawns -> {summary['path']}")
    return 0
