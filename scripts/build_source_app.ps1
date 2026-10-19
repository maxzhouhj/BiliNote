param([switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $projectRoot
try {
    if (-not $SkipFrontend) {
        Push-Location (Join-Path $projectRoot 'BillNote_frontend')
        try {
            & pnpm.cmd install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            & pnpm.cmd build:source
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    & python -X utf8 scripts/package_source_app.py
    if ($LASTEXITCODE -ne 0) { throw 'Source application packaging failed.' }
} finally { Pop-Location }
