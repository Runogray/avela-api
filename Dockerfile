# Django 6 requires Python 3.12+. The host dev box runs 3.9, which is exactly
# why the API lives in a container.
FROM python:3.12-slim

# - no .pyc files (keeps mounted volumes clean / avoids root-owned __pycache__)
# - unbuffered stdout so logs stream straight to `docker compose logs`
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg[binary] ships manylinux wheels, so no libpq/build toolchain needed.
# Install deps first for a cached layer that only rebuilds when requirements change.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code.
COPY . .

# Run as an unprivileged user. Pre-create staticfiles/ so the named volume
# mounted there inherits appuser ownership (collectstatic writes to it in prod).
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/staticfiles \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser /app
USER appuser

EXPOSE 8000

# entrypoint waits for Postgres + runs migrations, then execs the CMD below.
ENTRYPOINT ["/app/entrypoint.sh"]

# Default (production) process. docker-compose overrides this with runserver
# for local development.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
