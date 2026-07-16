@echo off

start "FastAPI" cmd /k "cd /d C:\Users\GEORGE\OneDrive\Documents\Grad-school\projects\PCI-APP\PCI-server && call .venv\Scripts\activate.bat && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

start "Celery" cmd /k "cd /d C:\Users\GEORGE\OneDrive\Documents\Grad-school\projects\PCI-APP\PCI-server && call .venv\Scripts\activate.bat && celery -A app.core.celery_app worker --loglevel=info --pool=solo"