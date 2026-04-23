param(
    [switch]$ResetDemo
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

$argsList = @()
if ($ResetDemo) {
    $argsList += "--reset-demo"
}

.\.venv\Scripts\python.exe scripts\seed_demo.py @argsList
