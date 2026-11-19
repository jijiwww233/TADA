"""Palette targets from decomposition; soft nearest-swatch scoring, no all-color gate."""
import math
import re
from dataclasses import dataclass

from .recommendation import COLORS, parse_brief

# Representatives for the existing color vocabulary, not additional query keywords.
HEX = {'米白': '#f5f0e6', '白': '#ffffff', '黑': '#000000', '灰': '#808080',
       '红': '#ff0000', '蓝': '#0000ff', '绿': '#008000', '黄': '#ffff00',
       '橙': '#ffa500', '紫': '#800080'}


@dataclass(frozen=True)
class Target:
    hex: str
    main: bool = False


def rgb(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return None
    return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))


def targets(need, fallback=(), prominent=False):
    """Accept structured hex targets as well as the current model's color sentence.

    Named colors use the existing parser, including longest-name/negation handling.
    Each clause retains its own main/accent role. No first-color-only truncation.
    """
    result = []
    if isinstance(need, list):
        for item in need:
            if isinstance(item, dict) and rgb(item.get('hex')):
                result.append(Target(item['hex'].lower(), item.get('role') in ('main', '为主')))
    elif isinstance(need, str):
        for clause in re.split(r'[，,；;。\n]', need):
            main = bool(re.search(r'为主|主色|大面积', clause))
            result.extend(Target(h.lower(), main) for h in re.findall(r'#[0-9a-fA-F]{6}\b', clause))
            result.extend(Target(HEX[c], main) for c in parse_brief(clause).colors)
    if not result:
        result = [Target(HEX[c], prominent) for c in fallback if c in HEX]
    # Repeated colors do not get counted twice. A main requirement wins over accent.
    merged = {}
    for item in result:
        merged[item.hex] = Target(item.hex, item.main or merged.get(item.hex, item).main)
    return tuple(merged.values())


def score(swatches, desired):
    valid = [(rgb(s.get('hex')), s.get('ratio')) for s in swatches]
    valid = [(c, float(r)) for c, r in valid if c and isinstance(r, (int, float))
             and not isinstance(r, bool) and math.isfinite(r) and 0 < r <= 1]
    total = 0.0
    for target in desired:
        wanted = rgb(target.hex)
        if not valid or wanted is None:
            continue
        # Find the closest color first, rather than picking a more abundant wrong color.
        color, share = min(valid, key=lambda x: (sum((a-b)**2 for a, b in zip(wanted, x[0])), -x[1]))
        similarity = max(0.0, 1 - math.dist(wanted, color) / math.sqrt(3 * 255**2))
        # Main colors penalize small shares smoothly; no missing-color veto/threshold.
        total += similarity * share * (share if target.main else 1)
    return total
