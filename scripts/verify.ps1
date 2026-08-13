# Local gate matching CI static + tests jobs (see .github/workflows/ci.yml).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$env:UV_PYTHON = if ($env:UV_PYTHON) { $env:UV_PYTHON } else { "3.14" }

function Invoke-PythonTool {
    param([string[]]$ToolArgs)
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --no-sync @ToolArgs
        if ($LASTEXITCODE -eq 0) { return }
    }
    $module = $ToolArgs[0]
    $rest = @()
    if ($ToolArgs.Length -gt 1) {
        $rest = $ToolArgs[1..($ToolArgs.Length - 1)]
    }
    & python -m $module @rest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "==> ruff check"
Invoke-PythonTool @("ruff", "check", "src", "tests")

Write-Host "==> ruff format --check"
Invoke-PythonTool @("ruff", "format", "--check", "src", "tests")

Write-Host "==> pyright (full src, matching CI)"
Invoke-PythonTool @("pyright")

Write-Host "==> pytest"
# The local test Postgres is the `realmwalkers-db` container, which publishes 5433 — NOT 5432.
# Other projects routinely run their own Postgres on 5432, so the old 5432 default silently pointed
# the suite at a foreign database instead of failing.
if (-not $env:DOMINION_TEST_DATABASE_URL) {
    $env:DOMINION_TEST_DATABASE_URL = "postgresql+asyncpg://dominion:dominion@127.0.0.1:5433/dominion_test"
}
if (-not $env:DOMINION_REQUIRE_DB) { $env:DOMINION_REQUIRE_DB = "1" }
if (-not $env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY = "sk-ant-ci-not-a-real-key" }

# Preflight mirroring scripts/verify.sh: print the resolved target, then fail loudly if nothing is
# listening on it. Reachability is not proof it is the *right* Postgres (only a real connection is,
# and conftest's DOMINION_REQUIRE_DB makes a bad one fail rather than skip) — it removes the silence.
$hostPort = ($env:DOMINION_TEST_DATABASE_URL -split "@")[-1]
$hostPort = ($hostPort -split "[/?]")[0]
$dbHost = ($hostPort -split ":")[0]
$dbPort = if ($hostPort -match ":(\d+)$") { [int]$Matches[1] } else { 5432 }
Write-Host "    test DB target: $env:DOMINION_TEST_DATABASE_URL"
$tcp = New-Object System.Net.Sockets.TcpClient
try { $tcp.Connect($dbHost, $dbPort); $reachable = $true } catch { $reachable = $false } finally { $tcp.Dispose() }
if (-not $reachable) {
    Write-Host "verify: FATAL - nothing is listening on ${dbHost}:${dbPort}."
    Write-Host "  The test Postgres is the 'realmwalkers-db' container, published on 5433."
    Write-Host "  Start it, or set DOMINION_TEST_DATABASE_URL to the correct target."
    exit 1
}

Invoke-PythonTool @("pytest", "-q", "-rs")

Write-Host "verify: all gates passed"
