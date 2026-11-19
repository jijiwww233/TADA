"""Persistent, cross-worktree XHS circuit breaker. No network, cookies or account ids.

Location
- WOMINMIN_SAFETY_DIR/xhs.json when the launcher sets it (all instances must point at
  the same folder, so a trip in one instance stops the others);
- otherwise <data dir>/safety/xhs.json (the consolidated formal data folder);
- the pre-consolidation %LOCALAPPDATA%\\Wominmin\\safety\\xhs.json is still READ as a
  compatibility fallback while the new file does not exist. Writes always go to the
  current location.

Read semantics (review item 11)
Every state()/require() call re-reads the file; there is no in-process cache. Each XHS
network call is wrapped by network() (or calls require() itself), so a cooldown tripped
by any other instance/process is honoured before the very next request.

Fail closed: a lock file that exists but cannot be read or parsed is treated as locked.
Manual holds never expire automatically. Timed incidents require a later manual action;
watchers cannot resume them just because the clock passed the cooldown deadline.
"""
import json
import math
import os
import tempfile
import threading
import time
from functools import wraps
from pathlib import Path

from .. import config

MESSAGE = '平台风控中，请稍后手动同步'
HOURS = 6
_LOCK = threading.RLock()


class CoolingDown(RuntimeError):
    pass


def legacy_path() -> Path:
    base = os.getenv('LOCALAPPDATA') or str(Path.home() / '.local/share')
    return Path(base) / 'Wominmin' / 'safety' / 'xhs.json'


def path() -> Path:
    root = os.getenv('WOMINMIN_SAFETY_DIR')
    if root:
        return Path(root) / 'xhs.json'
    return config.data_dir() / 'safety' / 'xhs.json'


def _parent_readable(parent: Path) -> bool:
    """False only when the directory exists but cannot be listed (permission/IO)."""
    try:
        with os.scandir(parent) as entries:
            next(entries, None)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _validate(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('lock is not an object')
    if not any(key in raw for key in ('until', 'manual_hold', 'auto_paused')):
        raise ValueError('lock has no known fields')
    until = raw.get('until', 0)
    if isinstance(until, bool) or not isinstance(until, (int, float)) or not math.isfinite(float(until)):
        raise ValueError('lock until is invalid')
    for key in ('manual_hold', 'auto_paused'):
        if key in raw and not isinstance(raw[key], bool):
            raise ValueError(f'lock {key} is invalid')
    return raw


def _load_raw():
    """Newest lock file content, or None when no lock file exists anywhere.

    Raises OSError/ValueError when a file exists but is unreadable/corrupt; callers
    turn that into "locked" (fail closed).
    """
    current = path()
    legacy = legacy_path()
    candidates = [current] if current == legacy else [current, legacy]
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            if not _parent_readable(candidate.parent):
                raise OSError(f'lock directory is not readable: {candidate.parent}') from None
            continue
        return _validate(json.loads(text))
    return None


def state(*, now=None):
    now = time.time() if now is None else now
    try:
        raw = _load_raw()
    except (OSError, ValueError, TypeError):
        return {'blocked': True, 'auto_blocked': True, 'manual_hold': True, 'message': MESSAGE}
    if raw is None:
        return {'blocked': False, 'auto_blocked': False, 'manual_hold': False, 'message': None}
    until = float(raw.get('until', 0))
    hold = bool(raw.get('manual_hold', False))
    auto = bool(raw.get('auto_paused', False))
    return {**raw, 'blocked': hold or now < until,
            'auto_blocked': hold or now < until or auto, 'message': MESSAGE}


def trip(reason='异常响应', *, manual=False, now=None):
    now = time.time() if now is None else now
    with _LOCK:
        previous = state(now=now)
        raw = {'manual_hold': manual or bool(previous.get('manual_hold')),
               'auto_paused': True,
               'until': max(float(previous.get('until', 0)), now + HOURS * 3600),
               'incident_at': now, 'reason': reason}
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name: concurrent instances must not replace each other's temp file.
        handle, temp = tempfile.mkstemp(prefix='.xhs-', suffix='.tmp', dir=str(target.parent))
        try:
            with os.fdopen(handle, 'w', encoding='utf8') as file:
                json.dump(raw, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, target)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
    return state(now=now)


def require(*, automatic=False):
    """Re-read the lock file and raise when the requested class of work is paused."""
    current = state()
    if current['auto_blocked' if automatic else 'blocked']:
        raise CoolingDown(MESSAGE)


def network(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        require()
        return fn(*args, **kwargs)
    return guarded
