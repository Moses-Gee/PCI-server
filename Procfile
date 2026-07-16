web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: celery -A app.core.celery_app worker --loglevel=info --pool=solo --concurrency=1