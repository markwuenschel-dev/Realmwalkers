#requires -Version 7
$ErrorActionPreference = 'Stop'

$Engine = 'C:\Program Files\Epic Games\UE_5.7'
$Proj   = 'C:\Users\Nalakram\Documents\Unreal Projects\Gloamstead\Gloamstead.uproject'
$Target = 'GloamsteadEditor'
$Filter = 'Gloamstead.Spine'
$Report = Join-Path $env:TEMP 'GloamsteadGate'

function Fail($m) { Write-Host "GATE FAIL: $m" -ForegroundColor Red; exit 1 }

# 1. Build - exit code IS the oracle here.
& "$Engine\Engine\Build\BatchFiles\Build.bat" $Target Win64 Development -Project="$Proj" -WaitMutex
if ($LASTEXITCODE -ne 0) { Fail "build returned $LASTEXITCODE" }

# 2. Tests - editor-cmd exit code is unreliable; parse the report, fail closed.
if (Test-Path $Report) { Remove-Item $Report -Recurse -Force }
& "$Engine\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" $Proj `
    -ExecCmds="Automation RunTests $Filter" `
    -TestExit="Automation Test Queue Empty" `
    -unattended -nullrhi -nosplash -nopause -ReportExportPath="$Report" | Out-Null

$index = Join-Path $Report 'index.json'
if (-not (Test-Path $index)) { Fail "no report written - runner never produced results" }

$r     = Get-Content $index -Raw | ConvertFrom-Json
$tests = @($r.tests)
if ($tests.Count -eq 0) { Fail "report has zero tests - filter matched nothing" }

$bad = $tests | Where-Object { $_.state -ne 'Success' }
if ($bad) {
    $bad | ForEach-Object { Write-Host "  FAILED: $($_.fullTestPath) [$($_.state)]" -ForegroundColor Red }
    Fail "$($bad.Count)/$($tests.Count) test(s) not green"
}

Write-Host "GATE PASS: build green, $($tests.Count) test(s) green" -ForegroundColor Green
exit 0
