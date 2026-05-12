param(
    [string]$PgHost = '127.0.0.1',
    [int]$PgPort = 5432,
    [string]$PgAdminUser = 'postgres',
    [string]$PgAdminPassword = '',
    [string]$AppDb = 'tg_crawler',
    [string]$AppUser = 'tguser',
    [string]$AppPassword = 'tgpwd'
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$initSql = Join-Path $repoRoot 'init.sql'

if (-not (Test-Path -LiteralPath $initSql)) {
    throw "init.sql not found: $initSql"
}

function Get-RepoPython {
    param([string]$Root)

    $candidates = @(
        (Join-Path $Root 'web\.venv\Scripts\python.exe'),
        (Join-Path $Root 'crawler\.venv\Scripts\python.exe')
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) {
        return $systemPython.Source
    }

    return $null
}

function Get-PsqlExe {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $pgService = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'postgresql-x64-*' -or $_.DisplayName -like '*PostgreSQL*' } |
        Select-Object -First 1

    if ($pgService -and $pgService.PathName) {
        $m = [regex]::Match($pgService.PathName, '"(?<exe>[^\"]*pg_ctl\.exe)"')
        if ($m.Success) {
            $psqlCandidate = $m.Groups['exe'].Value -replace 'pg_ctl\.exe$', 'psql.exe'
            if (Test-Path -LiteralPath $psqlCandidate) {
                return $psqlCandidate
            }
        }
    }

    return $null
}

function Invoke-Psql {
    param(
        [string]$PsqlExe,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $PsqlExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code: $LASTEXITCODE)"
    }
}

function Invoke-PsqlScalar {
    param(
        [string]$PsqlExe,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    $result = & $PsqlExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code: $LASTEXITCODE)"
    }

    return ($result -join "`n").Trim()
}

$psql = Get-PsqlExe

if ($psql) {
    $safeUserIdent = $AppUser.Replace('"', '""')
    $safeDbIdent = $AppDb.Replace('"', '""')
    $safeUserLiteral = $AppUser.Replace("'", "''")
    $safeDbLiteral = $AppDb.Replace("'", "''")
    $safePwdLiteral = $AppPassword.Replace("'", "''")

    $roleExistsSql = "SELECT 1 FROM pg_roles WHERE rolname = '{0}'" -f $safeUserLiteral
    $dbExistsSql = "SELECT 1 FROM pg_database WHERE datname = '{0}'" -f $safeDbLiteral

    if ($PgAdminPassword) {
        $env:PGPASSWORD = $PgAdminPassword
    }

    $psqlInitSucceeded = $false
    try {
        $roleExists = Invoke-PsqlScalar -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $PgAdminUser, '-d', 'postgres', '-t', '-A', '-c', $roleExistsSql) -FailureMessage 'Failed to check app role existence'
        if (-not $roleExists) {
            Invoke-Psql -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $PgAdminUser, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', ("CREATE ROLE ""{0}"" LOGIN PASSWORD '{1}'" -f $safeUserIdent, $safePwdLiteral)) -FailureMessage 'Failed to create app role'
        }

        Invoke-Psql -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $PgAdminUser, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', ("ALTER ROLE ""{0}"" WITH LOGIN PASSWORD '{1}'" -f $safeUserIdent, $safePwdLiteral)) -FailureMessage 'Failed to set app role password'

        $dbExists = Invoke-PsqlScalar -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $PgAdminUser, '-d', 'postgres', '-t', '-A', '-c', $dbExistsSql) -FailureMessage 'Failed to check app database existence'
        if (-not $dbExists) {
            Invoke-Psql -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $PgAdminUser, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', ("CREATE DATABASE ""{0}"" OWNER ""{1}""" -f $safeDbIdent, $safeUserIdent)) -FailureMessage 'Failed to create app database'
        }

        Invoke-Psql -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $PgAdminUser, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', ("GRANT ALL PRIVILEGES ON DATABASE ""{0}"" TO ""{1}""" -f $safeDbIdent, $safeUserIdent)) -FailureMessage 'Failed to grant database privileges'

        if ($PgAdminPassword) {
            $env:PGPASSWORD = $AppPassword
        }
        Invoke-Psql -PsqlExe $psql -Arguments @('-h', $PgHost, '-p', "$PgPort", '-U', $AppUser, '-d', $AppDb, '-v', 'ON_ERROR_STOP=1', '-f', $initSql) -FailureMessage 'Failed to execute init.sql'
        $psqlInitSucceeded = $true
    }
    catch {
        Write-Warning "psql initialization failed, fallback to Python: $($_.Exception.Message)"
    }
    finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

    if ($psqlInitSucceeded) {
        "Database initialization completed via psql: $AppDb"
        return
    }
}

$pythonExe = Get-RepoPython -Root $repoRoot
if (-not $pythonExe) {
    throw 'psql not found, and Python not found. Install PostgreSQL client or run scripts/local/setup-python.ps1 first.'
}

$pyScript = Join-Path $PSScriptRoot 'init_db.py'
if (-not (Test-Path -LiteralPath $pyScript)) {
    throw "Python fallback script not found: $pyScript"
}

$pyArgs = @(
    $pyScript,
    '--host', $PgHost,
    '--port', "$PgPort",
    '--admin-user', $PgAdminUser,
    '--app-db', $AppDb,
    '--app-user', $AppUser,
    '--app-password', $AppPassword,
    '--init-sql', $initSql
)

if ($PgAdminPassword) {
    $pyArgs += @('--admin-password', $PgAdminPassword)
}

& $pythonExe @pyArgs
if ($LASTEXITCODE -ne 0) {
    throw "Python init failed with exit code $LASTEXITCODE"
}

"Database initialization completed via Python: $AppDb"
