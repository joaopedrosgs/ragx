"""Keep UI atlas regions and nine-slice styles as native editable resources."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .compact_sprites import ImagePages, encoded


def compile_ui(source: Path, output: Path, resource_root: str = 'res://ui/skin') -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Source and output must be separate trees')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty')
    pages = ImagePages(output / 'pages', importer='texture')
    images = {}
    for path in sorted(source.rglob('*.png')):
        relative = path.relative_to(source).as_posix()
        descriptor = pages.add(path)
        images[relative] = descriptor
        page, x, y, w, h, _ = descriptor
        target = output / (relative[:-4] + '.texture.tres')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '[gd_resource type="AtlasTexture" load_steps=2 format=3]\n\n'
            f'[ext_resource type="Texture2D" path="{resource_root}/pages/{page}" id="1"]\n\n'
            '[resource]\natlas = ExtResource("1")\n'
            f'region = Rect2({x}, {y}, {w}, {h})\nfilter_clip = true\n', encoding='utf-8')
    pages.flush()
    # Preserve authored crop, content margins and stretch rules, replacing only
    # each texture dependency. No guessed nine-slice margins or baked labels.
    for path in sorted(source.rglob('*.tres')):
        target = output / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding='utf-8')
        for match in list(re.finditer(re.escape(resource_root) + r'/([^"\n]+)\.png', text)):
            relative = match.group(1) + '.png'
            if relative not in images:
                raise ValueError(f'Missing UI style texture: {relative}')
        text = re.sub(re.escape(resource_root) + r'/([^"\n]+)\.png',
                      lambda m: resource_root + '/' + m.group(1) + '.texture.tres', text)
        target.write_text(text, encoding='utf-8')
    for path in sorted(source.rglob('*.json')):
        target = output / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    (output / 'atlas-regions.json').write_bytes(encoded({'version': 1, 'images': images}))
    return {'textures': len(images), 'files': sum(1 for p in output.rglob('*') if p.is_file()),
            'bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compile_ui(args.source, args.output)), flush=True)


if __name__ == '__main__':
    main()
