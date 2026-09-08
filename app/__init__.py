"""DynaForm application factory."""
from __future__ import annotations

import logging

from flask import Flask

from .config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config["SECRET_KEY"]:
        if app.debug or app.testing:
            app.config["SECRET_KEY"] = "dev-only-insecure-key"
        else:
            raise RuntimeError("SECRET_KEY environment variable must be set")

    # Piggyback on gunicorn's stdout logger so log lines share one
    # formatter/stream instead of a second handler; falls back to Flask's
    # own default (also stdout) for local `flask run` without gunicorn.
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)

    from .routes import bp

    app.register_blueprint(bp)

    return app
