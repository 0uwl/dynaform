# Loaded automatically by gunicorn from its working directory (/app in the
# image). Reading the environment here, rather than in a shell-form CMD, lets
# the Containerfile use exec form: gunicorn runs as PID 1 and receives
# SIGTERM directly on `podman stop`, instead of via a shell that drops it.
import os
import resource

bind = f"{os.environ.get('BIND_HOST', '[::]')}:{os.environ.get('BIND_PORT', '8000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
accesslog = "-"
errorlog = "-"
# A worker stuck rendering a runaway template ({% for %} nested in
# {% for %}) is killed and replaced after this many seconds.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "10"))

# Caps each worker's address space, so a template that asks for gigabytes
# ("a" * 10**9) gets a MemoryError -- shown on the form like any other render
# failure -- instead of taking the host's memory with it.
WORKER_MAX_MEMORY_MB = int(os.environ.get("WORKER_MAX_MEMORY_MB", "256"))


def post_fork(server, worker):
    limit = WORKER_MAX_MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
