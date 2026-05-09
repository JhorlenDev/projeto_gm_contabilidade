#!/bin/sh
set -e

python manage.py migrate
python manage.py seed_plano_contas_historicos
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
