param(
    [ValidateSet("setup", "start", "stop", "status")]
    [string]$Action = "status",
    [int]$Port = 55432
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$localRoot = Join-Path $projectRoot ".local"
$dataDirectory = Join-Path $localRoot "postgres-18-data"
$logPath = Join-Path $localRoot "postgres-18.log"
$postgresBin = if ($env:WEBMARKET_POSTGRES_BIN) {
    $env:WEBMARKET_POSTGRES_BIN
} else {
    "D:\INSTALL\PostgreSQL\18\bin"
}

$initdb = Join-Path $postgresBin "initdb.exe"
$pgCtl = Join-Path $postgresBin "pg_ctl.exe"
$pgIsReady = Join-Path $postgresBin "pg_isready.exe"
$createdb = Join-Path $postgresBin "createdb.exe"
$psql = Join-Path $postgresBin "psql.exe"

foreach ($executable in @($initdb, $pgCtl, $pgIsReady, $createdb, $psql)) {
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "PostgreSQL executable not found: $executable"
    }
}

function Start-LocalPostgres {
    if (-not (Test-Path -LiteralPath (Join-Path $dataDirectory "PG_VERSION"))) {
        throw "Local cluster is not initialized. Run: .\scripts\local_postgres.ps1 setup"
    }

    & $pgCtl -D $dataDirectory status *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Output "WebMarket PostgreSQL is already running on port $Port."
        return
    }

    & $pgCtl -D $dataDirectory -l $logPath -o "-p $Port -h 127.0.0.1" -w start
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start local PostgreSQL. See $logPath"
    }
}

function Ensure-WebMarketDatabase {
    $databaseExists = & $psql -w -h 127.0.0.1 -p $Port -U webmarket -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname = 'webmarket'"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the local PostgreSQL cluster."
    }

    if (($databaseExists | Out-String).Trim() -ne "1") {
        & $createdb -w -h 127.0.0.1 -p $Port -U webmarket webmarket
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to create the webmarket database."
        }
        Write-Output "Created local database: webmarket"
    }
}

switch ($Action) {
    "setup" {
        New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
        if (-not (Test-Path -LiteralPath (Join-Path $dataDirectory "PG_VERSION"))) {
            & $initdb -D $dataDirectory -U webmarket -E UTF8 --locale=C `
                --auth-local=trust --auth-host=trust
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to initialize the local PostgreSQL cluster."
            }
        }
        Start-LocalPostgres
        Ensure-WebMarketDatabase
        & $pgIsReady -h 127.0.0.1 -p $Port -d webmarket -U webmarket
    }
    "start" {
        Start-LocalPostgres
        Ensure-WebMarketDatabase
        & $pgIsReady -h 127.0.0.1 -p $Port -d webmarket -U webmarket
    }
    "stop" {
        if (Test-Path -LiteralPath (Join-Path $dataDirectory "PG_VERSION")) {
            & $pgCtl -D $dataDirectory -m fast -w stop
        }
    }
    "status" {
        if (-not (Test-Path -LiteralPath (Join-Path $dataDirectory "PG_VERSION"))) {
            Write-Output "WebMarket PostgreSQL is not initialized."
            exit 1
        }
        & $pgCtl -D $dataDirectory status
        & $pgIsReady -h 127.0.0.1 -p $Port -d webmarket -U webmarket
    }
}
