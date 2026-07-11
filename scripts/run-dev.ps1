$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -c constraints.txt
alembic upgrade head
uvicorn rental_manager.main:app --reload --host 127.0.0.1 --port 8000
