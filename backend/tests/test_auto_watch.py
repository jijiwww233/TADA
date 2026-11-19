"""Automatic watcher: startup check, busy/account/login guards, no model calls."""
import os
import time

from app.sync.auto_watch import AutoSync


def _paused_needs_login(finished_at):
    return {"status": "paused", "pause_reason": "needs_login", "finished_at": finished_at}


def wait_for(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_auto_sync_triggers_when_logged_in_and_idle():
    calls = []
    state = {"busy": False, "account_known": True, "last_run": {"status": "paused", "pause_reason": "network"}}
    sync = AutoSync("xhs", lambda: calls.append(1) or True, lambda: state,
                    interval_s=0.2, startup_delay_s=0)
    assert sync.start() is True
    try:
        assert wait_for(lambda: calls)
    finally:
        sync.stop()
    assert calls


def test_auto_sync_skips_busy_and_not_logged_in():
    calls = []
    busy = {"busy": True, "account_known": True, "last_run": None}
    sync = AutoSync("x", lambda: calls.append(1) or True, lambda: busy,
                    interval_s=0.1, startup_delay_s=0)
    sync.start(); time.sleep(0.25); sync.stop()
    assert calls == []
    unknown = {"busy": False, "account_known": False, "last_run": None}
    sync = AutoSync("x", lambda: calls.append(1) or True, lambda: unknown,
                    interval_s=0.1, startup_delay_s=0)
    sync.start(); time.sleep(0.25); sync.stop()
    assert calls == []


def test_auto_sync_needs_login_does_not_trigger():
    calls = []
    state = {"busy": False, "account_known": True,
             "last_run": {"status": "paused", "pause_reason": "needs_login"}}
    sync = AutoSync("xhs", lambda: calls.append(1) or True, lambda: state,
                    interval_s=0.1, startup_delay_s=0)
    sync.start()
    try:
        assert wait_for(lambda: sync.needs_login)
    finally:
        sync.stop()
    assert calls == []


def test_auto_sync_disabled_does_not_start():
    sync = AutoSync("x", lambda: True, lambda: {}, enabled=False)
    assert sync.start() is False
    assert sync.status()["auto"]["running"] is False

def test_auto_sync_retries_when_account_refreshed_after_failed_run(tmp_path):
    """A re-login (new account artifact) must clear the effect of an older needs_login run."""
    artifact = tmp_path / "account.json"
    artifact.write_text("{}")
    os.utime(artifact, (time.time(), time.time()))
    calls = []
    state = {"busy": False, "account_known": True, "last_run": _paused_needs_login("2020-01-01 00:00:00")}
    sync = AutoSync("xhs", lambda: calls.append(1) or True, lambda: state,
                    interval_s=0.2, startup_delay_s=0,
                    account_mtime=lambda: artifact.stat().st_mtime)
    sync.start()
    try:
        assert wait_for(lambda: calls)
    finally:
        sync.stop()
    assert calls
    assert sync.needs_login is False


def test_auto_sync_keeps_skipping_when_account_older_than_failed_run(tmp_path):
    artifact = tmp_path / "account.json"
    artifact.write_text("{}")
    os.utime(artifact, (1000.0, 1000.0))
    calls = []
    state = {"busy": False, "account_known": True, "last_run": _paused_needs_login("2020-01-01 00:00:00")}
    sync = AutoSync("xhs", lambda: calls.append(1) or True, lambda: state,
                    interval_s=0.1, startup_delay_s=0,
                    account_mtime=lambda: artifact.stat().st_mtime)
    sync.start()
    try:
        assert wait_for(lambda: sync.needs_login)
    finally:
        sync.stop()
    assert calls == []


def test_auto_sync_publishes_next_check_while_waiting_for_login():
    state = {"busy": False, "account_known": True, "last_run": _paused_needs_login("2020-01-01 00:00:00")}
    sync = AutoSync("xhs", lambda: True, lambda: state, interval_s=0.1, startup_delay_s=0)
    sync.start()
    try:
        assert wait_for(lambda: sync.needs_login and sync.next_check_at is not None)
    finally:
        sync.stop()
