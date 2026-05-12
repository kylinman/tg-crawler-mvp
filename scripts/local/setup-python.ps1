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
$projects = @('web', 'crawler')

foreach ($project in $projects) {
    $projectDir = Join-Path $repoRoot $project
    $venvPython = New-VenvIfMissing -ProjectDir $projectDir -PythonLauncher $pythonLauncher

    if ($UpgradePip) {
        & $venvPython -m pip install --upgrade pip
    }

    & $venvPython -m pip install -r (Join-Path $projectDir 'requirements.txt')
}

"Python environments ready for web and crawler."
