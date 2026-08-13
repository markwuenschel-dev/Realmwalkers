# Creates real .bat drop targets on the Desktop.
# Each drop target accepts one file, several files, or a folder. Multiple sources are assembled into
# one manuscript before export; a single source is formatted normally.

$runner = Join-Path $PSScriptRoot 'drag_and_format.ps1'
$dest   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Format Manuscript'

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Get-ChildItem $dest -Filter *.lnk -ErrorAction SilentlyContinue | Remove-Item -Force

$targets = @{
    'Format as BOOK'          = 'reader'
    'Format for SUBMISSION'   = 'shunn'
    'Format as REFERENCE DOC' = 'doc'
}

foreach ($name in $targets.Keys) {
    $body = @"
@echo off
rem Drop one or more manuscript files, or a manuscript folder, onto this file.
if "%~1"=="" (
    echo.
    echo   Drop one or more files, or a folder, onto this icon - do not double-click it.
    echo   Accepted: .md .markdown .txt .docx
    echo.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$runner" -Format $($targets[$name]) %*
"@
    $path = Join-Path $dest "$name.bat"
    [System.IO.File]::WriteAllText($path, $body, (New-Object System.Text.ASCIIEncoding))
    Write-Host "  wrote $path"
}

$pickerBody = @"
@echo off
title Format Manuscript
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "$(Join-Path $PSScriptRoot 'pick_and_format.ps1')"
"@
$pickerPath = Join-Path $dest 'FORMAT MANUSCRIPT.bat'
[System.IO.File]::WriteAllText($pickerPath, $pickerBody, (New-Object System.Text.ASCIIEncoding))
Write-Host "  wrote $pickerPath"

Write-Host ""
Write-Host "  Drop targets are in: $dest"
