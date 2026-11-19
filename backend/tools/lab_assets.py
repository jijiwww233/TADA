"""Export visual_ref thumbnails + palette for the /lab sample page.

Read-only on the source library (sqlite opened with mode=ro, images only read).
Output goes to frontend/public/lab-assets/ (gitignored): resized JPEGs + manifest.json.
No network, no model calls.

  python tools/lab_assets.py --data ./data
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[2] / 'frontend' / 'public' / 'lab-assets'
FALLBACK = ['#CFE3FF', '#CFF5EA', '#FAD9E6', '#E6DDFB']


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_oklch(hex_: str) -> tuple[float, float, float]:
    r, g, b = (_lin(int(hex_[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(A, B), math.degrees(math.atan2(B, A)) % 360


def score(colors: list[tuple[str, float]]) -> float:
    """Prefer light, gently colourful images; penalise dark-dominant ones."""
    total = 0.0
    for hex_, ratio in colors:
        L, C, _ = hex_to_oklch(hex_)
        if L < 0.3:
            total -= ratio
        elif C > 0.02:  # near-white/grey backgrounds neither help nor hurt
            total += ratio * min(C, 0.15) * 8
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--count', type=int, default=30)
    args = ap.parse_args()
    data = Path(args.data)
    conn = sqlite3.connect(f'file:{(data / "app.db").as_posix()}?mode=ro', uri=True)
    rows = conn.execute("""
        SELECT i.id, i.file_path, i.width, i.height FROM images i
        JOIN image_reference_labels r ON r.image_id = i.id
        LEFT JOIN analyses a ON a.image_id = i.id AND a.status = 'done'
        WHERE coalesce(r.manual_category, r.auto_category) = 'visual_ref'
          -- skip screenshots / text-heavy cards when the analysis says so
          AND coalesce(json_extract(a.style_json, '$.medium'), '') NOT IN ('ui', 'typography')
          AND length(coalesce(a.visible_text, '')) <= 40""").fetchall()
    palette = {}
    for image_id, hex_, ratio in conn.execute('SELECT image_id, hex, ratio FROM colors ORDER BY image_id, rank'):
        palette.setdefault(image_id, []).append((hex_, ratio))
    ranked = sorted(rows, key=lambda r: -score(palette.get(r[0], [])))[:args.count]

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    items = []
    for image_id, file_path, width, height in ranked:
        with Image.open(data / file_path) as im:
            im = im.convert('RGB')
            im.thumbnail((720, 720))
            name = f'{image_id}.jpg'
            im.save(OUT / name, quality=82)
            w, h = im.size
        colors = palette.get(image_id, [])
        # Memo rule: keep only the hue; lightness/chroma unified in OKLCH.
        hues = [round(hex_to_oklch(c)[2]) for c, _ in colors if hex_to_oklch(c)[1] > 0.03][:3]
        items.append({'id': image_id, 'src': f'/lab-assets/{name}', 'w': w, 'h': h,
                      'colors': [c for c, _ in colors[:5]], 'hues': hues})
    total = conn.execute('SELECT count(*) FROM images').fetchone()[0]
    visual = conn.execute("""SELECT count(*) FROM image_reference_labels
        WHERE coalesce(manual_category, auto_category) = 'visual_ref'""").fetchone()[0]
    (OUT / 'manifest.json').write_text(json.dumps({'total': total, 'visual_ref': visual, 'items': items, 'fallback': FALLBACK}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{len(rows)} visual_ref, exported {len(items)} -> {OUT}')


if __name__ == '__main__':
    main()
