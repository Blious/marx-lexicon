[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$Python,
    [switch]$WithMcp,
    [switch]$ReadOnly,
    [switch]$NoBrowser,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'

if (-not $Python) {
    $localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
}

function Invoke-ProjectPython {
    & $Python @args
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE). See the output above."
    }
}

$browserJob = $null
Push-Location -LiteralPath $PSScriptRoot
try {
    $modules = 'marx_lexicon,fastapi,uvicorn,jinja2,markdown_it,rdflib'
    $extras = 'web,export'
    if (-not $ReadOnly) {
        $modules += ',filelock'
        $extras += ',edit'
    }
    if ($WithMcp) {
        $modules += ',mcp'
        $extras += ',mcp'
    }
    & $Python -c "import importlib.util, sys; sys.exit(not all(importlib.util.find_spec(n) for n in '$modules'.split(',')))"
    if ($Install -or $LASTEXITCODE -ne 0) {
        Write-Host 'Installing project dependencies...'
        Invoke-ProjectPython -m pip install -e "${PSScriptRoot}[$extras]"
    }

    # Rebuild from the current YAML and articles before starting the local server.
    Invoke-ProjectPython -m marx_lexicon --root $PSScriptRoot build
    $url = "http://127.0.0.1:$Port/"
    Write-Host "Web: $url"
    if ($WithMcp) { Write-Host "MCP: ${url}mcp" }
    Write-Host 'Press Ctrl+C in this window to stop the server.'

    if (-not $NoBrowser) {
        $browserJob = Start-Job -ArgumentList $url -ScriptBlock {
            param($url)
            for ($attempt = 0; $attempt -lt 60; $attempt++) {
                try {
                    $response = Invoke-WebRequest -Uri "${url}api/metadata" -UseBasicParsing -TimeoutSec 1
                    if ($response.StatusCode -eq 200) {
                        Start-Process $url
                        return
                    }
                }
                catch { Start-Sleep -Milliseconds 250 }
            }
        }
    }

    $serverArgs = @('-m', 'marx_lexicon', '--root', $PSScriptRoot,
                    'serve', '--host', '127.0.0.1', '--port', "$Port")
    if ($WithMcp) { $serverArgs += '--with-mcp' }
    if (-not $ReadOnly) { $serverArgs += '--edit' }
    Invoke-ProjectPython @serverArgs
}
finally {
    if ($browserJob) {
        Stop-Job -Job $browserJob
        Remove-Job -Job $browserJob
    }
    Pop-Location
}
