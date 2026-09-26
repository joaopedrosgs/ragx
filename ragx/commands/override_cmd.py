"""``ragx override`` — extract ONE sprite's visual into editable open files.

For an authored "Override": the original visual copied out of the GRF so an
artist can change it with ordinary tools, while everything else in the client
keeps reading the archive. Only the selected sprite's dependency set is written
— never a whole category — into a new folder:

    <dest>/frames/000.png ...   every SPR frame as top-down RGBA (palette pool
                                first, then the RGBA pool; index 0 transparent)
    <dest>/sounds/<name>.wav    the WAVs the ACT's frame events play
    <dest>/animation.json       the ACT, in the same schema ``ragx sprites``
                                writes (``act_to_meta``), plus the frame files
    <dest>/provenance.json      source entries, their fingerprints, ragx version

The folder is staged beside the destination and renamed into place only after
everything was written, so an interrupted extraction never leaves a partial
override. An existing destination is never overwritten: authored edits live
there.

    ragx override 몬스터/poring --dest content/mob/1002
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path

from .. import __version__
from .. import client as client_mod
from ..formats import act, spr
from ..grf import normalize_path
from .sprites_cmd import act_to_meta, decode_spr_frames, resolve_spr

SPRITE_PREFIX = "data\\sprite\\"
WAV_PREFIX = "data\\wav\\"
FORMAT = "ragx-override-1"


def extract(reader, sprite: str, dest: Path) -> dict:
    """Write the override folder for ``sprite`` (path under data\\sprite\\,
    without extension) to ``dest``. ``reader`` has ``read(path)`` and
    ``__contains__``; ``fingerprint(path)`` is used when available."""
    from PIL import Image

    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} already has authored files; refusing to overwrite")
    base = normalize_path(SPRITE_PREFIX + sprite.replace("/", "\\"))
    act_name = base + ".act"
    if act_name not in reader:
        raise FileNotFoundError(act_name)
    spr_name = base + ".spr"
    if spr_name not in reader:
        names = set(reader.namelist()) if hasattr(reader, "namelist") else set()
        spr_name = resolve_spr(act_name, names) or spr_name
    parsed_spr = spr.parse(reader.read(spr_name))
    parsed_act = act.parse(reader.read(act_name))
    frames = decode_spr_frames(parsed_spr)
    animation = act_to_meta(parsed_act, len(frames), len(parsed_spr.indexed_frames))

    stage = dest.with_name(dest.name + ".staging-%d" % os.getpid())
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "frames").mkdir(parents=True)
    try:
        frame_files = []
        for index, pixels in enumerate(frames):
            name = "frames/%03d.png" % index
            Image.fromarray(pixels, "RGBA").save(stage / name)
            frame_files.append(name)
        sources = {act_name: None, spr_name: None}
        sounds, missing = {}, []
        for event in animation["events"]:
            if not event.lower().endswith(".wav") or event in sounds:
                continue
            wav = normalize_path(WAV_PREFIX + event)
            if wav not in reader:
                missing.append(event)
                continue
            (stage / "sounds").mkdir(exist_ok=True)
            local = "sounds/" + Path(event.replace("\\", "/")).name
            (stage / local).write_bytes(reader.read(wav))
            sounds[event] = local
            sources[wav] = None
        if hasattr(reader, "fingerprint"):
            for name in sources:
                sources[name] = reader.fingerprint(name)
        document = {
            "format": FORMAT,
            "sprite": sprite,
            "frames": frame_files,
            "indexed_count": len(parsed_spr.indexed_frames),
            "actions": animation["actions"],
            "events": animation["events"],
            "sounds": sounds,
        }
        (stage / "animation.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
        provenance = {
            "format": FORMAT,
            "ragx": __version__,
            "sprite": sprite,
            "sources": sources,
            "missing_sounds": missing,
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
        (stage / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=1), encoding="utf-8")
        if dest.exists():
            dest.rmdir()  # empty, checked above
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"dest": str(dest), "frames": len(frame_files), "sounds": len(sounds),
            "missing_sounds": missing}


MAGENTA = (255, 0, 255)


def extract_image(reader, entry: str, dest: Path) -> dict:
    """Write one image entry (a BMP icon/illustration, a TGA status icon) as an
    editable RGBA PNG at ``dest`` plus ``<dest>.provenance.json``. Magenta is
    keyed to transparent where the image has no alpha, as the client and the
    icon exporter do. Never overwrites."""
    import io

    from PIL import Image

    dest = Path(dest)
    sidecar = dest.with_name(dest.name + ".provenance.json")
    if dest.exists() or sidecar.exists():
        raise FileExistsError(f"{dest} already exists; refusing to overwrite")
    name = normalize_path(entry)
    if name not in reader:
        raise FileNotFoundError(name)
    image = Image.open(io.BytesIO(reader.read(name)))
    had_alpha = "A" in image.getbands()
    image = image.convert("RGBA")
    if not had_alpha:
        pixels = image.load()
        width, height = image.size
        for y in range(height):
            for x in range(width):
                if pixels[x, y][:3] == MAGENTA:
                    pixels[x, y] = (0, 0, 0, 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = dest.with_name(dest.name + ".staging-%d.png" % os.getpid())
    try:
        image.save(stage)
        provenance = {"format": FORMAT, "ragx": __version__, "entry": name,
                      "fingerprint": reader.fingerprint(name) if hasattr(reader, "fingerprint") else None,
                      "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
        sidecar.write_text(json.dumps(provenance, ensure_ascii=False, indent=1), encoding="utf-8")
        stage.rename(dest)
    except BaseException:
        stage.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    return {"dest": str(dest), "size": list(image.size)}


def run(args) -> int:
    with client_mod.open_stack(args.client) as stack:
        try:
            if args.command == "override-image":
                result = extract_image(stack, args.entry, Path(args.dest))
            else:
                result = extract(stack, args.sprite, Path(args.dest))
        except (FileExistsError, FileNotFoundError) as error:
            print("override: %s" % error, file=sys.stderr)
            return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
