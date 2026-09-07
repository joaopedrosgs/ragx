"""Build shared sheets once, then independent ACT metadata, with source tracking."""
from __future__ import annotations

from pathlib import Path

from .incremental import BuildCache, TrackedSource, save_png, write_json


def export_sprite(reader, act_name: str, spr_name: str, out_root: Path) -> str:
    from PIL import Image
    import numpy as np
    from .commands.sprites_cmd import act_to_meta, decode_spr_frames, pack_sheet, _rel
    from .formats import act, spr

    if not isinstance(reader, TrackedSource):
        if not hasattr(reader, '_ragx_tracked'):
            reader._ragx_tracked = TrackedSource(reader)
        reader = reader._ragx_tracked
    if not hasattr(reader, '_sprite_caches'):
        reader._sprite_caches = {}
    root = Path(out_root).resolve()
    if root not in reader._sprite_caches:
        reader._sprite_caches[root] = BuildCache(root, reader)
    cache = reader._sprite_caches[root]

    def sheet():
        parsed = spr.parse(reader.read(spr_name))
        frames = decode_spr_frames(parsed)
        if not frames:
            return None
        pixels, rects = pack_sheet(frames)
        relative = _rel(spr_name)
        save_png(root / (relative + '.png'), Image.fromarray(pixels))
        recolorable = any(part in spr_name for part in ('\\머리통\\', '\\몸통\\'))
        if parsed.indexed_frames and recolorable:
            indices = np.zeros(pixels.shape[:2], dtype=np.uint8)
            for frame, (x, y, w, h) in zip(parsed.indexed_frames, rects):
                if w and h:
                    indices[y:y+h, x:x+w] = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(h, w)
            save_png(root / (relative + '.idx.png'), Image.fromarray(indices))
        return {'w': pixels.shape[1], 'h': pixels.shape[0], 'frames': rects,
                'indexed_count': len(parsed.indexed_frames), 'sheet': relative + '.png'}

    def animation():
        metadata = cache.run('sheet:' + spr_name, {}, sheet)
        if metadata is None:
            return 'empty'
        parsed = act.parse(reader.read(act_name))
        result = dict(metadata)
        result.update(act_to_meta(parsed, len(result['frames']), result['indexed_count']))
        write_json(root / (_rel(act_name) + '.json'), result)
        return 'ok'

    builds = cache.builds
    status = cache.run('animation:' + act_name, {'spr': spr_name}, animation)
    return 'cached' if cache.builds == builds else status
