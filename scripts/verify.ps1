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
if (-not $env:DOMINION_TEST_DATABASE_URL) {
    $env:DOMINION_TEST_DATABASE_URL = "postgresql+asyncpg://dominion:dominion@127.0.0.1:5432/dominion_test"
}
if (-not $env:DOMINION_REQUIRE_DB) { $env:DOMINION_REQUIRE_DB = "1" }
if (-not $env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY = "sk-ant-ci-not-a-real-key" }
Invoke-PythonTool @("pytest", "-q", "-rs")

Write-Host "verify: all gates passed"
