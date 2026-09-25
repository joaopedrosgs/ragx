"""Compile image families into deduplicated, editor-importable atlas pages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compact_sprites import ImagePages, encoded


def compile_images(source: Path, output: Path, page_size: int = 2048) -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Source and output must be separate trees')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty')
    pages = ImagePages(output / 'pages', page_size)
    images = {}
    for path in sorted(source.rglob('*.png')):
        images[path.relative_to(source).as_posix()] = pages.add(path)
        if len(images) % 2000 == 0:
            print(f'{len(images)} images; {len(pages.unique)} unique', flush=True)
    pages.flush()
    (output / 'library.json').write_bytes(encoded({'version': 1, 'images': images}))
    return {'images': len(images), 'unique': len(pages.unique),
            'files': sum(1 for p in output.rglob('*') if p.is_file()),
            'bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compile_images(args.source, args.output)), flush=True)


if __name__ == '__main__':
    main()
