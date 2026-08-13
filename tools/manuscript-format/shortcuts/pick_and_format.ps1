# Double-click entry point for the manuscript formatter.
#
# Pick one or more source files, or an entire manuscript folder. Multiple sources can be assembled
# into one book before export. The Python merger preserves semantic structure, recognizes authored
# front/back matter, sorts filenames naturally, and keeps chapter/source prose intact.

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms | Out-Null

$pythonExe = $null
$pythonPrefix = @()
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $pythonExe = $pythonCommand.Source
} else {
    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand) {
        $pythonExe = $pyCommand.Source
        $pythonPrefix = @('-3')
    }
}
if (-not $pythonExe) {
    throw 'Python 3 was not found. Install Python 3, enable Add Python to PATH, then run this launcher again.'
}

$tool     = Split-Path -Parent $PSScriptRoot
$chapters = Join-Path (Split-Path -Parent (Split-Path -Parent $tool)) 'book1\manuscript'

function Write-Rule { param([string]$Text = '')
    Write-Host ''
    if ($Text) { Write-Host "  $Text" -ForegroundColor Cyan }
    Write-Host '  ---------------------------------------------------------------'
}

function Common-Parent { param([string[]]$Paths)
    if ($Paths.Count -eq 1) { return Split-Path -Parent $Paths[0] }
    $parts = $Paths | ForEach-Object { (Split-Path -Parent $_).TrimEnd('\').Split('\') }
    $limit = ($parts | ForEach-Object Count | Measure-Object -Minimum).Minimum
    $common = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $limit; $i++) {
        $candidate = $parts[0][$i]
        if (($parts | Where-Object { $_[$i] -ne $candidate }).Count -gt 0) { break }
        $common.Add($candidate)
    }
    if ($common.Count -eq 0) { return Split-Path -Parent $Paths[0] }
    return ($common -join '\')
}

# ---- 1. choose files or folder ---------------------------------------------

Write-Rule 'What are you formatting?'
Write-Host ''
Write-Host '    1   Select one or more manuscript files'
Write-Host '    2   Select a manuscript folder (scans subfolders too)'
Write-Host ''
do { $sourceChoice = (Read-Host '  Type 1 or 2 and press Enter').Trim() }
until ($sourceChoice -in @('1', '2'))

$files = @()
$sourceRoot = $null
if ($sourceChoice -eq '2') {
    $folder = New-Object System.Windows.Forms.FolderBrowserDialog
    $folder.Description = 'Pick the manuscript folder. Front matter, chapters, and back matter can be in subfolders.'
    $folder.SelectedPath = if (Test-Path $chapters) { $chapters } else { [Environment]::GetFolderPath('Desktop') }
    if ($folder.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
    $sourceRoot = $folder.SelectedPath
    $files = @($sourceRoot)
} else {
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Title = 'Pick manuscript source files - Ctrl+click for more than one'
    $dlg.InitialDirectory = if (Test-Path $chapters) { $chapters } else { [Environment]::GetFolderPath('Desktop') }
    $dlg.Filter = 'Manuscript files (*.md;*.markdown;*.txt;*.docx)|*.md;*.markdown;*.txt;*.docx|All files (*.*)|*.*'
    $dlg.Multiselect = $true
    if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
    $files = @($dlg.FileNames)
}

# ---- 2. choose assembly mode ------------------------------------------------

$merge = $sourceChoice -eq '2' -or $files.Count -gt 1
if ($files.Count -gt 1) {
    Write-Rule 'How should the selected files be handled?'
    Write-Host ''
    Write-Host '    1   Merge into one manuscript'
    Write-Host '    2   Format each file separately'
    Write-Host ''
    do { $mergeChoice = (Read-Host '  Type 1 or 2 and press Enter').Trim() }
    until ($mergeChoice -in @('1', '2'))
    $merge = $mergeChoice -eq '1'
}

$title = $null
$author = $null
if ($merge) {
    $outDir = if ($sourceRoot) { $sourceRoot } else { Common-Parent $files }
    $defaultTitle = 'Realmwalkers I: Threadbound'
    $defaultAuthor = 'Nalakram'
    Write-Rule 'Manuscript identity'
    $entered = Read-Host "  Title [$defaultTitle]"
    $title = if ([string]::IsNullOrWhiteSpace($entered)) { $defaultTitle } else { $entered.Trim() }
    $enteredAuthor = Read-Host "  Written By [$defaultAuthor]"
    $author = if ([string]::IsNullOrWhiteSpace($enteredAuthor)) { $defaultAuthor } else { $enteredAuthor.Trim() }
} else {
    $outDir = Split-Path -Parent $files[0]
}

# ---- 3. pick the format ----------------------------------------------------

Write-Rule 'What do you want to make?'
Write-Host ''
Write-Host '    1   Book manuscript  title page, Contents, new-page chapters, LitRPG panels' -ForegroundColor Green
Write-Host '    2   Submission       Courier, double-spaced, for agents and editors'
Write-Host '    3   Book + Submission'
Write-Host '    4   Markdown         semantic .md with structure comments'
Write-Host '    5   Reference doc    flat canon document; NOT a book manuscript' -ForegroundColor Yellow
Write-Host '    6   Everything       the .reader.docx file is the book manuscript'
Write-Host ''

$map = @{
    '1' = @('reader'); '2' = @('shunn'); '3' = @('reader', 'shunn'); '4' = @('md')
    '5' = @('doc'); '6' = @('reader', 'shunn', 'doc', 'md')
}
do { $choice = (Read-Host '  Type a number and press Enter').Trim() }
until ($map.ContainsKey($choice))
$formats = $map[$choice]

if ($merge -and $formats -contains 'doc') {
    Write-Host ''
    Write-Host '  Note: the .reference.docx output is a flat reference document.' -ForegroundColor Yellow
    Write-Host '        Open the .reader.docx output for the actual book manuscript.' -ForegroundColor Yellow
}

# ---- 4. run ----------------------------------------------------------------

$made = New-Object System.Collections.Generic.List[string]
$failed = 0

Push-Location $tool
if ($merge) {
    Write-Rule "Merging sources -> $($formats -join ', ')"
    $before = @{}
    Get-ChildItem $outDir -File -ErrorAction SilentlyContinue | ForEach-Object { $before[$_.FullName] = $_.LastWriteTimeUtc.Ticks }
    $argsList = New-Object System.Collections.Generic.List[string]
    $files | ForEach-Object { $argsList.Add($_) }
    $argsList.Add('--to'); $argsList.Add(($formats -join ','))
    $argsList.Add('-o'); $argsList.Add($outDir)
    $argsList.Add('--title'); $argsList.Add($title)
    if ($author) { $argsList.Add('--author'); $argsList.Add($author) }
    $argsList.Add('--book-front-matter')
    $argsList.Add('--no-half-title')
    & $pythonExe @pythonPrefix -m manuscript_format @argsList
    if ($LASTEXITCODE -ne 0) {
        $failed++
    } else {
        Get-ChildItem $outDir -File | Where-Object {
            -not $before.ContainsKey($_.FullName) -or $before[$_.FullName] -ne $_.LastWriteTimeUtc.Ticks
        } | ForEach-Object { $made.Add($_.FullName) }
    }
} else {
    foreach ($in in $files) {
        $fileOut = Split-Path -Parent $in
        foreach ($fmt in $formats) {
            Write-Rule "$(Split-Path -Leaf $in)  ->  $fmt"
            $before = @{}
            Get-ChildItem $fileOut -File -ErrorAction SilentlyContinue | ForEach-Object { $before[$_.FullName] = $_.LastWriteTimeUtc.Ticks }
            & $pythonExe @pythonPrefix -m manuscript_format "$in" --to $fmt -o "$fileOut"
            if ($LASTEXITCODE -ne 0) { $failed++; continue }
            Get-ChildItem $fileOut -File | Where-Object {
                -not $before.ContainsKey($_.FullName) -or $before[$_.FullName] -ne $_.LastWriteTimeUtc.Ticks
            } | ForEach-Object { $made.Add($_.FullName) }
        }
    }
}
Pop-Location

# ---- 5. report -------------------------------------------------------------

Write-Rule
if ($made.Count -gt 0) {
    $madeUnique = @($made | Select-Object -Unique)
    $preferred = $madeUnique | Where-Object { $_ -like '*.reader.docx' } | Select-Object -First 1
    if (-not $preferred) { $preferred = $madeUnique | Where-Object { $_ -like '*.shunn.docx' } | Select-Object -First 1 }
    if (-not $preferred) { $preferred = $madeUnique | Where-Object { $_ -like '*.md' } | Select-Object -First 1 }
    if (-not $preferred) { $preferred = $madeUnique | Select-Object -First 1 }

    Write-Host ''
    Write-Host "  Made $($madeUnique.Count) file(s):" -ForegroundColor Green
    foreach ($path in $madeUnique) {
        $leaf = Split-Path -Leaf $path
        if ($path -eq $preferred) {
            Write-Host "      OPEN THIS: $leaf" -ForegroundColor Green
        } else {
            Write-Host "                 $leaf"
        }
    }
    Write-Host ''
    Write-Host "  In: $(Split-Path -Parent $preferred)"
    Start-Process explorer.exe -ArgumentList "/select,`"$preferred`""
}
if ($failed -gt 0) {
    Write-Host ''
    Write-Host "  $failed operation(s) did not work - the error is above." -ForegroundColor Red
}
Write-Host ''
[void](Read-Host '  Press Enter to close')
