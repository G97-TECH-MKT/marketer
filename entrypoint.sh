#!/bin/sh
set -e

if [ -n "$DATABASE_URL" ]; then
    echo "Running database migrations..."
    alembic -c /app/alembic.ini upgrade head
    echo "Migrations complete."
fi

exec "$@"
