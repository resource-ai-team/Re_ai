$ErrorActionPreference = 'Stop'
$repoDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$python = Join-Path $repoDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv and install the project dependencies first (see README).'
}
Push-Location -LiteralPath $repoDirectory
try {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.data\manifest.json'))) {
        & $python -m scripts.kg_explorer.export_graph
        if ($LASTEXITCODE -ne 0) { throw 'Graph export failed. Check Neo4j configuration and import.' }
    }
    Write-Output 'Graph Explorer: http://127.0.0.1:8020/ (Ctrl+C to stop)'
    & $python -m uvicorn scripts.kg_explorer.server:app --host 127.0.0.1 --port 8020
    if ($LASTEXITCODE -ne 0) { throw 'Explorer exited with an error.' }
} finally {
    Pop-Location
}
