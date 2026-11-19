"""Small persisted UI settings (auto sync). Stored under the refactor data dir."""
from __future__ import annotations
import json
import os
from pathlib import Path

from . import config

PLATFORMS = ("xhs", "huaban", "pinterest", "zcool")


def _path() -> Path:
    return Path(os.environ.get("WOMINMIN_SETTINGS_FILE", config.data_dir() / "settings.json"))


def _platforms(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {name: bool(raw.get(name, True)) for name in PLATFORMS}


def read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"auto_sync": bool(data.get("auto_sync", False)),
                    "auto_platforms": _platforms(data.get("auto_platforms"))}
    except Exception:
        pass
    return {"auto_sync": False, "auto_platforms": _platforms({})}


def write(**changes) -> dict:
    data = read()
    if "auto_sync" in changes:
        data["auto_sync"] = bool(changes["auto_sync"])
    if "auto_platforms" in changes and isinstance(changes["auto_platforms"], dict):
        merged = dict(data["auto_platforms"])
        merged.update({k: bool(v) for k, v in changes["auto_platforms"].items() if k in PLATFORMS})
        data["auto_platforms"] = merged
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return data
