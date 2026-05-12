param(
    [string]$DatabaseUrl = 'postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler',
    [string]$S3Endpoint = 'http://127.0.0.1:9000',
    [string]$S3PublicEndpoint = 'http://127.0.0.1:9000',
    [string]$S3AccessKey = 'minioadmin',
    [string]$S3SecretKey = 'minioadmin',
    [string]$S3Bucket = 'tg-media',
    [int]$OwnerUserId = 0,
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
$crawlerDir = Join-Path $repoRoot 'crawler'
$pythonExe = Join-Path $crawlerDir '.venv\Scripts\python.exe'

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

if ($OwnerUserId -gt 0) {
    $env:CRAWLER_OWNER_USER_ID = "$OwnerUserId"
}

if ($OwnerUserId -le 0 -and (-not $env:TG_API_ID -or -not $env:TG_API_HASH -or -not $env:TG_PHONE)) {
    throw 'Missing TG_API_ID/TG_API_HASH/TG_PHONE. Put them into .env.local or current shell env.'
}

if (-not $Force) {
    $escapedCrawlerDir = [regex]::Escape($crawlerDir)
    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match $escapedCrawlerDir -and
            $_.CommandLine -match 'main.py'
        }

    if ($existing) {
        $ids = ($existing | Select-Object -ExpandProperty ProcessId) -join ', '
        throw "Crawler appears to be running already (PID: $ids). Use -Force to bypass guard."
    }
}

Push-Location $crawlerDir
try {
    & $pythonExe main.py
}
finally {
    Pop-Location
}
