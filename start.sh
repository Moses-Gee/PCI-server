#!/bin/bash

# Exit immediately if any command fails
set -e

# Start the Celery worker in the background
echo "Starting Celery worker..."
celery -A app.core.celery_app worker --loglevel=info --pool=solo

# Start FastAPI in the foreground (Hugging Face expects port 7860)
echo "Starting FastAPI server..."
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 7860
