#!/bin/sh
set -e

echo "[entrypoint] applying database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] failing tasks interrupted by a previous restart..."
python manage.py fail_stale_tasks || echo "[entrypoint] stale-task cleanup skipped"

echo "[entrypoint] seeding BGM library..."
python manage.py seed_bgm || echo "[entrypoint] BGM seed skipped"

echo "[entrypoint] starting gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --worker-class gthread \
    --threads 8 \
    --timeout 0 \
    --access-logfile - \
    --error-logfile -
