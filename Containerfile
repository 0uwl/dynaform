FROM python:3.12-slim

RUN useradd --create-home --uid 1000 dynaform
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

USER dynaform
EXPOSE 8000

CMD gunicorn -w ${GUNICORN_WORKERS:-2} -b ${BIND_HOST:-0.0.0.0}:${BIND_PORT:-8000} \
    --access-logfile - --error-logfile - "app:create_app()"
