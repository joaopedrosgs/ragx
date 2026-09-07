"""Export all GRF sound effects as PCM WAV, retaining qualified names."""
from __future__ import annotations
import io
import json
import shutil
import subprocess
import sys
import tempfile
import wave
import zlib
from pathlib import Path, PurePosixPath
from ..client import open_stack

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


def export(grf, out: Path) -> dict:
    dest = out / "audio" / "sfx"
    dest.mkdir(parents=True, exist_ok=True)
    exported = {}
    aliases = {}
    failures = []
    recovered = []
    for name in sorted(grf.namelist()):
        key = name.replace(chr(92), "/").lower()
        if not key.startswith("data/wav/") or not key.endswith(".wav"):
            continue
        rel = key[len("data/wav/"):]
        parts = PurePosixPath(rel).parts
        if not parts or any(p in ("..", ".") or ":" in p for p in parts):
            failures.append({"source": name, "error": "unsafe archive path"})
            continue
        try:
            blob, repaired = normalize_wav(grf.read(name))
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            exported[rel] = name
            aliases.setdefault(parts[-1], []).append(rel)
            if repaired:
                recovered.append(rel)
        except Exception as exc:
            failures.append({"source": name, "error": str(exc)})
    # Preserve every qualified WAV; bare script names prefer the root resource,
    # otherwise a deterministic alias. Collisions remain visible in the report.
    for base, paths in aliases.items():
        if base not in exported:
            (dest / base).write_bytes((dest / paths[0]).read_bytes())
    report = {"exported": len(exported), "sources": exported,
              "collisions": {k: v for k, v in aliases.items() if len(v) > 1},
              "recovered": recovered, "failures": failures}
    (dest.parent / "sfx-export.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run(args) -> int:
    with open_stack(args.client) as grf:
        report = export(grf, Path(args.out))
    print(json.dumps({"exported": report["exported"],
                      "collisions": len(report["collisions"]),
                      "recovered": report["recovered"],
                      "failures": report["failures"]}, ensure_ascii=True))
    return 1 if report["failures"] else 0
