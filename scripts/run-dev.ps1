$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
uvicorn rental_manager.main:app --reload --host 127.0.0.1 --port 8000
