# Pinned by digest so a rebuild of the same commit produces the same image.
# Debian security fixes arrive through this digest: Dependabot bumps it
# (.github/dependabot.yml), and the Trivy scan reports what the current one
# carries.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

RUN useradd --create-home --uid 1000 dynaform
WORKDIR /app

# pip is removed once the dependencies are in place. Nothing at run time needs
# it -- gunicorn runs the app -- and Trivy reads the PEP 770 SBOM pip ships,
# reporting CVEs against the packages it vendors. A runtime that cannot install
# packages is the better shape anyway.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
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
