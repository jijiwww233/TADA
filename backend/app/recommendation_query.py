"""Effective semantic query for a (previous, current) brief pair. Pure; no I/O.

The recommendation core keeps the full raw text for its own decision. The single
upstream semantic search instead gets the context that is still valid: negated
chunks are dropped, and vocabulary spans whose term the parsed result no longer
wants (negated or replaced now) are removed. Everything else stays verbatim, e.g.
previous '复古建筑展板' + current '不要复古，改现代极简' -> '建筑展板，现代极简'.
Uses the core's own clause/negation regexes and vocabulary, so both agree.
"""
from __future__ import annotations

import re

from .recommendation import (ARTIFACTS, COLORS, LAYOUT, STYLES, TOPICS, _NEGATIVE, _TRANSITION,
                             Brief, parse_brief)


class EmptyEffectiveQuery(ValueError):
    """Only exclusions remain; never fall back to text with negated terms."""


_CATEGORIES = (("topics", TOPICS), ("layout", LAYOUT), ("styles", STYLES), ("colors", COLORS), ("artifacts", ARTIFACTS))
_ALIASES = {alias: (name, key) for name, vocab in _CATEGORIES for key, terms in vocab.items() for alias in terms}
_SPANS = re.compile("|".join(re.escape(a) for a in sorted(_ALIASES, key=lambda a: (-len(a), a))), re.I)
_ALIAS_KEY = {alias.lower(): value for alias, value in _ALIASES.items()}
# Edit verbs left at the start of a clause after the core's transition split.
_LEADING = re.compile(r"^(?:改成|改为|换成|换为|但要|而要|也要|还要|加上|而是|改|换|再|并且|而且|另外|同时|和|与|的|、|\s)+")
_TRAILING = re.compile(r"(?:的|和|与|、|\s|[，,。；;！!？?])+$")


def _expand(text: str) -> str:
    # Same compound-color expansion as parse_brief, so spans line up with its terms.
    return text.replace("红蓝", "红色蓝色").replace("蓝白", "蓝色白色").replace("红黑", "红色黑色")


def _positive_fragments(text: str, wanted: Brief) -> list[str]:
    fragments = []
    for clause in _TRANSITION.split(_expand(text)):
        negative = False
        for chunk in _NEGATIVE.split(clause or ""):
            if _NEGATIVE.fullmatch(chunk):
                negative = True
                continue
            if negative or not chunk:
                continue

            def keep(match):
                name, key = _ALIAS_KEY[match.group().lower()]
                return match.group() if key in getattr(wanted, name) else ""

            fragment = _TRAILING.sub("", _LEADING.sub("", _SPANS.sub(keep, chunk)))
            if fragment:
                fragments.append(fragment)
    return fragments


def resolve_brief(current: str, previous: str | None = None) -> tuple[Brief, str]:
    """Return (final parsed Brief for the core, effective text for semantic search)."""
    if not isinstance(current, str) or not current.strip():
        raise ValueError("brief must not be blank")
    previous = previous if isinstance(previous, str) and previous.strip() else None
    final = parse_brief(current, previous=parse_brief(previous)) if previous else parse_brief(current)
    fragments = []
    for text in (previous, current):
        if text:
            fragments.extend(_positive_fragments(text, final))
    effective = "，".join(dict.fromkeys(fragments))
    if not effective:
        raise EmptyEffectiveQuery("请描述想要的参考，而不只是排除条件")
    return final, effective
