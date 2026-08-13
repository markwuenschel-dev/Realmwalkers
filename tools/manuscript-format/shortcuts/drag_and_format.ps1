param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('reader', 'shunn', 'doc', 'md')]
    [string]$Format,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Inputs
)

$tool = Split-Path -Parent $PSScriptRoot
if (-not $Inputs -or $Inputs.Count -eq 0) {
    Write-Host ''
    Write-Host '  Drop one or more manuscript files, or a manuscript folder, onto this .bat.'
    Write-Host ''
    [void](Read-Host '  Press Enter to close')
    exit 1
}

$resolved = @($Inputs | ForEach-Object { (Resolve-Path $_).Path })
$first = $resolved[0]
$outDir = if (Test-Path $first -PathType Container) { $first } else { Split-Path -Parent $first }
$merge = $resolved.Count -gt 1 -or (Test-Path $first -PathType Container)

Push-Location $tool
$argsList = New-Object System.Collections.Generic.List[string]
$resolved | ForEach-Object { $argsList.Add($_) }
$argsList.Add('--to'); $argsList.Add($Format)
$argsList.Add('-o'); $argsList.Add($outDir)
if ($merge) {
    $title = Split-Path -Leaf $outDir
    if ([string]::IsNullOrWhiteSpace($title)) { $title = 'Untitled Manuscript' }
    $argsList.Add('--title'); $argsList.Add($title)
    $argsList.Add('--book-front-matter')
}
& python -m manuscript_format @argsList
$exitCode = $LASTEXITCODE
Pop-Location

Write-Host ''
if ($exitCode -eq 0) {
    Write-Host "  Done. Output saved to: $outDir" -ForegroundColor Green
} else {
    Write-Host '  Formatting failed. The error is printed above.' -ForegroundColor Red
}
Write-Host ''
[void](Read-Host '  Press Enter to close')
exit $exitCode
