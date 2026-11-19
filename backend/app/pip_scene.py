"""Pure validation for the PiP canvas scene JSON shared with the frontend.

No I/O happens here: the store validates a scene before writing and again after
reading, so a hand-edited row cannot reach the API. Every failure is a
ValueError subclass; the API maps it to 422 {"code": "invalid_scene"}.

Coordinates are canvas units. The contract only accepts JSON numbers: booleans
are not numbers, NaN/Infinity are rejected and numeric strings are rejected.
The returned dict is the normalized copy that is safe to store as JSON.
"""
import math
import re

MAX_ITEMS = 200
MAX_NOTES = 100
COORD_LIMIT = 1_000_000
MIN_SCALE = 0.05
MAX_SCALE = 20.0
MIN_SIZE = 1
MAX_SIZE = 100_000
ROTATION_LIMIT = 3600
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 200
MAX_NOTE_ID_LENGTH = 64
MAX_NOTE_TEXT_LENGTH = 2000
# Representable noise, not a validation hole: 0.8 + 0.2 > 1.0 in binary floats.
_CROP_EPSILON = 1e-9

_TOP_LEVEL_KEYS = frozenset(("version", "view", "items", "notes"))
_VIEW_KEYS = frozenset(("x", "y", "scale"))
_ITEM_KEYS = frozenset((
    "image_id", "x", "y", "width", "height", "rotation",
    "flip_x", "grayscale", "opacity", "crop",
))
_CROP_KEYS = frozenset(("x", "y", "width", "height"))
_NOTE_KEYS = frozenset(("id", "x", "y", "text", "color", "font_size"))

_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


class SceneError(ValueError):
    """The scene JSON violates the shared frontend/backend contract."""


def _fail(message):
    raise SceneError(message)


def _mapping(value, allowed, label):
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    keys = set(value)
    extra = keys - allowed
    missing = allowed - keys
    if extra:
        _fail(f"{label} has unknown keys: {', '.join(sorted(map(str, extra)))}")
    if missing:
        _fail(f"{label} is missing keys: {', '.join(sorted(map(str, missing)))}")
    return value


def _number(value, label, *, minimum=None, maximum=None, exclusive_minimum=False):
    # bool is an int subclass in Python but not a JSON number for this contract.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{label} must be finite")
    if minimum is not None:
        if value < minimum or (exclusive_minimum and value == minimum):
            comparator = ">" if exclusive_minimum else ">="
            _fail(f"{label} must be {comparator} {minimum}")
    if maximum is not None and value > maximum:
        _fail(f"{label} must be <= {maximum}")
    return value


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be an integer")
    return value


def _boolean(value, label):
    if not isinstance(value, bool):
        _fail(f"{label} must be a boolean")
    return value


def _view(value):
    view = _mapping(value, _VIEW_KEYS, "view")
    return {
        "x": _number(view["x"], "view.x", minimum=-COORD_LIMIT, maximum=COORD_LIMIT),
        "y": _number(view["y"], "view.y", minimum=-COORD_LIMIT, maximum=COORD_LIMIT),
        "scale": _number(view["scale"], "view.scale", minimum=MIN_SCALE, maximum=MAX_SCALE),
    }


def _crop(value, label):
    crop = _mapping(value, _CROP_KEYS, label)
    x = _number(crop["x"], f"{label}.x", minimum=0, maximum=1)
    y = _number(crop["y"], f"{label}.y", minimum=0, maximum=1)
    width = _number(crop["width"], f"{label}.width", minimum=0, maximum=1, exclusive_minimum=True)
    height = _number(crop["height"], f"{label}.height", minimum=0, maximum=1, exclusive_minimum=True)
    if x + width > 1 + _CROP_EPSILON:
        _fail(f"{label}.x + {label}.width must be <= 1")
    if y + height > 1 + _CROP_EPSILON:
        _fail(f"{label}.y + {label}.height must be <= 1")
    return {"x": x, "y": y, "width": width, "height": height}


def _item(value, index, seen_image_ids):
    label = f"items[{index}]"
    item = _mapping(value, _ITEM_KEYS, label)
    image_id = _integer(item["image_id"], f"{label}.image_id")
    if image_id < 1:
        _fail(f"{label}.image_id must be >= 1")
    if image_id in seen_image_ids:
        _fail(f"{label}.image_id {image_id} is duplicated within the scene")
    seen_image_ids.add(image_id)
    crop = item["crop"]
    return {
        "image_id": image_id,
        "x": _number(item["x"], f"{label}.x", minimum=-COORD_LIMIT, maximum=COORD_LIMIT),
        "y": _number(item["y"], f"{label}.y", minimum=-COORD_LIMIT, maximum=COORD_LIMIT),
        "width": _number(item["width"], f"{label}.width", minimum=MIN_SIZE, maximum=MAX_SIZE),
        "height": _number(item["height"], f"{label}.height", minimum=MIN_SIZE, maximum=MAX_SIZE),
        "rotation": _number(item["rotation"], f"{label}.rotation",
                            minimum=-ROTATION_LIMIT, maximum=ROTATION_LIMIT),
        "flip_x": _boolean(item["flip_x"], f"{label}.flip_x"),
        "grayscale": _boolean(item["grayscale"], f"{label}.grayscale"),
        "opacity": _number(item["opacity"], f"{label}.opacity", minimum=0, maximum=1),
        "crop": None if crop is None else _crop(crop, f"{label}.crop"),
    }


def _note(value, index, seen_ids):
    label = f"notes[{index}]"
    note = _mapping(value, _NOTE_KEYS, label)
    note_id = note["id"]
    if not isinstance(note_id, str) or not note_id:
        _fail(f"{label}.id must be a non-empty string")
    if len(note_id) > MAX_NOTE_ID_LENGTH:
        _fail(f"{label}.id must be at most {MAX_NOTE_ID_LENGTH} characters")
    if note_id in seen_ids:
        _fail(f"{label}.id {note_id!r} is duplicated within the scene")
    seen_ids.add(note_id)
    text = note["text"]
    if not isinstance(text, str):
        _fail(f"{label}.text must be a string")
    if len(text) > MAX_NOTE_TEXT_LENGTH:
        _fail(f"{label}.text must be at most {MAX_NOTE_TEXT_LENGTH} characters")
    color = note["color"]
    if not isinstance(color, str) or _COLOR_RE.fullmatch(color) is None:
        _fail(f"{label}.color must be #rrggbb hexadecimal")
    return {
        "id": note_id,
        "x": _number(note["x"], f"{label}.x", minimum=-COORD_LIMIT, maximum=COORD_LIMIT),
        "y": _number(note["y"], f"{label}.y", minimum=-COORD_LIMIT, maximum=COORD_LIMIT),
        "text": text,
        "color": color,
        "font_size": _number(note["font_size"], f"{label}.font_size",
                             minimum=MIN_FONT_SIZE, maximum=MAX_FONT_SIZE),
    }


def validate_scene(value):
    """Return a normalized deep copy of a valid scene or raise SceneError."""
    scene = _mapping(value, _TOP_LEVEL_KEYS, "scene")
    version = scene["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail("scene.version must be exactly 1")

    raw_items = scene["items"]
    if not isinstance(raw_items, list):
        _fail("scene.items must be an array")
    if len(raw_items) > MAX_ITEMS:
        _fail(f"scene.items must contain at most {MAX_ITEMS} entries")
    seen_image_ids = set()
    items = [_item(raw, index, seen_image_ids) for index, raw in enumerate(raw_items)]

    raw_notes = scene["notes"]
    if not isinstance(raw_notes, list):
        _fail("scene.notes must be an array")
    if len(raw_notes) > MAX_NOTES:
        _fail(f"scene.notes must contain at most {MAX_NOTES} entries")
    seen_note_ids = set()
    notes = [_note(raw, index, seen_note_ids) for index, raw in enumerate(raw_notes)]

    return {"version": 1, "view": _view(scene["view"]), "items": items, "notes": notes}
