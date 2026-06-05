param(
    [switch]$UpgradePip
)

$ErrorActionPreference = 'Stop'

function Get-PythonExe {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        return "$($pyCmd.Source) -3"
    }

    throw 'Python not found. Install Python 3.11+ first.'
}

function New-VenvIfMissing {
    param(
        [string]$ProjectDir,
        [string]$PythonLauncher
    )

    $venvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($PythonLauncher.Contains(' -3')) {
            & py -3 -m venv (Join-Path $ProjectDir '.venv')
        }
        else {
            & python -m venv (Join-Path $ProjectDir '.venv')
        }
    }

    return $venvPython
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonLauncher = Get-PythonExe
$projects = @('web', 'crawler', 'desktop')

foreach ($project in $projects) {
    $projectDir = Join-Path $repoRoot $project
    $reqFile = Join-Path $projectDir 'requirements.txt'

    if (-not (Test-Path $reqFile)) {
        Write-Host "[$project] No requirements.txt, skipping."
        continue
    }

    $venvPython = New-VenvIfMissing -ProjectDir $projectDir -PythonLauncher $pythonLauncher

    if ($UpgradePip) {
        & $venvPython -m pip install --upgrade pip
    }

    & $venvPython -m pip install -r $reqFile
}

"Python environments ready for web, crawler and desktop (Qt UI)."
Write-Host ""
Write-Host "Tip: On Unix, install 'uv' (https://astral.sh/uv) for much faster setup (the .sh script prefers it automatically)."
