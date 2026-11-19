"""Dominant colors with Pillow. Local and deterministic; the AI never judges colors."""
import sqlite3
from pathlib import Path

from PIL import Image

TOP_N = 5
PALETTE = 8          # quantize to this many first, then merge near-duplicates
MERGE_DIST = 28      # RGB euclidean distance under which two palette colors count as one
SAMPLE_SIDE = 200


def extract(path: Path, n: int = TOP_N) -> list[tuple[str, float]]:
    """[(#rrggbb, ratio of the whole image)], most dominant first. Ratios of the top n
    need not add up to 1. Transparent areas are flattened onto white."""
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((SAMPLE_SIDE, SAMPLE_SIDE))
        q = im.quantize(colors=PALETTE, method=Image.Quantize.MEDIANCUT,
                        dither=Image.Dither.NONE)
    palette = q.getpalette()
    total = q.width * q.height
    found = sorted(q.getcolors(), reverse=True)  # [(count, index)]

    merged: list[list] = []  # [[rgb, count]]
    for count, idx in found:
        rgb = tuple(palette[idx * 3: idx * 3 + 3])
        for m in merged:
            if sum((a - b) ** 2 for a, b in zip(m[0], rgb)) < MERGE_DIST ** 2:
                m[1] += count
                break
        else:
            merged.append([rgb, count])
    merged.sort(key=lambda m: m[1], reverse=True)
    return [("#%02x%02x%02x" % m[0], round(m[1] / total, 4)) for m in merged[:n]]


def ensure_colors(conn: sqlite3.Connection, image_id: int, path: Path) -> bool:
    """Extract once. An image that already has colors is left untouched.
    Returns True if rows were written."""
    if conn.execute("SELECT 1 FROM colors WHERE image_id = ? LIMIT 1", (image_id,)).fetchone():
        return False
    rows = extract(path)
    conn.executemany(
        "INSERT OR IGNORE INTO colors (image_id, rank, hex, ratio) VALUES (?, ?, ?, ?)",
        [(image_id, rank, hex_, ratio) for rank, (hex_, ratio) in enumerate(rows)],
    )
    return True
