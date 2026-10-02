import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_load_without_env_file(monkeypatch):
    # Remove APP_ENV from the environment so we get the field default.
    # In CI, APP_ENV=test is set for the pytest job; without this monkeypatch
    # the assertion would fail even though the default is "development".
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings(_env_file=None)
    assert s.app_env == "development"
    assert s.agent_max_iterations == 10


def test_env_vars_override(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "5")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    s = Settings(_env_file=None)
    assert s.agent_max_iterations == 5
    # SecretStr keeps the key out of logs/reprs
    assert "sk-test" not in repr(s)
    assert s.openrouter_api_key.get_secret_value() == "sk-test"


def test_iteration_limit_is_bounded(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "1000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
