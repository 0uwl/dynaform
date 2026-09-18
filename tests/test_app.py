"""Unit tests for app/__init__.py: the application factory's startup checks."""
import logging

import pytest

from app import SECRET_KEY_MISSING, create_app
from app.config import Config


@pytest.fixture
def no_secret_key(monkeypatch):
    """Config reads the environment at import time, so patch the attribute."""
    monkeypatch.setattr(Config, "SECRET_KEY", "")


class TestSecretKeyRequired:
    def test_refuses_to_start_without_a_key(self, no_secret_key):
        with pytest.raises(RuntimeError):
            create_app()

    def test_never_invents_a_key_of_its_own(self, no_secret_key):
        # A generated key would differ per install and per restart, and would
        # silently undo the point of refusing to start.
        with pytest.raises(RuntimeError):
            create_app()

    def test_logs_how_to_fix_it(self, no_secret_key, caplog):
        with caplog.at_level(logging.CRITICAL), pytest.raises(RuntimeError):
            create_app()
        assert "openssl rand -base64 32" in caplog.text
        assert "Environment=SECRET_KEY=" in caplog.text

    def test_the_message_points_at_the_quadlet_unit(self):
        assert "dynaform.container" in SECRET_KEY_MISSING
        assert "systemctl --user restart dynaform" in SECRET_KEY_MISSING

    def test_the_message_carries_no_key_of_its_own(self):
        # It tells you how to make one; it must never contain a usable value.
        assert "dev-only-insecure-key" not in SECRET_KEY_MISSING

    def test_debug_and_test_runs_still_get_a_throwaway_key(self, no_secret_key, monkeypatch):
        monkeypatch.setenv("FLASK_DEBUG", "1")
        app = create_app()
        assert app.config["SECRET_KEY"] == "dev-only-insecure-key"


class TestConfiguredKey:
    def test_a_supplied_key_is_used_as_is(self, monkeypatch):
        monkeypatch.setattr(Config, "SECRET_KEY", "a-real-key")
        assert create_app().config["SECRET_KEY"] == "a-real-key"
