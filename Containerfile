FROM python:3.12-slim

# Apply Debian's security updates. The base image is rebuilt on its own
# schedule, and between rebuilds the packages inside it fall behind the
# security archive -- as *fixable* CVEs, which is the category `ignore-unfixed`
# in the Trivy scan deliberately does not filter out. PR #5's first scan found
# 27 of them (3 CRITICAL, 10 HIGH) in an otherwise untouched base: gzip
# 1.13-1 with 1.13-1+deb13u1 published, glibc +deb13u3 with +deb13u4
# published, and so on. Nothing here fixes that except installing them.
#
# This deliberately floats, and a Dockerfile linter will say so (droast DF069,
# hadolint DL3005: "makes builds non-reproducible"). That is the right trade
# here: requirements.txt pins what the application *is*, while the security
# layer underneath it is supposed to move. A build pinned to last month's
# vulnerabilities is reproducible in the least useful sense of the word.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 dynaform
WORKDIR /app

# pip ships in the base image and is scanned like anything else, so it is
# upgraded first (25.0.1 carried five fixable CVEs) and then removed once the
# dependencies are in place.
#
# Removing it is not tidiness. pip 26 ships a PEP 770 SBOM declaring the
# packages it vendors, which Trivy reads -- so an up-to-date pip reports CVEs
# against its *bundled* msgpack and setuptools (GHSA-6v7p-g79w-8964,
# CVE-2025-47273, CVE-2026-59890) that no upgrade of pip itself can clear.
# Nothing at run time needs pip: gunicorn runs the app. Taking it out drops
# that whole inventory from the image, and a runtime that cannot install
# packages is the better shape anyway.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
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
