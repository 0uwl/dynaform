"""Shared pytest fixtures for the DynaForm test suite."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest

from app import create_app as _create_app


@pytest.fixture
def app():
    flask_app = _create_app()
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def library(app, tmp_path):
    """Point TEMPLATE_DIR at an empty tmp dir and hand it back to the test.

    Shares the function-scoped `app`, so a test can ask for both `library` and
    `client` and the client sees the configured directory.
    """
    app.config["TEMPLATE_DIR"] = str(tmp_path)
    return tmp_path
