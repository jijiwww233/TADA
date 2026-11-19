"""Visible Huaban login waits past blocked/captcha states instead of closing the window."""
from app.sync import huaban_service
from app.sync.engine import ReaderError


class FakeBrowser:
    def __init__(self, states):
        self.states = list(states)
        self.closed = False
        self.opens = 0

    def open(self):
        self.opens += 1

    def is_closed(self):
        return self.closed

    def check_login(self):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return {"state": state}

    def close(self):
        self.closed = True


def test_login_keeps_window_open_after_blocked_then_succeeds():
    browser = FakeBrowser(["blocked", "ok"])
    out = huaban_service.run_login(lambda: browser, timeout_s=5, poll_s=0)
    assert out["ok"] is True
    assert out["message"] == "花瓣登录成功"
    assert browser.opens == 1
    assert browser.closed is True


def test_login_handles_reader_error_blocked_as_state():
    browser = FakeBrowser([ReaderError("blocked", "HTTP 405"), "ok"])
    out = huaban_service.run_login(lambda: browser, timeout_s=5, poll_s=0)
    assert out["ok"] is True


def test_login_timeout_reports_last_blocked_state():
    browser = FakeBrowser(["blocked"])
    out = huaban_service.run_login(lambda: browser, timeout_s=0.02, poll_s=0.005)
    assert out["ok"] is False
    assert "登录超时" in out["message"]
    assert "blocked" in out["message"]
