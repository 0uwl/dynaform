# uv is only a build tool: it is copied in through a bind mount for the one
# RUN that installs dependencies, so it never lands in the image. Pinned by
# digest and bumped by Dependabot, like the base image below.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# Pinned by digest so a rebuild of the same commit produces the same image.
# Debian security fixes arrive through this digest: Dependabot bumps it
# (.github/dependabot.yml), and the Trivy scan reports what the current one
# carries.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

RUN useradd --create-home --uid 1000 dynaform
WORKDIR /app

# Dependencies install from uv.lock (--locked fails the build if it is stale
# against pyproject.toml) into /app/.venv, which PATH puts first.
#
# The base image's pip is removed too. Nothing at run time needs it --
# gunicorn runs the app -- and Trivy reads the PEP 770 SBOM pip ships,
# reporting CVEs against the packages it vendors. A runtime that cannot
# install packages is the better shape anyway.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    uv sync --locked --no-dev --no-install-project --no-cache \
    && pip uninstall --yes pip

COPY app/ app/

# Mount point for the operator's own templates. Created empty and owned by the
# app user so a bind mount is optional: with nothing mounted the picker simply
# has nothing to list.
ENV TEMPLATE_DIR=/templates
RUN mkdir -p /templates && chown dynaform:dynaform /templates

USER dynaform
EXPOSE 8000

CMD gunicorn -w ${GUNICORN_WORKERS:-2} -b ${BIND_HOST:-0.0.0.0}:${BIND_PORT:-8000} \
    --access-logfile - --error-logfile - "app:create_app()"
