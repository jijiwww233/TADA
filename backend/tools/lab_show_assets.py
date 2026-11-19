"""Export the user's hand-picked showcase images for /lab (round 3).

Source: a folder the user copies in (default ./data
The 虚实.jpg poster (the tomato) is a style reference only and must never be used as a card (round 3), so it is
no longer exported. Images with a face are flagged (`face: true`): the page shows at most one of them at a time.
Output (gitignored): frontend/public/lab-assets/show/NN.jpg, 3:4, 480x640, plus show.json with a palette.
No network, no model calls.

  python tools/lab_show_assets.py [--src DIR]
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'frontend' / 'public' / 'lab-assets' / 'show'
W, H = 480, 640  # 3:4

# Where the subject sits (0 = top/left, 0.5 = centre, 1 = bottom/right), only for images that need it.
FOCUS_Y = {'1b6831e451405be56acfe6f631558cf6': 0.3, '8c5043d322de2b42d166948ae9d3d07e': 0.5}
# Posters with type at both the top and the bottom: fit the whole poster and extend the paper colour instead of cropping.
PAD = {'36f5a899dd39b3faf13b90f785f3d2ce', '511722e2166eb88869172e2f3a3d8446', 'fe9364cd7a649096c408f23e521b7ee7'}
# Portraits: at most one on screen at a time, never the focus of the first screen, never the middle of the wall.
FACES = {'1b6831e451405be56acfe6f631558cf6'}  # Proust


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_oklch(r: int, g: int, b: int) -> tuple[float, float, float]:
    r_, g_, b_ = _lin(r / 255), _lin(g / 255), _lin(b / 255)
    l = (0.4122214708 * r_ + 0.5363325363 * g_ + 0.0514459929 * b_) ** (1 / 3)
    m = (0.2119034982 * r_ + 0.6806995451 * g_ + 0.1073969566 * b_) ** (1 / 3)
    s = (0.0883024619 * r_ + 0.2817188376 * g_ + 0.6299787005 * b_) ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(A, B), math.degrees(math.atan2(B, A)) % 360


def crop_3_4(im: Image.Image, fy: float) -> Image.Image:
    w, h = im.size
    if w / h > W / H:  # too wide: trim the sides evenly
        nw = round(h * W / H)
        x = (w - nw) // 2
        im = im.crop((x, 0, x + nw, h))
    else:  # too tall: trim top/bottom around the subject
        nh = round(w * H / W)
        y = round((h - nh) * fy)
        im = im.crop((0, y, w, y + nh))
    return im.resize((W, H), Image.LANCZOS)


def fit_pad(im: Image.Image) -> Image.Image:
    w, h = im.size
    scale = min(W / w, H / h)
    im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    # Paper colour = average of a thin border strip on the side that needs filling.
    strip = (0, 0, 4, im.height) if im.width < W else (0, 0, im.width, 4)
    edge = im.crop(strip).resize((1, 1), Image.BOX).getpixel((0, 0))
    canvas = Image.new('RGB', (W, H), edge)
    canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    return canvas


def palette(im: Image.Image, n: int = 5) -> list[dict]:
    small = im.resize((96, 128)).quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = small.getpalette()
    counts = sorted(small.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    out = []
    for count, idx in counts:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        L, C, h = rgb_to_oklch(r, g, b)
        out.append({'hex': f'#{r:02x}{g:02x}{b:02x}', 'ratio': round(count / total, 3), 'L': round(L, 3), 'C': round(C, 3), 'h': round(h, 1)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    args = ap.parse_args()
    sources = [(p.stem, p) for p in sorted(Path(args.src).iterdir()) if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}]

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    items = []
    for i, (stem, path) in enumerate(sources):
        with Image.open(path) as im:
            im = im.convert('RGB')
            im = fit_pad(im) if stem in PAD else crop_3_4(im, FOCUS_Y.get(stem, 0.5))
            name = f'{i:02d}.jpg'
            im.save(OUT / name, quality=86, optimize=True, progressive=True)
            items.append({'id': i, 'src': f'/lab-assets/show/{name}', 'w': W, 'h': H, 'source': path.name, 'face': stem in FACES, 'palette': palette(im)})
    (OUT / 'show.json').write_text(json.dumps({'items': items}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{len(items)} images -> {OUT}')


if __name__ == '__main__':
    main()
