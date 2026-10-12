#!/usr/bin/env bash
# Container entrypoint: wait for the database, apply migrations, collect static
# in production, then hand off to the CMD (gunicorn) or the compose `command`
# override (runserver).
set -euo pipefail

# --- wait for Postgres (only when we're actually using it) -------------------
if [ -n "${DATABASE_URL_HOST:-}" ]; then
  echo "Waiting for Postgres at ${DATABASE_URL_HOST}:${DB_PORT:-5432} ..."
  python - <<'PY'
import os, socket, sys, time

host = os.environ["DATABASE_URL_HOST"]
port = int(os.environ.get("DB_PORT", "5432"))
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        time.sleep(1)
else:
    sys.exit(f"Postgres at {host}:{port} not reachable after 60s")
print("Postgres is up.")
PY
fi

# --- migrations --------------------------------------------------------------
echo "Applying database migrations ..."
python manage.py migrate --noinput

# --- static files (production only; runserver serves them in DEBUG) ----------
if [ "${DJANGO_DEBUG:-1}" != "1" ]; then
  echo "Collecting static files ..."
  python manage.py collectstatic --noinput
fi

exec "$@"
