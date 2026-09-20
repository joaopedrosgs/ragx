"""Export the reference client's interface art through the pinned ragx reader.

The original bitmaps stay in the GRF; generated PNGs live in ignored ui/skin.
This adapter extends ragx's small default selection without modifying its vendor
snapshot. Run after setup to regenerate the complete window and launcher skin.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', default='C:/Gravity/Ragnarok')
    parser.add_argument('--assets', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    from ragx.client import open_stack
    from ragx.commands.ui_cmd import UI_GRF_PREFIX, _keyed_png, _make_loader, compose_theme
    from .export_ui_primitives import SkinBuilder

    groups = {'basic_interface', 'ro_menu_icon', 'menu_icon', 'achievement_re',
              'inventory', 'statuswnd', 'shortcut', 'renew_questui', 'questitem'}
    count = 0
    with open_stack(args.client) as archive:
        for key in sorted(archive.namelist()):
            if not key.startswith(UI_GRF_PREFIX):
                continue
            relative = key[len(UI_GRF_PREFIX):].split('\\')
            if len(relative) == 1 and relative[0] in {'checkbox_0.bmp', 'checkbox_1.bmp'}:
                dest = args.assets / 'ui/skin' / (Path(relative[0]).stem + '.png')
                _keyed_png(archive.read(key)).save(dest)
                count += 1
                continue
            if len(relative) != 2 or relative[0] not in groups:
                continue
            if Path(relative[-1]).suffix not in {'.bmp', '.tga', '.png'}:
                continue
            dest = args.assets / 'ui/skin' / relative[0] / (Path(relative[-1]).stem + '.png')
            dest.parent.mkdir(parents=True, exist_ok=True)
            _keyed_png(archive.read(key)).save(dest)
            count += 1
        compose_theme(_make_loader(archive, None), args.assets)
        primitives = SkinBuilder(archive, args.assets / 'ui/skin/primitives', 'res://ui/skin/primitives')
        primitives.build()
    print(json.dumps({'ok': True, 'textures': count, 'primitives': len(primitives.assets),
                      'output': str(args.assets / 'ui/skin')}))


if __name__ == '__main__':
    main()
