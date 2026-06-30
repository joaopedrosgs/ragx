"""``ragx sprites`` — export SPR/ACT sprite pairs to spritesheet PNG + JSON.

For each ``data/sprite/**/<name>.act`` + ``.spr`` pair in the client:

  <out>/sprites/<rel path>.png    all SPR frames shelf-packed into one sheet
  <out>/sprites/<rel path>.json   frame rects + the full ACT animation data

The JSON layout:

  {
    "w", "h":        sheet size in pixels
    "frames":        [[x, y, w, h], ...] sheet rect per SPR frame; the palette
                     pool comes first, then the RGBA pool (an ACT layer of
                     sprite_type 1 indexes at indexed_count + sprite_index)
    "indexed_count": size of the palette pool
    "sheet":         the .png this json points at (sprites can share a sheet)
    "actions":       [{"delay": float (25 ms units), "frames": [
                        {"layers": [[x, y, frame, mirror] short form or
                                    [x, y, frame, mirror, r, g, b, a,
                                     sx, sy, angle] full form, ...],
                         "anchor": [x, y] | null,   first attach point
                         "event": int}, ...]}, ...]
    "events":        ["sound/file.wav" | "atk", ...]
  }
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from .. import client as client_mod

SPRITE_PREFIX = "data\\sprite\\"
MAX_SHEET_WIDTH = 2048
PADDING = 1

_DEFAULT_COLOR = (255, 255, 255, 255)


def decode_spr_frames(spr) -> list[np.ndarray]:
    """All SPR frames as top-down RGBA arrays (palette pool then RGBA pool)."""
    frames = []
    if spr.indexed_frames:
        lut = np.zeros((256, 4), dtype=np.uint8)
        if spr.palette is not None:
            palette = np.frombuffer(spr.palette, dtype=np.uint8).reshape(256, 4)
            lut[:, :3] = palette[:, :3]
        lut[1:, 3] = 255  # palette index 0 is the transparency key
        for frame in spr.indexed_frames:
            indices = np.frombuffer(frame.pixels, dtype=np.uint8)
            frames.append(lut[indices].reshape(frame.height, frame.width, 4))
    for frame in spr.rgba_frames:
        raw = np.frombuffer(frame.pixels, dtype=np.uint8)
        raw = raw.reshape(frame.height, frame.width, 4)
        # stored bottom-up in A,B,G,R order
        frames.append(raw[::-1, :, [3, 2, 1, 0]])
    return frames


def pack_sheet(frames: list[np.ndarray]) -> tuple[np.ndarray, list[list[int]]]:
    """Shelf-pack frames into one RGBA sheet; returns (sheet, rects)."""
    order = sorted(range(len(frames)), key=lambda i: -frames[i].shape[0])
    max_frame_width = max((f.shape[1] for f in frames), default=0)
    total_area = sum(f.shape[0] * f.shape[1] for f in frames)
    width = min(MAX_SHEET_WIDTH,
                max(max_frame_width + 2 * PADDING,
                    int(np.sqrt(total_area) * 1.2) + 2 * PADDING))

    rects: list[list[int]] = [[0, 0, 0, 0] for _ in frames]
    x = y = shelf_height = 0
    for index in order:
        frame = frames[index]
        h, w = frame.shape[0], frame.shape[1]
        if w == 0 or h == 0:
            continue
        if x + w + PADDING > width:
            x = 0
            y += shelf_height
            shelf_height = 0
        rects[index] = [x + PADDING, y + PADDING, w, h]
        x += w + 2 * PADDING
        shelf_height = max(shelf_height, h + 2 * PADDING)
    height = y + shelf_height

    sheet = np.zeros((max(height, 1), width, 4), dtype=np.uint8)
    for index, frame in enumerate(frames):
        rx, ry, w, h = rects[index]
        if w and h:
            sheet[ry: ry + h, rx: rx + w] = frame
    return sheet, rects


def act_to_meta(act, frame_count: int, indexed_count: int) -> dict:
    """Convert a parsed Act to the compact JSON structure."""
    actions = []
    for action in act.actions:
        frames = []
        for frame in action.frames:
            layers = []
            for layer in frame.layers:
                if layer.sprite_index < 0:
                    continue
                ref = layer.sprite_index
                if layer.sprite_type == 1:
                    ref += indexed_count
                elif layer.sprite_type != 0:
                    continue
                if not 0 <= ref < frame_count:
                    continue  # a few official files reference missing frames
                entry = [layer.x, layer.y, ref, 1 if layer.mirror else 0]
                if (layer.color != _DEFAULT_COLOR or layer.scale_x != 1.0
                        or layer.scale_y != 1.0 or layer.angle != 0.0):
                    entry += [*layer.color,
                              round(layer.scale_x, 4), round(layer.scale_y, 4),
                              layer.angle]
                layers.append(entry)
            anchor = None
            if frame.attach_points:
                anchor = [frame.attach_points[0].x, frame.attach_points[0].y]
            entry = {"layers": layers}
            if anchor is not None:
                entry["anchor"] = anchor
            if frame.event_id >= 0:
                entry["event"] = frame.event_id
            frames.append(entry)
        actions.append({"delay": round(action.delay, 3), "frames": frames})
    return {"actions": actions, "events": act.events}


def _rel(grf_name: str) -> str:
    return grf_name[len(SPRITE_PREFIX):-4].replace("\\", "/")


def export_one(grf, act_name: str, spr_name: str, out_root: Path) -> str:
    """Convert one .act + .spr pair. The .spr may be shared by several
    .act files (robes ship one sheet per robe and an .act per job); the
    sheet PNG is then written once at the .spr's own path and every
    .json points at it via the "sheet" field."""
    from PIL import Image

    from ..formats import act as act_format
    from ..formats import spr as spr_format

    parsed_spr = spr_format.parse(grf.read(spr_name))
    parsed_act = act_format.parse(grf.read(act_name))

    frames = decode_spr_frames(parsed_spr)
    if not frames:
        return "empty"
    sheet, rects = pack_sheet(frames)

    sheet_rel = _rel(spr_name)
    meta = act_to_meta(parsed_act, len(frames), len(parsed_spr.indexed_frames))
    meta["w"] = int(sheet.shape[1])
    meta["h"] = int(sheet.shape[0])
    meta["frames"] = rects
    meta["indexed_count"] = len(parsed_spr.indexed_frames)
    meta["sheet"] = sheet_rel + ".png"

    png_path = out_root / (sheet_rel + ".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not png_path.exists():  # shared sheets: first writer wins
        tmp_path = png_path.with_suffix(f".{os.getpid()}.tmp")
        Image.fromarray(sheet, "RGBA").save(tmp_path, format="PNG")
        try:
            os.replace(tmp_path, png_path)
        except OSError:
            tmp_path.unlink(missing_ok=True)

    json_path = out_root / (_rel(act_name) + ".json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return "ok"


def _worker(job: tuple[str, list[tuple[str, str]], str]) -> list[tuple[str, str]]:
    client_dir, pairs, out_root = job

    grf = globals().get("_WORKER_GRF")
    if grf is None or getattr(grf, "_client_dir", None) != client_dir:
        grf = client_mod.open_archive(client_dir)
        grf._client_dir = client_dir
        globals()["_WORKER_GRF"] = grf

    results = []
    for act_name, spr_name in pairs:
        try:
            results.append((act_name, export_one(grf, act_name, spr_name, Path(out_root))))
        except Exception:  # noqa: BLE001
            results.append((act_name, "FAILED\n" + traceback.format_exc()))
    return results


def resolve_spr(act_name: str, spr_set: set[str]) -> str | None:
    """Find the .spr for an .act: same base name, or the robe folder's
    shared sheet (data/sprite/로브/<robe>/<robe>.spr)."""
    base = act_name[:-4]
    if base + ".spr" in spr_set:
        return base + ".spr"
    parts = act_name.split("\\")
    if len(parts) >= 5 and parts[2] == "로브":
        candidate = "\\".join(parts[:4]) + "\\" + parts[3] + ".spr"
        if candidate in spr_set:
            return candidate
    return None


def run(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    archive = client_mod.open_archive(args.client)
    names = archive.namelist()
    spr_set = {n for n in names if n.endswith(".spr")}
    if args.all:
        acts = sorted(n for n in names
                      if n.startswith(SPRITE_PREFIX) and n.endswith(".act"))
    elif args.sprites:
        acts = [SPRITE_PREFIX + s.replace("/", "\\").lower() + ".act"
                for s in args.sprites]
        for act in acts:
            archive.read(act)  # fail fast on typos
    else:
        archive.close()
        sys.exit("no sprites given (pass sprite paths or --all)")
    archive.close()

    targets = []
    no_spr = 0
    for act in acts:
        spr = resolve_spr(act, spr_set)
        if spr is None:
            no_spr += 1
        else:
            targets.append((act, spr))
    if no_spr:
        print(f"{no_spr} .act files have no resolvable .spr, skipped")
    if not targets:
        sys.exit("no exportable sprite pairs found")

    out_root = str(Path(args.out) / "sprites")
    print(f"exporting {len(targets)} sprite(s) -> {out_root}", flush=True)
    t0 = time.time()
    counts: dict[str, int] = {}
    failures: list[tuple[str, str]] = []

    batch_size = 100
    batches = [targets[i: i + batch_size] for i in range(0, len(targets), batch_size)]
    total_batches = len(batches)

    def consume(batch_results: list[tuple[str, str]], done: int) -> None:
        for name, status in batch_results:
            key = status.split("\n", 1)[0]
            counts[key] = counts.get(key, 0) + 1
            if status.startswith("FAILED"):
                failures.append((name, status))
        if done % 50 == 0 or done == total_batches:
            print(f"[{done}/{total_batches} batches] {counts} {time.time()-t0:.0f}s",
                  flush=True)

    if args.processes > 1:
        import multiprocessing as mp
        jobs = [(args.client, batch, out_root) for batch in batches]
        with mp.Pool(args.processes) as pool:
            for done, batch_results in enumerate(
                    pool.imap_unordered(_worker, jobs, chunksize=1), 1):
                consume(batch_results, done)
    else:
        for done, batch in enumerate(batches, 1):
            consume(_worker((args.client, batch, out_root)), done)

    print(f"\ndone in {time.time()-t0:.0f}s: {counts}")
    for name, status in failures[:10]:
        print(f"\n{name}:\n{status[:500]}")
    return 1 if failures else 0
