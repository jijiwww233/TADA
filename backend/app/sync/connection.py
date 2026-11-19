"""Connection labels use local evidence only. Status reads never probe a platform."""
from . import safety

LABELS = {
    "connected": "已连接",
    "needs_login": "需要登录",
    "needs_verification": "需要验证",
    "blocked": "被拦截",
}


def describe(platform: str, state: dict) -> dict:
    """Map the last persisted sync state to one honest label. No network access."""
    run = state.get("last_run") or {}
    reason = run.get("pause_reason")
    if platform == "xhs" and safety.state()["blocked"]:
        key = "blocked"
    elif reason == "needs_login":
        key = "needs_login"
    elif reason in ("captcha", "verification") or (platform == "zcool" and reason == "blocked"):
        key = "needs_verification"
    elif reason in ("blocked", "rate_limited") or run.get("status") == "failed":
        key = "blocked"
    elif not state.get("account_known"):
        key = "needs_login"
    else:
        key = "connected"
    return {
        "connection_state": key,
        "connection_label": LABELS[key],
        "verification_waiting": state.get("action") == "verify",
    }
