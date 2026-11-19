"""演示版边界：普通整理操作放行，敏感操作仍然 403。"""
from tools import public_demo


def test_normal_organizing_writes_are_allowed():
    assert public_demo.request_allowed("PUT", "/api/images/12/annotation")
    assert public_demo.request_allowed("POST", "/api/reference/decisions")
    assert public_demo.request_allowed(
        "POST", "/api/reference/decisions/0123456789abcdef0123456789abcdef/undo")


def test_sensitive_operations_stay_blocked():
    blocked = [
        ("PUT", "/api/settings/api-key"),
        ("POST", "/api/settings/auto-sync"),
        ("POST", "/api/settings/auto-sync/xhs"),
        ("POST", "/api/sync/all"),
        ("POST", "/api/sync/xhs/start"),
        ("POST", "/api/watch-folders"),
        ("PUT", "/api/watch-folders/settings"),
        ("DELETE", "/api/watch-folders/1"),
        ("POST", "/api/phone-import/pairing"),
        ("DELETE", "/api/phone-import/pairing"),
        ("DELETE", "/api/projects/1"),
        ("DELETE", "/api/images/1"),
    ]
    for method, path in blocked:
        assert not public_demo.request_allowed(method, path), (method, path)


def test_reads_and_existing_project_writes_still_work():
    assert public_demo.request_allowed("GET", "/api/images")
    assert public_demo.request_allowed("GET", "/api/reference/review")
    assert public_demo.request_allowed("POST", "/api/projects")
    assert public_demo.request_allowed("PATCH", "/api/projects/3")
    assert public_demo.request_allowed("DELETE", "/api/projects/3/pins/7")
    assert not public_demo.request_allowed("DELETE", "/api/projects/3")
