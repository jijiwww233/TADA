"""Pure projects/pins business rules; no persistence or recommendation side effects.

The caller supplies project/image IDs and checks their existence. Store one Pin per
(project_id, image_id); encode_purposes maps its tuple to the existing pins.purpose
TEXT column. Adapters must apply changes atomically under the existing UNIQUE key.
Recommendations are never passed to this module: pin_image requires a user-selected
purpose list. No function reads or modifies analyses.
"""
from dataclasses import dataclass, replace
import json
from collections.abc import Iterable

PURPOSES = ("layout", "style", "palette", "subject")


def _id(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _text(value, name, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{name} must be {'nonempty ' if nonempty else ''}text")
    return value.strip()


def normalize_purposes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("purposes must be a collection, not text")
    try:
        items = tuple(values)
    except TypeError:
        raise ValueError("purposes must be a collection") from None
    if any(not isinstance(p, str) or p not in PURPOSES for p in items):
        raise ValueError("unknown purpose; use layout/style/palette/subject")
    return tuple(p for p in PURPOSES if p in items)


def encode_purposes(values: Iterable[str]) -> str:
    """Canonical JSON array for pins.purpose; no schema change needed."""
    return json.dumps(normalize_purposes(values), separators=(",", ":"))


def decode_purposes(value: str | None) -> tuple[str, ...]:
    """Read JSON arrays and legacy single-purpose TEXT. Invalid values are rejected."""
    if value is None or value == "":
        return ()
    if not isinstance(value, str):
        raise ValueError("stored purpose must be text or NULL")
    value = value.strip()
    if value in PURPOSES:
        return (value,)
    try:
        decoded = json.loads(value)
    except ValueError:
        raise ValueError("invalid stored purpose JSON") from None
    if not isinstance(decoded, list):
        raise ValueError("stored purpose must be a JSON array")
    return normalize_purposes(decoded)


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    brief: str

    def __post_init__(self):
        _id(self.id, "project_id")
        object.__setattr__(self, "name", _text(self.name, "name", nonempty=True))
        object.__setattr__(self, "brief", _text(self.brief, "brief"))


@dataclass(frozen=True)
class Pin:
    project_id: int
    image_id: int
    purposes: tuple[str, ...]
    note: str = ""

    def __post_init__(self):
        _id(self.project_id, "project_id")
        _id(self.image_id, "image_id")
        values = normalize_purposes(self.purposes)
        if not values:
            raise ValueError("a pin must have at least one purpose")
        object.__setattr__(self, "purposes", values)
        object.__setattr__(self, "note", _text(self.note, "note"))


def create_project(project_id: int, name: str, brief: str = "") -> Project:
    return Project(project_id, name, brief)


def update_brief(project: Project, brief: str) -> Project:
    """Replace brief only; pins, recommendations and image analyses are untouched."""
    return replace(project, brief=brief)


def _pins(pins):
    rows = tuple(pins)
    if not all(isinstance(p, Pin) for p in rows):
        raise ValueError("pins must contain Pin values")
    keys = [(p.project_id, p.image_id) for p in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate existing pin identity")
    return rows


def pin_image(pins: Iterable[Pin], project_id: int, image_id: int,
              purposes: Iterable[str], *, note: str | None = None) -> tuple[Pin, ...]:
    """Explicit user command: insert or merge purposes, preserving the existing note.

    Supplying note replaces it (including an explicit empty string). An empty
    purpose command is rejected; displaying a recommendation never creates a pin.
    """
    _id(project_id, "project_id")
    _id(image_id, "image_id")
    selected = normalize_purposes(purposes)
    if not selected:
        raise ValueError("select at least one purpose to pin")
    rows = _pins(pins)
    for i, pin in enumerate(rows):
        if (pin.project_id, pin.image_id) == (project_id, image_id):
            merged = replace(pin, purposes=normalize_purposes((*pin.purposes, *selected)),
                             note=pin.note if note is None else note)
            return rows[:i] + (merged,) + rows[i + 1:]
    return rows + (Pin(project_id, image_id, selected, note if note is not None else ""),)


def remove_purpose(pins: Iterable[Pin], project_id: int, image_id: int,
                   purpose: str) -> tuple[Pin, ...]:
    """Remove one use; remove the pin only when its last purpose is removed."""
    _id(project_id, "project_id")
    _id(image_id, "image_id")
    normalize_purposes((purpose,))
    rows = _pins(pins)
    result = []
    for pin in rows:
        if (pin.project_id, pin.image_id) != (project_id, image_id) or purpose not in pin.purposes:
            result.append(pin)
            continue
        remaining = tuple(p for p in pin.purposes if p != purpose)
        if remaining:
            result.append(replace(pin, purposes=remaining))
    return tuple(result)
