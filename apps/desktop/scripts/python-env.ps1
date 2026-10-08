# Picks the Python that builds EchoSpeak's backend and checks it has what the
# bundle needs. Dot-source it: . (Join-Path $PSScriptRoot "python-env.ps1")
#
# Why: the first `python` on PATH can belong to another app. 11.4.0 was built
# from another agent's virtualenv that had PyInstaller but none of EchoSpeak's
# packages, and PyInstaller quietly left web search and page reading out.

# Packages the installed app can't work without. PyInstaller is listed so a
# Python that can't build at all fails here with the same clear message.
$EchoBundleModules = @(
    "PyInstaller", "langchain_core", "langchain_community", "faster_whisper", "onnxruntime", "tokenizers",
    "ddgs", "primp", "lxml", "trafilatura"
)

function Resolve-EchoPython {
    param([string]$Requested = "")
    if ($Requested) { return $Requested }
    if ($env:ECHOSPEAK_PYTHON) { return $env:ECHOSPEAK_PYTHON }
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
    foreach ($candidate in @(
        (Join-Path $repoRoot "apps\backend\.venv\Scripts\python.exe"),
        (Join-Path $repoRoot ".venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $path = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $path) { return ([string]$path).Trim() }
    }
    return "python"
}

function Assert-EchoPythonReady {
    param([Parameter(Mandatory = $true)][string]$PythonExecutable)
    $where = (& $PythonExecutable -c "import sys; print(sys.executable)" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $where) {
        throw "Python '$PythonExecutable' could not be started. Pass -PythonExecutable or set ECHOSPEAK_PYTHON."
    }
    $where = ([string]$where).Trim()
    $probe = "import importlib.util as u, sys; print(','.join(m for m in sys.argv[1].split(',') if u.find_spec(m) is None))"
    $missing = ([string](& $PythonExecutable -c $probe ($EchoBundleModules -join ","))).Trim()
    if ($missing) {
        $requirements = (Resolve-Path (Join-Path $PSScriptRoot "..\..\backend\requirements.txt")).Path
        throw @"
The Python at $where is missing packages the app needs: $($missing -replace ',', ', ').
Install them into it:  & "$where" -m pip install -r "$requirements" pyinstaller==6.21.0
Or build with a Python that has them: -PythonExecutable <path>, or set ECHOSPEAK_PYTHON.
"@
    }
    # MCP: the app must ship SDK 2.3+ so it can talk to servers on the stateless 2026-07-28 spec.
    $mcpVersion = ([string](& $PythonExecutable -c "import importlib.metadata as m; print(m.version('mcp'))" 2>$null)).Trim()
    if (-not $mcpVersion -or [int]($mcpVersion.Split('.')[0]) -lt 2) {
        $requirements = (Resolve-Path (Join-Path $PSScriptRoot "..\..\backend\requirements.txt")).Path
        throw @"
The Python at $where has MCP SDK $mcpVersion; the app needs 2.3 or newer (MCP 2026-07-28 spec).
Update it:  & "$where" -m pip install -r "$requirements"
"@
    }
    Write-Host "Building with Python: $where"
    return $where
}
