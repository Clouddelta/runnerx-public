param(
    [string]$MySqlBin = 'C:\Program Files\MySQL\MySQL Server 8.0\bin',
    [int]$DatabasePort = 3318,
    [int]$ApiPort = 8000,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $projectRoot
try {
    if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required' }
    }
    $runtimePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (!$SkipInstall) {
        & $runtimePython -m pip install '.[dev]'
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    }
    & $runtimePython scripts/local_mysql.py prepare --bin-dir $MySqlBin --port $DatabasePort
    if ($LASTEXITCODE -ne 0) { throw 'Private MySQL startup failed' }
    $nativeState = Get-Content -LiteralPath '.local\native\state.json' -Raw | ConvertFrom-Json
    $env:APP_ENV = 'local'
    $env:DB_HOST = '127.0.0.1'
    $env:DB_PORT = [string]$nativeState.port
    $env:DB_USER = 'runnerx'
    $env:DB_NAME = 'runnerx'
    $env:DB_PASSWORD = $nativeState.db_password
    $env:DB_UNIX_SOCKET = ''
    $env:RUNNERX_DATABASE_URL = ''
    $env:API_DOCS_ENABLED = 'true'
    $env:DEMO_API_KEY = $nativeState.api_key
    & $runtimePython -m runnerx.cli init-db
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
    & $runtimePython -m runnerx.cli seed-demo --data-dir data/sample
    if ($LASTEXITCODE -ne 0) { throw 'Sample import failed' }
    Write-Host "Swagger UI: http://127.0.0.1:$ApiPort/docs"
    Write-Host 'The private demo key is stored in .local/native/state.json (api_key).'
    Write-Host 'Press Ctrl+C to stop the API. The database will also be shut down.'
    try {
        & $runtimePython -m uvicorn runnerx.api:app --host 127.0.0.1 --port $ApiPort
    } finally {
        & $runtimePython scripts/local_mysql.py stop
    }
} finally {
    Pop-Location
}
