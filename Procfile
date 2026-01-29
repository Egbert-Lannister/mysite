web: gunicorn mysite.wsgi --workers 3 --threads 2 --timeout 60
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
