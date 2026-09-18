"""DynaForm application factory."""
from __future__ import annotations

import logging
import os

from flask import Flask

from .config import Config

# There is deliberately no default: a key baked into the image would be the
# same key on every install, which is the same as having no key at all. So
# refusing to start is the only safe answer -- and an operator who has just
# been refused deserves the exact commands rather than a one-line complaint.
SECRET_KEY_MISSING = """\
SECRET_KEY is not set, so DynaForm will not start.

It signs the CSRF token on every form, and there is no default on purpose: a
built-in key would be identical on every install, which is no protection.

Generate one:

    openssl rand -base64 32

Add it to the Quadlet unit (~/.config/containers/systemd/dynaform.container):

    Environment=SECRET_KEY=<the generated key>

Then reload and restart:

    systemctl --user daemon-reload
    systemctl --user restart dynaform

Running DynaForm outside a container? Set SECRET_KEY in its environment
instead; `flask --app app run --debug` supplies a throwaway key by itself.\
"""


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # Piggyback on gunicorn's stdout logger so log lines share one
    # formatter/stream instead of a second handler; falls back to Flask's
    # own default (also stdout) for local `flask run` without gunicorn. Done
    # before the SECRET_KEY check so that its message lands in the same
    # stream -- and so the journal carries it for a container that never got
    # far enough to serve a request.
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)

    if not app.config["SECRET_KEY"]:
        if app.debug or app.testing:
            app.config["SECRET_KEY"] = "dev-only-insecure-key"
        else:
            app.logger.critical(SECRET_KEY_MISSING)
            raise RuntimeError("SECRET_KEY is not set; see the log above for how to set it.")

    template_dir = app.config["TEMPLATE_DIR"]
    if not template_dir:
        app.logger.info("TEMPLATE_DIR unset: the template picker is off")
    elif os.path.isdir(template_dir):
        app.logger.info("Serving directory templates from %s", template_dir)
    else:
        app.logger.warning(
            "TEMPLATE_DIR %s is not a directory: the template picker stays empty",
            template_dir,
        )

    from .routes import bp

    app.register_blueprint(bp)

    return app
