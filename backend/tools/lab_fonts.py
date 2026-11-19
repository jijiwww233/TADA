"""Subset the /lab fonts (all SIL OFL 1.1) into small self-hosted WOFF2 files.

Sources are downloaded once into .tools/fonts/ (google/fonts repo, see docs/ui-intro/lab-v2/README.md).
Chinese fonts are cut down to the characters the lab source actually uses:
  - Noto Serif SC (思源宋体): only the title strings (TITLE_TEXT below) — serif is for titles only.
  - Noto Sans SC  (思源黑体): every CJK character / fullwidth punctuation found in frontend/src/lab.
Latin fonts keep Basic Latin + Latin-1 + common punctuation.
Needs fontTools + brotli (installed into .tools/py, not the project venv):

  set PYTHONPATH=.tools/py && backend/.venv/Scripts/python backend/tools/lab_fonts.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fontTools import subset
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / '.tools' / 'fonts'
OUT = ROOT / 'frontend' / 'src' / 'lab' / 'fonts'
LAB = ROOT / 'frontend' / 'src' / 'lab'
MAIN = ROOT / 'frontend' / 'src' / 'main'

# Serif is only used for titles; keep this in sync with the title strings in src/lab/demos/FontsDemo.tsx
# and the /main shell (tutorial headline 「在寻找之前，」, section titles).
TITLE_TEXT = '我明明收藏过。找到了“”在寻找之前，'
LATIN = ''.join(chr(c) for c in range(0x20, 0x7F)) + ''.join(chr(c) for c in range(0xA0, 0x100)) + '‘’“”–—…·•→←↑↓×'

FONTS = [
    # (source, output, which text, variable axes to keep: a fixed value pins the axis, a tuple limits it)
    ('notoserifsc/NotoSerifSC[wght].ttf', 'noto-serif-sc-title.woff2', 'serif-title', {'wght': (300, 700)}),  # titles only: Light for the line, Bold for 明明 (round 4)
    ('notosanssc/NotoSansSC[wght].ttf', 'noto-sans-sc-lab.woff2', 'cjk', {'wght': (400, 600)}),
    ('inter/Inter[opsz,wght].ttf', 'inter-latin.woff2', 'latin', {'wght': (400, 650), 'opsz': (14, 32)}),
    ('newsreader/Newsreader[opsz,wght].ttf', 'newsreader-latin.woff2', 'latin', {'wght': (300, 600), 'opsz': (12, 72)}),  # Light at display size (round 4)
    ('ibmplexmono/IBMPlexMono-Regular.ttf', 'ibm-plex-mono-latin.woff2', 'latin', None),
]

CJK = re.compile(r'[　-〿㐀-䶿一-鿿＀-￯‘-‟…·]')


def _cjk_of(root: Path) -> str:
    chars: set[str] = set()
    for p in root.rglob('*'):
        if p.suffix in {'.ts', '.tsx', '.css'} and 'fonts' not in p.parts:
            chars.update(CJK.findall(p.read_text(encoding='utf-8')))
    return ''.join(sorted(chars))


def lab_cjk() -> str:
    return _cjk_of(LAB)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    texts = {'serif-title': TITLE_TEXT + _cjk_of(MAIN) + ' ', 'cjk': lab_cjk() + LATIN, 'latin': LATIN}
    for src, out, kind, axes in FONTS:
        opts = subset.Options()
        opts.flavor = 'woff2'
        opts.layout_features = ['*']
        opts.name_IDs = ['*']
        opts.notdef_outline = True
        font = subset.load_font(str(SRC / src), opts, lazy=False)
        s = subset.Subsetter(opts)
        s.populate(text=texts[kind])
        s.subset(font)
        if axes:  # after subsetting: far fewer glyphs to instance
            instancer.instantiateVariableFont(font, axes, inplace=True)
        subset.save_font(font, str(OUT / out), opts)
        print(f'{out}: {(OUT / out).stat().st_size / 1024:.1f} KB ({len(texts[kind])} chars)')
    for d in sorted({f[0].split('/')[0] for f in FONTS}):
        shutil.copy(SRC / d / 'OFL.txt', OUT / f'OFL-{d}.txt')


if __name__ == '__main__':
    main()
