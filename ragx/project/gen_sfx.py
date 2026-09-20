#!/usr/bin/env python3
"""gen_sfx.py — export the client's sound effects.

`soundeffect "foo.wav",0` names a file in the client's data, so the wavs have to
be on disk under the name the script uses. They live inside the GRF, unlike the
BGM, which is loose in the install:

    data/wav/*.wav            ~2050 general effects
    data/wav/effect/*.wav     ~625 skill and effect sounds

Both are flattened into `audio/sfx/`, because a script names a bare filename and
does not say which folder it came from. A name in two folders keeps the first
seen and reports the clash rather than letting one silently shadow the other.

Audio lands in the gitignored `audio/` tree. Normalize legacy WAV codecs to
PCM before the Godot import step; Godot cannot import MS/IMA ADPCM WAV sources.

Usage:
    python tools/gen_sfx.py --client C:/Gravity/Ragnarok --out .
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import tempfile
import wave
import zlib
from pathlib import Path

from ragx.grf import GrfStack

BS = chr(92)
ROOTS = ["data" + BS + "wav" + BS]


def normalize_wav(blob: bytes, ffmpeg: str | None = None) -> tuple[bytes, bool]:
    """Return importable PCM WAV bytes and whether damaged source was recovered.

    Some client sounds contain a truncated zlib-wrapped WAV. Keep the decodable
    samples, then let FFmpeg rebuild the RIFF/data lengths; never pad lost audio.
    """
    recovered = False
    if not blob.startswith(b"RIFF"):
        decoder = zlib.decompressobj()
        blob = decoder.decompress(blob, 32 * 1024 * 1024)
        recovered = not decoder.eof
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError("source is not a WAV")
    if not recovered:
        try:
            with wave.open(io.BytesIO(blob)) as wav:
                if wav.getcomptype() == "NONE" and wav.getsampwidth() in (1, 2):
                    expected = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                    samples = wav.readframes(wav.getnframes())
                    if len(samples) == expected:
                        # Legacy LIST/INFO text can be CP949, which Godot reads
                        # as UTF-8. Keep samples/rate/channels, drop editor tags.
                        output = io.BytesIO()
                        with wave.open(output, "wb") as clean:
                            clean.setparams(wav.getparams())
                            clean.writeframes(samples)
                        return output.getvalue(), False
        except (wave.Error, EOFError):
            pass
    executable = ffmpeg or shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("FFmpeg is required to convert legacy WAV codecs to PCM")
    # A seekable output lets FFmpeg finalize RIFF lengths, preserving the source
    # rate and channel count instead of resampling every effect to one format.
    with tempfile.TemporaryDirectory(prefix="ro-wav-") as temporary:
        output = Path(temporary) / "normalized.wav"
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-acodec", "pcm_s16le", str(output)],
            input=blob, capture_output=True, timeout=30, check=False)
        if result.returncode or not output.exists():
            raise ValueError("WAV decoding failed: " + result.stderr.decode(errors="replace"))
        normalized = output.read_bytes()
        with wave.open(io.BytesIO(normalized)) as wav:
            if wav.getnframes() == 0:
                raise ValueError("WAV has no decodable samples")
        return normalized, recovered


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    ap.add_argument("--out", default=".", help="the Godot project root")
    args = ap.parse_args()

    client = Path(args.client)
    grf = GrfStack([str(client / "data.grf"), str(client / "event.grf")])

    dest = Path(args.out) / "audio" / "sfx"
    dest.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}
    clashes = 0
    total = 0
    failures: list[str] = []
    recovered: list[str] = []
    for name in grf.namelist():
        lowered = name.lower()
        if not lowered.endswith(".wav"):
            continue
        if not any(lowered.startswith(root.lower()) for root in ROOTS):
            continue
        base = name.rsplit(BS, 1)[-1].lower()
        if base in written:
            clashes += 1
            continue
        try:
            blob = grf.read(name)
            blob, repaired = normalize_wav(blob)
        except Exception as exc:                     # noqa: BLE001
            failures.append(f"{base}: {exc}")
            continue
        if repaired:
            recovered.append(base)
        (dest / base).write_bytes(blob)
        written[base] = name
        total += len(blob)

    print("gen_sfx: %d effects, %.0f MB -> %s" % (len(written), total / 1e6, dest))
    if clashes:
        print("  %d names appeared in more than one folder; the first won" % clashes)
    if recovered:
        print("  recovered available samples from truncated sources: " + ", ".join(recovered))
    for failure in failures:
        print("  ERROR: " + failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
