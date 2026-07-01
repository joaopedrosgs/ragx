"""Assemble transparent orbit GIFs + downscaled transparent still PNGs."""
import glob
import os
import sys

from PIL import Image

SHOTS = sys.argv[1]
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)


def build_gif(map_name, width=640, step=1, duration=70):
    paths = sorted(glob.glob(os.path.join(SHOTS, "orbit", f"{map_name}_*.png")))[::step]
    frames = []
    for p in paths:
        im = Image.open(p).convert("RGBA")
        h = round(im.height * width / im.width)
        im = im.resize((width, h), Image.LANCZOS)
        alpha = im.getchannel("A")
        # Quantize RGB to 255 colors; reserve palette index 255 for transparency.
        pal = im.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT, dither=Image.NONE)
        transparent = alpha.point(lambda a: 255 if a < 128 else 0)  # L mask
        pal.paste(255, (0, 0), transparent)
        pal.info["transparency"] = 255
        frames.append(pal)
    out = os.path.join(OUT, f"{map_name}_orbit.gif")
    frames[0].save(out, save_all=True, append_images=frames[1:], loop=0,
                   duration=duration, disposal=2, transparency=255, optimize=False)
    return out, os.path.getsize(out), len(frames)


def build_still(src, dst_name, width=1500):
    im = Image.open(src).convert("RGBA")
    h = round(im.height * width / im.width)
    im = im.resize((width, h), Image.LANCZOS)
    out = os.path.join(OUT, dst_name)
    im.save(out, "PNG", optimize=True)
    return out, os.path.getsize(out)


for m in ("prontera", "geffen", "payon"):
    out, size, n = build_gif(m, width=640, step=1, duration=70)
    print(f"GIF  {os.path.basename(out):22s} {size/1e6:5.2f} MB  ({n} frames)")

for src, dst in (("prontera_persp", "prontera.png"), ("prontera_lit", "prontera_lit.png"),
                 ("geffen_lit", "geffen.png"), ("payon_persp", "payon.png")):
    out, size = build_still(os.path.join(SHOTS, f"{src}.png"), dst)
    print(f"PNG  {dst:22s} {size/1e6:5.2f} MB")
