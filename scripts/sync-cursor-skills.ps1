# Copy .agents/skills/ -> .cursor/skills/ for Cursor's slash menu.
# Run locally after pull or skill updates. Do not commit .cursor/skills/.
#Requires -Version 7.0
$ErrorActionPreference = 'Stop'

$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$src = Join-Path $root '.agents/skills'
$dst = Join-Path $root '.cursor/skills'

if (-not (Test-Path $src)) {
    throw "Missing skills source: $src"
}

New-Item -ItemType Directory -Force -Path $dst | Out-Null

$count = 0
Get-ChildItem -Path $src -Directory | ForEach-Object {
    $target = Join-Path $dst $_.Name
    if (Test-Path $target) {
        Remove-Item -Recurse -Force $target
    }
    Copy-Item -Path $_.FullName -Destination $target -Recurse -Force
    $count++
}

Write-Host "Synced $count skills to .cursor/skills/"
