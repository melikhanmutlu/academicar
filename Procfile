web: sh -c 'gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 8 --timeout 180'
worker: python worker.py
