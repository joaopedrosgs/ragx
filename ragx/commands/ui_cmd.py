"""``ragx ui`` — export interface bitmaps to transparent PNG.

The base interface bitmaps live in the client GRF under
``data\\texture\\유저인터페이스\\`` (these are read directly — no separate extract
step). The client's ``skin/<name>/`` folders are *overrides* of that base, so by
default we use the base and ``--skin`` only layers a named skin on top.

All sources are BMPs that key on magenta (255, 0, 255). Files are keyed and
written to ``<out>/ui/skin/<group>/``, plus composited 9-slice theme textures
(window frame, button states, title bar) in ``<out>/ui/skin/theme/``.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Callable

from .. import client as client_mod

UI_KOR = "유저인터페이스"
UI_GRF_PREFIX = f"data\\texture\\{UI_KOR}\\"
MAGENTA = (255, 0, 255)

# Files each window needs, grouped by their interface sub-folder.
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

# A loader returns the raw bytes of an interface bitmap, or None if absent.
Loader = Callable[[str, str], "bytes | None"]


def _make_loader(grf, skin_dir: Path | None) -> Loader:
    """Build a `(name, group) -> bytes | None` loader. A named skin folder on
    disk (if given) overrides the GRF base; each source is tried with the group
    sub-folder first, then the interface root."""
    def load(name: str, group: str) -> bytes | None:
        subs = [group, ""] if group else [""]
        if skin_dir is not None:
            for sub in subs:
                path = (skin_dir / sub / name) if sub else (skin_dir / name)
                if path.is_file():
                    return path.read_bytes()
        for sub in subs:
            key = UI_GRF_PREFIX + (f"{sub}\\{name}" if sub else name)
            if key in grf:
                return grf.read(key)
        return None
    return load


def _keyed_png(data: bytes):
    """Decode a BMP and turn its magenta key pixels transparent."""
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    for y in range(height):
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            if (r, g, b) == MAGENTA:
                pixels[x, y] = (0, 0, 0, 0)
    return image


def _keyed(load: Loader, name: str):
    data = load(name, "basic_interface")
    return _keyed_png(data) if data is not None else None


def compose_theme(load: Loader, out: Path) -> None:
    """Composite RO's separate 9-slice pieces into single textures a UI theme
    can use as stretchable backgrounds (window frame, button states, title bar)."""
    from PIL import Image

    dest = out / "ui" / "skin" / "theme"
    dest.mkdir(parents=True, exist_ok=True)

    cs = 14
    keys = ["lu", "mu", "ru", "lm", "rm", "ld", "md", "rd"]
    pieces = {k: _keyed(load, f"sysbox_{k}.bmp") for k in keys}
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
        left = _keyed(load, f"btn_{src}_left.bmp")
        mid = _keyed(load, f"btn_{src}_mid.bmp")
        right = _keyed(load, f"btn_{src}_right.bmp")
        if left and mid and right:
            button = Image.new("RGBA", (left.width + mid.width + right.width, left.height),
                               (0, 0, 0, 0))
            button.paste(left, (0, 0))
            button.paste(mid, (left.width, 0))
            button.paste(right, (left.width + mid.width, 0))
            button.save(dest / f"button_{state}.png")

    title = _keyed(load, "titlebar_fix.bmp")
    if title:
        title.save(dest / "titlebar.png")
    print(f"theme: composited textures -> {dest}")


def export(load: Loader, out: Path, groups: list[str]) -> None:
    for group in groups:
        dest = out / "ui" / "skin" / group
        dest.mkdir(parents=True, exist_ok=True)
        found = missing = 0
        for name in GROUPS[group]:
            data = load(name, group)
            if data is None:
                print(f"  MISSING {group}/{name}")
                missing += 1
                continue
            _keyed_png(data).save(dest / (Path(name).stem + ".png"))
            found += 1
        print(f"{group}: {found} written, {missing} missing -> {dest}")
    compose_theme(load, out)


def run(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    groups = args.groups or list(GROUPS)
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        sys.exit(f"unknown UI group(s): {', '.join(unknown)}\n"
                 f"  available: {', '.join(GROUPS)}")

    skin_dir = None
    if args.skin:
        skin_dir = Path(args.client) / "skin" / args.skin
        if not skin_dir.is_dir():
            print(f"warning: skin folder not found, using base only: {skin_dir}")
            skin_dir = None

    grf = client_mod.open_stack(args.client)
    try:
        export(_make_loader(grf, skin_dir), Path(args.out), groups)
    finally:
        grf.close()
    return 0
