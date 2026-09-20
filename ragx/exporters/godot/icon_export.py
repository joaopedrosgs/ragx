"""Export RO item + skill icons and item illustrations as individual raw PNGs.

The client's inventory icons and item illustrations are individual BMPs in the
GRF under ``data/texture/유저인터페이스/item/`` (8.5k icons, skill icons among them)
and ``.../collection/`` (7.3k illustrations). This dumps each, magenta-keyed, as
its own transparent PNG, plus a small ``items.json`` lookup. The Godot runtime
packs only the icons actually on screen into dynamic atlases (see the AtlasPool /
IconDb autoloads) — so nothing is pre-baked and nothing but the lookup needs to
be parsed up front; memory tracks what's visible, not what's on disk.

The client ``System/itemInfo.lua`` (id -> resource/display name) is read so the
game can resolve an icon by item id; skill icons are matched to the SKID
constants in the already-exported ``data/skills.json`` by name.

Outputs under ``<godot>/icons/`` (loose: shipped next to the executable, loaded
at runtime via Image.load_from_file, never imported into the PCK)::

  icons/item/<basename>.png         8.5k item + skill icons (one file each)
  icons/collection/<basename>.png   7.3k item illustrations
  icons/illust/<basename>.png       NPC cut-in portraits (`cutin` script cmd)
  icons/items.json                  { items: {id: [icon, name]},
                                      skills_by_id: {id: icon},
                                      skills_by_const: {const: icon} }

Usage::

    python -m ragnadot.icon_export --client C:/Gravity/Ragnarok --out <godot>
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import lupa.lua51 as lua51
import numpy as np
from PIL import Image

from ragx.grf import GrfStack
from .lua51 import rebase_chunk

UI_KOR = "유저인터페이스"
TEX_PREFIX = "data\\texture\\" + UI_KOR + "\\"
MAGENTA = (255, 0, 255)


# --------------------------------------------------------------------------- #
# itemInfo
# --------------------------------------------------------------------------- #
def _dec(value, encoding: str) -> str:
    if not isinstance(value, bytes):
        return "" if value is None else str(value)
    try:
        return value.decode(encoding)
    except UnicodeDecodeError:
        return value.decode(encoding, "replace")


def load_item_info(client: str) -> dict[str, dict]:
    """Read System/itemInfo.lua into {id: {name, icon}}.

    `identifiedResourceName` is the cp949 icon basename (matches the GRF's
    lowercased Korean file names); `identifiedDisplayName` is the LATAM cp1252
    localisation. The compiled .lub ships as an empty stub, so prefer the .lua.
    """
    system = Path(client) / "System"
    source = None
    for name in ("itemInfo.lua", "itemInfo.lub"):
        path = system / name
        if path.is_file() and path.stat().st_size > 1024:  # skip the stub
            source = path
            break
    if source is None:
        print("  itemInfo not found; skipping item id table")
        return {}

    runtime = lua51.LuaRuntime(encoding=None)
    chunk = rebase_chunk(source.read_bytes(), 8)
    fn = runtime.eval(b"loadstring")(chunk, b"@itemInfo")
    if isinstance(fn, tuple):
        raise ValueError(f"itemInfo load error: {fn[1]!r}")
    fn()
    tbl = runtime.eval(b"tbl")
    if tbl is None:
        return {}

    items: dict[str, dict] = {}
    for item_id in tbl.keys():
        if not isinstance(item_id, int):
            continue
        row = tbl[item_id]
        icon = _dec(row[b"identifiedResourceName"], "cp949").lower()
        if not icon:
            continue
        items[str(item_id)] = {"name": _dec(row[b"identifiedDisplayName"], "cp1252"),
                               "icon": icon}
    return items


# --------------------------------------------------------------------------- #
# raw icon extraction
# --------------------------------------------------------------------------- #
def _keyed_png(data: bytes) -> Image.Image | None:
    """Open a BMP and turn magenta into transparency; None on a broken bitmap."""
    try:
        image = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    arr = np.asarray(image, dtype=np.uint8).copy()
    if arr.ndim != 3 or arr.shape[2] != 4 or arr.size == 0:
        return None
    key = (arr[:, :, 0] == MAGENTA[0]) & (arr[:, :, 1] == MAGENTA[1]) \
        & (arr[:, :, 2] == MAGENTA[2])
    arr[key] = (0, 0, 0, 0)
    return Image.fromarray(arr, "RGBA")


def export_set(grf, subdir: str, out_dir: Path) -> set[str]:
    """Write every BMP under 유저인터페이스/<subdir>/ as its own keyed PNG.
    Returns the set of basenames written."""
    prefix = (TEX_PREFIX + subdir + "\\").lower()
    names = sorted(n for n in grf.namelist()
                   if n.lower().startswith(prefix) and n.lower().endswith(".bmp"))
    dest = out_dir / subdir
    dest.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    skipped = 0
    for name in names:
        base = name[len(prefix):-4].rsplit("\\", 1)[-1].lower()
        if not base or base in written:
            continue
        image = _keyed_png(grf.read(name))
        if image is None:
            skipped += 1
            continue
        image.save(dest / (base + ".png"))
        written.add(base)
    print(f"  {subdir}: {len(written)} PNGs written ({skipped} skipped) -> {dest}")
    return written


def build_skill_icons(skills_path: Path, item_icons: set[str]
                      ) -> tuple[dict[str, str], dict[str, str]]:
    """Map skills to their icon basename (skill icons live in the item set, named
    after the lowercased SKID const, e.g. AB_ADORAMUS -> ab_adoramus.png).
    Returns (skill id -> basename, SKID const -> basename)."""
    by_id: dict[str, str] = {}
    by_const: dict[str, str] = {}
    if not skills_path.is_file():
        return by_id, by_const
    skills = json.loads(skills_path.read_text(encoding="utf-8"))
    for sid, entry in skills.items():
        const = entry.get("const", "")
        base = const.lower()
        if const and base in item_icons:
            by_id[str(sid)] = base
            by_const[const] = base
    return by_id, by_const


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument("--out", required=True, help="Godot project root")
    parser.add_argument("--no-collection", action="store_true",
                        help="skip the large item illustrations")
    parser.add_argument("--no-illust", action="store_true",
                        help="skip the NPC cut-in illustrations")
    args = parser.parse_args()

    client = args.client
    icons_dir = Path(args.out) / "icons"
    data_dir = Path(args.out) / "data"

    grf = GrfStack([str(Path(client) / "data.grf"),
                    str(Path(client) / "event.grf")])

    item_icons = export_set(grf, "item", icons_dir)
    if not args.no_collection:
        export_set(grf, "collection", icons_dir)
    # NPC cut-ins. A script names one with `cutin "<basename>",<pos>` and the
    # server forwards that name in ZC_SHOW_IMAGE, so the basename IS the key —
    # no id table, unlike items.
    if not args.no_illust:
        export_set(grf, "illust", icons_dir)

    info = load_item_info(client)
    items = {iid: [v["icon"], v["name"]] for iid, v in info.items()
             if v["icon"] in item_icons}
    skills_by_id, skills_by_const = build_skill_icons(data_dir / "skills.json", item_icons)

    (icons_dir / "items.json").write_text(
        json.dumps({"items": items, "skills_by_id": skills_by_id,
                    "skills_by_const": skills_by_const},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"  items.json: {len(items)} items, {len(skills_by_id)} skills "
          f"-> {icons_dir}/items.json")

    gdignore = icons_dir / ".gdignore"
    if not gdignore.exists():
        gdignore.write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
