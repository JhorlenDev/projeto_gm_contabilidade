#!/bin/sh
set -e

python manage.py migrate
python manage.py seed_plano_contas_historicos
python manage.py collectstatic --noinput
python manage.py runserver 0.0.0.0:8000
