"""Compile SPR/ACT intermediates into shared animation records and image pages.

This is a representation change, not an archive or compression layer. Textures
remain normal Godot image resources, and animation shards remain inspectable
JSON. Source paths are stable logical identities; identical actions and pixels
are stored once even when hundreds of jobs use the same costume.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from PIL import Image


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def compact_animation(metadata: dict) -> dict:
    """Repeated ACT poses share a frame; delays, order and events stay exact."""
    pool, identities, actions = [], {}, []
    for action in metadata['actions']:
        references = []
        for frame in action['frames']:
            key = encoded(frame)
            if key not in identities:
                identities[key] = len(pool)
                pool.append(frame)
            references.append(identities[key])
        actions.append({**action, 'frames': references})
    return {'actions': actions, 'events': metadata.get('events', []), 'frame_pool': pool}


class Records:
    """Content-addressed records, grouped into bounded editor-visible files."""
    def __init__(self, output: Path, kind: str):
        self.output = output / kind
        self.scratch = output / (kind + '.sqlite')
        self.database = sqlite3.connect(self.scratch)
        self.database.execute('CREATE TABLE records (key TEXT PRIMARY KEY, body BLOB)')
        self.count = 0

    def add(self, record) -> str:
        raw = encoded(record)
        key = hashlib.sha256(raw).hexdigest()
        cursor = self.database.execute('INSERT OR IGNORE INTO records VALUES (?, ?)', (key, raw))
        if cursor.rowcount:
            self.count += 1
        return key

    def finish(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.database.commit()
        for number in range(256):
            prefix = f'{number:02x}'
            records = self.database.execute(
                'SELECT key, body FROM records WHERE key >= ? AND key < ? ORDER BY key',
                (prefix, prefix + 'z'))
            first = records.fetchone()
            if first is None:
                continue
            with (self.output / (prefix + '.json')).open('wb') as stream:
                stream.write(b'{')
                stream.write(encoded(first[0]) + b':' + first[1])
                for key, raw in records:
                    stream.write(b',' + encoded(key) + b':' + raw)
                stream.write(b'}')
        self.database.close()
        self.scratch.unlink()


class ImagePages:
    """Shelf-packed unique sheets. Recolour indices travel with colour pixels.

    Each sheet is cropped back to its original rectangle by the runtime before
    entering its existing LRU atlas. This preserves palette sampling and page
    eviction contracts without retaining the complete exported library in RAM.
    """
    def __init__(self, output: Path, size: int = 2048, importer: str = 'image'):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.importer = importer
        self.unique = {}
        self.page_number = 0
        self.page = None
        self.indices = None
        self.x = self.y = self.row = 0
        self.used_width = self.used_height = 0

    def flush(self):
        if self.page is None:
            return
        self.save(self.page.crop((0, 0, self.used_width, self.used_height)),
                  f'{self.page_number:05d}.png')
        if self.indices is not None:
            self.save(self.indices.crop((0, 0, self.used_width, self.used_height)),
                      f'{self.page_number:05d}.idx.png')
        self.page_number += 1
        self.page = self.indices = None
        self.x = self.y = self.row = self.used_width = self.used_height = 0

    def save(self, pixels: Image.Image, name: str):
        pixels.save(self.output / name)
        # CPU atlases need an Image resource, never a GPU texture readback.
        resource_type = 'Image' if self.importer == 'image' else 'CompressedTexture2D'
        (self.output / (name + '.import')).write_text(
            f'[remap]\nimporter="{self.importer}"\ntype="{resource_type}"\n'
            '\n[deps]\n\n[params]\n', encoding='utf-8')

    def add(self, path: Path):
        with Image.open(path) as opened:
            pixels = opened.convert('RGBA')
        index_path = path.with_suffix('.idx.png')
        indices = None
        if index_path.is_file():
            with Image.open(index_path) as opened:
                indices = opened.convert('L')
            if indices.size != pixels.size:
                raise ValueError(f'Index dimensions differ: {path}')
        signature = hashlib.sha256()
        signature.update(encoded(pixels.size))
        signature.update(pixels.tobytes())
        signature.update(b'indexed' if indices is not None else b'rgba')
        if indices is not None:
            signature.update(indices.tobytes())
        key = signature.hexdigest()
        if key in self.unique:
            return self.unique[key]
        w, h = pixels.size
        if w + 2 > self.size or h + 2 > self.size:
            name = key + '.png'
            self.save(pixels, name)
            if indices is not None:
                self.save(indices, key + '.idx.png')
            result = [name, 0, 0, w, h, indices is not None]
        else:
            if self.x + w + 2 > self.size:
                self.x = 0
                self.y += self.row
                self.row = 0
            if self.y + h + 2 > self.size:
                self.flush()
            if self.page is None:
                self.page = Image.new('RGBA', (self.size, self.size))
            x, y = self.x + 1, self.y + 1
            self.page.paste(pixels, (x, y))
            if indices is not None:
                if self.indices is None:
                    self.indices = Image.new('L', (self.size, self.size))
                self.indices.paste(indices, (x, y))
            result = [f'{self.page_number:05d}.png', x, y, w, h, indices is not None]
            self.x += w + 2
            self.row = max(self.row, h + 2)
            self.used_width = max(self.used_width, self.x)
            self.used_height = max(self.used_height, self.y + self.row)
        self.unique[key] = result
        return result


def compile_sprites(source: Path, output: Path, page_size: int = 2048) -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Source and output must be separate trees')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty; publish only a completed library')
    output.mkdir(parents=True, exist_ok=True)
    actions = Records(output, 'animations')
    sheets = Records(output, 'sheets')
    pages = ImagePages(output / 'pages', page_size)
    sheet_cache = {}
    sprites = {}
    for path in sorted(source.rglob('*.json')):
        metadata = json.loads(path.read_text(encoding='utf-8'))
        relative = path.relative_to(source).with_suffix('').as_posix()
        sheet = metadata.get('sheet', relative + '.png')
        sheet_path = (source / sheet).resolve()
        if source not in sheet_path.parents:
            raise ValueError(f'Sheet outside source: {sheet}')
        if sheet not in sheet_cache:
            sheet_cache[sheet] = pages.add(sheet_path)
        sheet_id = sheets.add({
            'image': sheet_cache[sheet], 'frames': metadata['frames'],
            'indexed_count': metadata['indexed_count'],
        })
        action_id = actions.add(compact_animation(metadata))
        sprites[relative] = [sheet_id, action_id]
        if len(sprites) % 2000 == 0:
            print(f'{len(sprites)} sprites; {actions.count} unique animations; '
                  f'{len(pages.unique)} unique sheets', flush=True)
    pages.flush()
    actions.finish()
    sheets.finish()
    manifest = {'version': 1, 'sprites': sprites}
    # The manifest is the publication marker and is always written last.
    (output / 'library.json').write_bytes(encoded(manifest))
    result = {'sprites': len(sprites), 'animations': actions.count,
              'sheets': sheets.count, 'unique_images': len(pages.unique),
              'files': 0, 'bytes': 0}
    for path in output.rglob('*'):
        if path.is_file():
            result['files'] += 1
            result['bytes'] += path.stat().st_size
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compile_sprites(args.source, args.output)), flush=True)


if __name__ == '__main__':
    main()
