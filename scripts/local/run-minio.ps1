param(
    [string]$MinioExe = 'C:\Users\dmc95\AppData\Local\Microsoft\WinGet\Packages\MinIO.Server_Microsoft.Winget.Source_8wekyb3d8bbwe\minio.exe',
    [string]$DataDir = 'D:\ai_project\telegram\tg-crawler-mvp\.local\minio\data',
    [string]$RootUser = 'minioadmin',
    [string]$RootPassword = 'minioadmin',
    [int]$ApiPort = 9000,
    [int]$ConsolePort = 9001,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $MinioExe)) {
    throw "MinIO executable not found: $MinioExe"
}

$dataParent = Split-Path -Parent $DataDir
if (-not (Test-Path -LiteralPath $dataParent)) {
    New-Item -ItemType Directory -Path $dataParent -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

if (-not $Force) {
    $existing = Get-CimInstance Win32_Process -Filter "Name='minio.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '--address' -and
            $_.CommandLine -match ":$ApiPort"
        }

    if ($existing) {
        $ids = ($existing | Select-Object -ExpandProperty ProcessId) -join ', '
        throw "MinIO appears to be running already (PID: $ids). Use -Force to bypass guard."
    }
}

$env:MINIO_ROOT_USER = $RootUser
$env:MINIO_ROOT_PASSWORD = $RootPassword

& $MinioExe server $DataDir --address ":$ApiPort" --console-address ":$ConsolePort"
