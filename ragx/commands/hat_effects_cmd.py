"""Export the client's costume (hat) effect table: the id the server sends -> art.

ZC_EQUIPMENT_EFFECT carries bare numbers. The client looks each one up in its own
``hatEffectTable`` (``hateffectinfo/hateffectinfo.lub``, keyed through
``HatEFID`` from ``hateffectids.lub``) and draws either an STR from
``data\\texture\\effect\\`` or an entry of its effect table. The export is keyed
by that NUMBER, exactly as the client reads it: rAthena's ``e_hat_effects`` names
drift from this client's after about id 225, but only the number is on the wire.

Output: ``<out>/data/hat_effects.json``::

    {"33": {"name": "HAT_EF_...", "str": "efst_strangelights/strangelights",
            "pos": -4, "pos_x": 0, "shrink_size": true, "shrink_position": true},
     "35": {"name": "HAT_EF_...", "effect_id": 1057}}

``str`` is the name the ``effects`` command exports that STR under; ``pos`` and
``pos_x`` are the client's raw offsets, in its own units. The remaining flags
keep the client's meaning: ``before`` (drawn behind the character),
``ignore_riding``, ``head`` (follows the head), ``doram_y`` and ``pair``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import client as client_mod
from ..lua import LuaEnv, decode

## Client field -> (exported name, kind).
FIELDS = {
    "hatEffectID": ("effect_id", "number"),
    "hatEffectPos": ("pos", "number"),
    "hatEffectPosX": ("pos_x", "number"),
    "isRenderBeforeCharacter": ("before", "bool"),
    "isIgnoreRiding": ("ignore_riding", "bool"),
    "isAttachedHead": ("head", "bool"),
    "isAdjustSizeWhenShrinkState": ("shrink_size", "bool"),
    "isAdjustPositionWhenShrinkState": ("shrink_position", "bool"),
    "hatEffectExtraDoramY": ("doram_y", "number"),
    "isEffectPair": ("pair", "bool"),
}


def str_effect_name(resource: str) -> str:
    """``efst_Foo\\bar.str`` -> ``efst_foo/bar``, the ``effects`` export's name for it."""
    name = resource.replace("\\", "/").lower()
    return name[:-4] if name.endswith(".str") else name


def _number(value):
    number = float(value)
    return int(number) if number.is_integer() else number


def convert(table: dict, ids: dict[str, int]) -> dict[str, dict]:
    """Plain ``hatEffectTable`` rows -> the exported rows, ordered by id."""
    names: dict[int, str] = {}
    for name, number in sorted(ids.items()):
        if name.startswith("HAT_EF_"):
            names.setdefault(int(number), name)
    rows: dict[int, dict] = {}
    for key, row in table.items():
        number = int(key)
        entry: dict = {}
        if number in names:
            entry["name"] = names[number]
        resource = row.get("resourceFileName")
        if resource:
            entry["str"] = str_effect_name(str(resource))
        for field, (out_name, kind) in FIELDS.items():
            if field in row:
                entry[out_name] = bool(row[field]) if kind == "bool" else _number(row[field])
        rows[number] = entry
    return {str(number): rows[number] for number in sorted(rows)}


def _plain(value, lua_type):
    """A Lua value as plain Python, tables recursively, strings decoded."""
    if decode(lua_type(value)) != "table":
        return decode(value)
    return {decode(key): _plain(item, lua_type) for key, item in value.items()}


def export(grf, out: Path) -> dict:
    env = LuaEnv(grf)
    env.load("hateffectinfo/hateffectids")
    env.load("hateffectinfo/hateffectinfo")
    lua_type = env.runtime.eval(b"type")
    ids = {str(name): int(number)
           for name, number in _plain(env.table("HatEFID"), lua_type).items()}
    rows = convert(_plain(env.table("hatEffectTable"), lua_type), ids)
    target = out / "data" / "hat_effects.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {
        "effects": len(rows),
        "str": sum(1 for row in rows.values() if "str" in row),
        "effect_id": sum(1 for row in rows.values() if "effect_id" in row),
        "path": target,
    }


def run(args) -> int:
    with client_mod.open_stack(args.client) as grf:
        summary = export(grf, Path(args.out))
    print(f"hat effects: {summary['effects']} ids ({summary['str']} STR, "
          f"{summary['effect_id']} effect-table) -> {summary['path']}")
    return 0
