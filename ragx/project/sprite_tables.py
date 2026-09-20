"""Export the client's sprite-resolution tables to JSON for the Godot runtime.

The client resolves "job 1002" / "headgear 4" / "weapon 1" to sprite file
paths through compiled lua tables (see references/zrenderer/RESOLVER.md).
This module executes those .lub files (ragnadot.lua51) and dumps everything
the Godot actor system needs as plain JSON into <out>/data/.

Player body/weapon-folder names are not in the lua files (the official
client hardcodes them); those come from zrenderer's community-maintained
resolver_data text tables, indexed by job id (ids > 4000 subtract 3950).

Usage:
    python -m ragnadot.sprite_tables --out C:/Users/pedro/Documents/ragnarok
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ragx.grf import GrfArchive
from .lua51 import LuaEnv, decode

# zrenderer's load order; later files depend on globals from earlier ones.
LUA_FILES = [
    "datainfo/accessoryid", "datainfo/accname", "datainfo/accname_f",
    "datainfo/spriterobeid", "datainfo/spriterobename", "datainfo/spriterobename_f",
    "datainfo/weapontable", "datainfo/weapontable_f",
    "datainfo/npcidentity", "datainfo/jobidentity",
    "datainfo/jobname", "datainfo/jobname_f",
    "datainfo/shadowtable", "datainfo/shadowtable_f",
    "skillinfoz/jobinheritlist",
    "spreditinfo/2dlayerdir_f",
    "spreditinfo/biglayerdir_female", "spreditinfo/biglayerdir_male",
    "spreditinfo/_new_2dlayerdir_f",
    "spreditinfo/_new_biglayerdir_female", "spreditinfo/_new_biglayerdir_male",
    "spreditinfo/_new_smalllayerdir_female", "spreditinfo/_new_smalllayerdir_male",
    "spreditinfo/smalllayerdir_female", "spreditinfo/smalllayerdir_male",
    "offsetitempos/offsetitempos_f", "offsetitempos/offsetitempos",
]

ZRENDERER_DATA = Path(__file__).resolve().parent.parent / "references" / "zrenderer" / "resolver_data"


def lua_to_python(value):
    """Recursively convert a lupa table to dict/list-of-pairs JSON-safe data."""
    if type(value).__name__ != "_LuaTable":
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return decode(value)
    result = {}
    for key, item in value.items():
        if callable(item) and type(item).__name__ != "_LuaTable":
            continue
        key = decode(key)
        if isinstance(key, float) and key.is_integer():
            key = int(key)
        result[str(key)] = lua_to_python(item)
    return result


def name_to_path(name: str) -> str:
    """Korean sprite names sometimes contain backslash sub-folders."""
    return name.replace("\\", "/").lower()


def is_player(jobid: int) -> bool:
    return jobid < 45 or 0 <= jobid - 4001 < 1999


def nonplayer_folder(jobid: int) -> str | None:
    if (45 <= jobid < 1000) or (10_001 <= jobid < 19_999):
        return "npc"
    if 0 <= jobid - 6017 <= 29:
        return "인간족/몸통"  # mercenary
    if 0 <= jobid - 6001 <= 51:
        return "homun"
    if (1001 <= jobid < 3999) or jobid >= 20_000:
        return "몬스터"
    return None


def export_tables(client: str, out_dir: Path) -> None:
    grf = GrfArchive(str(Path(client) / "data.grf"))
    env = LuaEnv(grf)
    for lua_file in LUA_FILES:
        env.load(lua_file)

    out_dir.mkdir(parents=True, exist_ok=True)

    def write(name: str, payload) -> None:
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        size = path.stat().st_size
        print(f"  {name}.json ({size:,} bytes)")

    # --- non-player bodies: job id -> sprite path under data/sprite/ ------
    jobtbl = env.table("jobtbl")
    job_ids = sorted({int(v) for v in jobtbl.values()})
    nonplayer = {}
    for jobid in job_ids:
        folder = nonplayer_folder(jobid)
        if folder is None:
            continue
        name = decode(env.call("ReqJobName", jobid))
        if name:
            nonplayer[str(jobid)] = f"{folder}/{name_to_path(name)}"
    write("nonplayer_bodies", nonplayer)

    # job enum name -> id (lets the game reference JT_PORING by name)
    write("job_ids", {decode(k): int(v) for k, v in jobtbl.items()})

    # --- player tables (zrenderer resolver_data, line index = job id) -----
    for txt, name in (("job_names.txt", "player_bodies"),
                      ("job_weapon_names.txt", "player_weapon_folders"),
                      ("job_pal_names.txt", "player_palettes"),
                      ("imf_names.txt", "player_imf_names"),
                      ("shield_names.txt", "shield_names")):
        lines = (ZRENDERER_DATA / txt).read_text(encoding="utf-8").splitlines()
        write(name, [name_to_path(line) for line in lines])

    # --- headgears: id -> name fragment ('악세사리/<g>/<g><name>') --------
    acc_ids = sorted({int(v) for v in env.table("ACCESSORY_IDs").values()})
    headgears = {}
    for accid in acc_ids:
        name = decode(env.call("ReqAccName", accid))
        if name:
            headgears[str(accid)] = name_to_path(name)
    write("headgear_names", headgears)

    # --- weapons: id -> name fragment + replacement ids -------------------
    weapon_ids = {int(v) for v in env.table("Weapon_IDs").values()}
    weapon_ids |= {int(v) for v in env.table("Expansion_Weapon_IDs").values()}
    weapon_names = {}
    weapon_real = {}
    for wid in range(0, max(weapon_ids) + 200):
        name = decode(env.call("ReqWeaponName", wid))
        if name:
            weapon_names[str(wid)] = name_to_path(name)
        real = env.call("GetRealWeaponId", wid)
        if real and int(real) != wid:
            weapon_real[str(wid)] = int(real)
    write("weapon_names", weapon_names)
    write("weapon_real_ids", weapon_real)

    # --- garments ----------------------------------------------------------
    robe_ids = sorted({int(v) for v in env.table("SPRITE_ROBE_IDs").values()})
    robes = {}
    for rid in robe_ids:
        name = decode(env.call("ReqRobSprName_V2", rid, False))
        if name:
            robes[str(rid)] = name_to_path(name)
    write("robe_names", robes)
    write("robe_top_layer", sorted(int(v) for v in env.table("RobeTopLayer").values()))
    # [job][action] -> 1-indexed list of frames where the garment draws on top
    write("garment_layer_dirs", {
        "small_m": lua_to_python(env.table("_New_SmallLayerDir_M")),
        "small_f": lua_to_python(env.table("_New_SmallLayerDir_F")),
        "big_m": lua_to_python(env.table("_New_BigLayerDir_M")),
        "big_f": lua_to_python(env.table("_New_BigLayerDir_F")),
        "size_types": lua_to_python(env.table("LayerSizeTypeList")),
    })

    # --- misc ---------------------------------------------------------------
    write("shadow_factors", lua_to_python(env.table("ShadowFactorTable")))
    write("sprite_inherit", {
        "sprite": lua_to_python(env.table("SPRITE_INHERIT_LIST")),
        "exception": lua_to_python(env.table("EXCEPTION_SPRITE_INHERIT_LIST")),
        "riding": lua_to_python(env.table("RIDING_SPRITE_INHERIT_LIST")),
        "job": lua_to_python(env.table("JOB_INHERIT_LIST")),
        "job2": lua_to_python(env.table("JOB_INHERIT_LIST2")),
    })

    # Doram headgear pixel offsets: classnum -> [gender][direction] -> [x, y]
    doram = {}
    offset_table = env.table("OffsetItemPos")
    if offset_table is not None and offset_table[b"Doram"] is not None:
        class_nums = {int(entry[b"ClassNum"]) for entry in offset_table[b"Doram"].values()}
        for class_num in sorted(class_nums):
            per_gender = []
            for gender in (0, 1):
                offsets = []
                for direction in range(8):
                    result = env.call("OffsetItemPos_GetOffsetForDoram",
                                      class_num, direction, gender)
                    if isinstance(result, tuple):
                        offsets.append([int(result[0] or 0), int(result[1] or 0)])
                    else:
                        offsets.append([0, 0])
                per_gender.append(offsets)
            doram[str(class_num)] = per_gender
    write("doram_head_offsets", doram)

    # --- IMF head priorities ------------------------------------------------
    # imf_priorities[<imf name>][<action>] = list of frame indices where the
    # head must be drawn before the body. Only exceptions are stored.
    from ragx.formats import imf as imf_format

    imf_priorities = {}
    imf_failures = 0
    for grf_name in grf.namelist():
        if not grf_name.endswith(".imf"):
            continue
        try:
            parsed = imf_format.parse(grf.read(grf_name))
        except Exception:  # noqa: BLE001 - a few official files are broken
            imf_failures += 1
            continue
        if len(parsed.priorities) < 2:
            continue
        head_first = {}
        for action, frames in enumerate(parsed.priorities[1]):
            hits = [f for f, priority in enumerate(frames) if priority == 1]
            if hits:
                head_first[str(action)] = hits
        if head_first:
            name = grf_name.rsplit("\\", 1)[-1].removesuffix(".imf")
            imf_priorities[name] = head_first
    write("imf_priorities", imf_priorities)
    if imf_failures:
        print(f"  ({imf_failures} broken .imf files skipped)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument("--out", required=True,
                        help="Godot project root; tables go to <out>/data")
    args = parser.parse_args()
    export_tables(args.client, Path(args.out) / "data")


if __name__ == "__main__":
    main()
