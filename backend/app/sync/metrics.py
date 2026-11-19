"""Opt-in stage timings. Never includes account ids, URLs, tokens or payloads."""
import json
import os
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import threading
_LOCK = threading.Lock()

@contextmanager
def measure(platform, stage, units=1):
    started = time.perf_counter()
    success = False
    try:
        yield
        success = True
    finally:
        path = os.getenv('SYNC_METRICS_FILE')
        if path:
            row = dict(platform=platform, stage=stage, units=units,
                       seconds=time.perf_counter()-started, success=success)
            try:
                with _LOCK:
                    target = Path(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('a', encoding='utf8') as stream:
                        stream.write(json.dumps(row)+'\n')
            except OSError:
                pass  # diagnostics must not change cursor/commit semantics

def timed(platform, stage):
    def decorate(fn):
        @wraps(fn)
        def call(*args, **kwargs):
            with measure(platform, stage):
                return fn(*args, **kwargs)
        return call
    return decorate
