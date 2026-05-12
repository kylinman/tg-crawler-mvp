param(
    [string]$DatabaseUrl = 'postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler',
    [string]$S3Endpoint = 'http://127.0.0.1:9000',
    [string]$S3PublicEndpoint = 'http://127.0.0.1:9000',
    [string]$S3AccessKey = 'minioadmin',
    [string]$S3SecretKey = 'minioadmin',
    [string]$S3Bucket = 'tg-media',
    [string]$AdminSecret = 'change-me-in-production',
    [int]$Port = 8080,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Import-EnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            return
        }

        $idx = $line.IndexOf('=')
        if ($idx -le 0) {
            return
        }

        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        Set-Item -Path "Env:$key" -Value $value
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$webDir = Join-Path $repoRoot 'web'
$pythonExe = Join-Path $webDir '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Python virtualenv not found: $pythonExe. Run scripts/local/setup-python.ps1 first."
}

Import-EnvFile (Join-Path $repoRoot '.env')
Import-EnvFile (Join-Path $repoRoot '.env.local')

$env:DATABASE_URL = $DatabaseUrl
$env:S3_ENDPOINT = $S3Endpoint
$env:S3_PUBLIC_ENDPOINT = $S3PublicEndpoint
$env:S3_ACCESS_KEY = $S3AccessKey
$env:S3_SECRET_KEY = $S3SecretKey
$env:S3_BUCKET = $S3Bucket
$env:ADMIN_SECRET = $AdminSecret

if (-not $Force) {
    $escapedWebDir = [regex]::Escape($webDir)
    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match $escapedWebDir -and
            $_.CommandLine -match 'uvicorn' -and
            $_.CommandLine -match 'main:app'
        }

    if ($existing) {
        $ids = ($existing | Select-Object -ExpandProperty ProcessId) -join ', '
        throw "Web appears to be running already (PID: $ids). Use -Force to bypass guard."
    }
}

Push-Location $webDir
try {
    & $pythonExe -m uvicorn main:app --host 0.0.0.0 --port $Port --reload
}
finally {
    Pop-Location
}
