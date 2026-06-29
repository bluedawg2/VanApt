# Double-click-friendly launcher for Windows.
# Right-click -> "Run with PowerShell", or run:  ./start.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Ensure the one optional dependency is present.
python -c "import bs4" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing beautifulsoup4..." -ForegroundColor Yellow
    python -m pip install --quiet beautifulsoup4
}

Write-Host "Starting vanapt apartment finder..." -ForegroundColor Cyan
python run.py
