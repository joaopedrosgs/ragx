"""Extract RO interface textures into a Godot project as transparent PNGs.

The game's **base** interface bitmaps live under the extracted GRF tree at
``extracted_assets/data/texture/유저인터페이스/`` (the default skin). The client's
``skin/<name>/`` folders are *overrides* of that base — so by default we use the
base, and ``--skin`` only layers a named skin on top. All are BMPs that key on
magenta (255,0,255). Files are keyed and written to ``<out>/ui/skin/<group>/``,
plus composited 9-slice theme textures in ``<out>/ui/skin/theme/``.

    python -m ragnadot.ui_export --out C:/Users/me/Documents/ragnarok          # base
    python -m ragnadot.ui_export --out ... --client C:/Gravity/Ragnarok --skin "America Latina"
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_KOR = "유저인터페이스"
DEFAULT_UI_DIR = PROJECT_ROOT / "extracted_assets" / "data" / "texture" / UI_KOR
MAGENTA = (255, 0, 255)

# Files each window needs, grouped by their interface subfolder.
GROUPS: dict[str, list[str]] = {
    "basic_interface": [
        # window chrome (classic basewin_bg + modern basewin_bg2)
        "basewin_bg.bmp", "basewin_bg2.bmp", "basewin_mini.bmp", "titlebar_fix.bmp",
        # HP/SP gauges (left cap, stretchable mid, right cap) + track
        "gze_bg.bmp",
        "gzered_left.bmp", "gzered_mid.bmp", "gzered_right.bmp",
        "gzeblue_left.bmp", "gzeblue_mid.bmp", "gzeblue_right.bmp",
        # status window (baked stat labels) + its stat-up arrows
        "statwin0_bg.bmp", "statwin1_bg.bmp",
        "arw_right.bmp", "arw_right_on.bmp",
        # system buttons (base ball + red/pink variant for close)
        "sys_base_off.bmp", "sys_base_on.bmp",
        "sys_base_pink_off.bmp",
        "sys_mini_off.bmp", "sys_mini_on.bmp",
        "sys_close_off.bmp", "sys_close_on.bmp",
        # menu icon buttons
        "btn_status_off.bmp", "btn_status_on.bmp",
        "btn_option_off.bmp", "btn_option_on.bmp",
        "btn_items_off.bmp", "btn_items_on.bmp",
        "btn_equip_off.bmp", "btn_equip_on.bmp",
        "btn_skill_off.bmp", "btn_skill_on.bmp",
        "btn_map_off.bmp", "btn_map_on.bmp",
        "btn_comm_off.bmp", "btn_comm_on.bmp",
        "btn_friend_off.bmp", "btn_friend_on.bmp",
    ],
}

def _keyed_png(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    for y in range(height):
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            if (r, g, b) == MAGENTA:
                pixels[x, y] = (0, 0, 0, 0)
    return image


def _load(name: str, group: str, dirs: list[Path]) -> bytes | None:
    """First match for `name` across the source dirs (override first, base last),
    trying the named subfolder then the UI root in each."""
    subs = [group, ""] if group else [""]
    for base in dirs:
        for sub in subs:
            path = (base / sub / name) if sub else (base / name)
            if path.is_file():
                return path.read_bytes()
    return None


def _keyed(name: str, dirs: list[Path]) -> Image.Image | None:
    data = _load(name, "basic_interface", dirs)
    return _keyed_png(data) if data is not None else None


def compose_theme(dirs: list[Path], out: Path) -> None:
    """Composite RO's separate 9-slice pieces into single textures a Godot Theme
    can use as StyleBoxTextures (window frame, button states, title bar)."""
    dest = out / "ui" / "skin" / "theme"
    dest.mkdir(parents=True, exist_ok=True)

    cs = 14
    keys = ["lu", "mu", "ru", "lm", "rm", "ld", "md", "rd"]
    pieces = {k: _keyed(f"sysbox_{k}.bmp", dirs) for k in keys}
    if all(pieces.values()):
        frame = Image.new("RGBA", (cs * 3, cs * 3), (0, 0, 0, 0))
        spots = {"lu": (0, 0), "mu": (cs, 0), "ru": (2 * cs, 0),
                 "lm": (0, cs), "rm": (2 * cs, cs),
                 "ld": (0, 2 * cs), "md": (cs, 2 * cs), "rd": (2 * cs, 2 * cs)}
        for k, xy in spots.items():
            frame.paste(pieces[k], xy)
        fill = pieces["md"].getpixel((cs // 2, 0))
        for y in range(cs):
            for x in range(cs):
                frame.putpixel((cs + x, cs + y), fill)
        frame.save(dest / "window_frame.png")

    for state, src in (("normal", "out"), ("hover", "over"),
                       ("pressed", "press"), ("disabled", "disable")):
        l = _keyed(f"btn_{src}_left.bmp", dirs)
        m = _keyed(f"btn_{src}_mid.bmp", dirs)
        r = _keyed(f"btn_{src}_right.bmp", dirs)
        if l and m and r:
            button = Image.new("RGBA", (l.width + m.width + r.width, l.height), (0, 0, 0, 0))
            button.paste(l, (0, 0))
            button.paste(m, (l.width, 0))
            button.paste(r, (l.width + m.width, 0))
            button.save(dest / f"button_{state}.png")

    title = _keyed("titlebar_fix.bmp", dirs)
    if title:
        title.save(dest / "titlebar.png")
    print(f"theme: composited textures -> {dest}")


def export(dirs: list[Path], out: Path, groups: list[str]) -> None:
    for group in groups:
        dest = out / "ui" / "skin" / group
        dest.mkdir(parents=True, exist_ok=True)
        found = missing = 0
        for name in GROUPS[group]:
            data = _load(name, group, dirs)
            if data is None:
                print(f"  MISSING {group}/{name}")
                missing += 1
                continue
            _keyed_png(data).save(dest / (Path(name).stem + ".png"))
            found += 1
        print(f"{group}: {found} written, {missing} missing -> {dest}")
    compose_theme(dirs, out)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="Godot project root")
    parser.add_argument("--ui-dir", type=Path, default=DEFAULT_UI_DIR,
                        help="extracted base UI dir (default: extracted_assets/.../유저인터페이스)")
    parser.add_argument("--client", default=None,
                        help="client dir (only needed to layer a --skin)")
    parser.add_argument("--skin", default=None,
                        help="optional skin name to override the base")
    parser.add_argument("--groups", nargs="*", default=list(GROUPS))
    args = parser.parse_args()

    dirs: list[Path] = []
    if args.skin and args.client:
        dirs.append(Path(args.client) / "skin" / args.skin)
    dirs.append(args.ui_dir)
    if not args.ui_dir.is_dir():
        print(f"warning: base UI dir not found: {args.ui_dir}")
    export(dirs, Path(args.out), args.groups)


if __name__ == "__main__":
    main()
