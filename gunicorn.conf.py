# Loaded automatically by gunicorn from its working directory (/app in the
# image). Reading the environment here, rather than in a shell-form CMD, lets
# the Containerfile use exec form: gunicorn runs as PID 1 and receives
# SIGTERM directly on `podman stop`, instead of via a shell that drops it.
import os

bind = f"{os.environ.get('BIND_HOST', '0.0.0.0')}:{os.environ.get('BIND_PORT', '8000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
accesslog = "-"
errorlog = "-"
