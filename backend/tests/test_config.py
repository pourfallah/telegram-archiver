"""Configuration sanity tests."""
from app.config import Settings, get_settings


def test_settings_defaults_are_sane():
    s = Settings()
    assert s.app_name == "Telegram Archive & Migration Suite"
    assert s.checkpoint_every >= 1
    assert s.media_concurrency >= 1
    assert s.export_msgs_per_sec > 0
    assert s.jwt_algorithm == "HS256"


def test_settings_origin_list_splits_commas():
    s = Settings(allowed_origins="http://a, http://b")
    assert s.origin_list == ["http://a", "http://b"]


def test_settings_override_via_env(monkeypatch):
    monkeypatch.setenv("EXPORT_MSGS_PER_SEC", "2.5")
    s = Settings()
    assert s.export_msgs_per_sec == 2.5


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
