# Development setup for Windows (PowerShell 5.1+).
# Creates the venv, installs dependencies, starts the bundled PostgreSQL,
# runs migrations and validates the environment.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

if (-not (Test-Path -LiteralPath ".venv")) {
    Write-Host "Creating virtual environment (.venv) with Python 3.11..."
    py -3.11 -m venv .venv
}
$Python = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements-dev.txt

Write-Host "Starting development PostgreSQL on port 55432..."
& $Python scripts\dev_db.py start

Write-Host "Running migrations..."
& $Python -m app.db.migrate

Write-Host "Validating environment..."
& $Python scripts\validate_environment.py

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Fill GEMINI_API_KEY (and Databricks values if used) in config\.env"
Write-Host "  2. Create an administrator:"
Write-Host "       .venv\Scripts\python scripts\create_admin.py --username admin --email you@example.com"
Write-Host "  3. Start the API:  .venv\Scripts\python -m app"
Write-Host "  4. Start the UI:   .venv\Scripts\streamlit run frontend\streamlit_app.py"
