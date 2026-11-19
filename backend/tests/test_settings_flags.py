"""Auto-sync product settings: global switch + per-platform opt-out."""
from app import settings


def test_platform_flags_default_on_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("WOMINMIN_SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert settings.read()["auto_platforms"] == {"xhs": True, "huaban": True,
                                                 "pinterest": True, "zcool": True}
    settings.write(auto_sync=True)
    settings.write(auto_platforms={"zcool": False})
    state = settings.read()
    assert state["auto_sync"] is True
    assert state["auto_platforms"]["zcool"] is False
    assert state["auto_platforms"]["xhs"] is True


def test_unknown_platform_flag_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("WOMINMIN_SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings.write(auto_platforms={"zcool": False, "nope": False})
    assert "nope" not in settings.read()["auto_platforms"]
