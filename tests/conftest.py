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
