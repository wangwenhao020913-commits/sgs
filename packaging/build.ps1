#requires -Version 5.1
<#
.SYNOPSIS
  The ONLY build entry point for the SGS portable Windows x64 package.

.DESCRIPTION
  Runs on Windows; callable from WSL via interop. It does:

    1. provision a per-user Python 3.14 inside the build root (no PATH changes,
       no all-users install);
    2. create a build venv and install pinned PyInstaller / pydantic;
    3. copy src/ tools/ packaging/ pyproject.toml into a staging tree
       (read-only copy - the repository is never modified);
    4. write build-info.json (version stamp) into the staging tree only;
    5. build with PyInstaller (onedir) -> dist\SGS\;
    6. rename to SGS-win64\, drop in the user guide / NOTICE / LICENSE;
    7. compress to dist\SGS-win64-<version>-<date>.zip.

  All intermediates live under -BuildRoot (default %USERPROFILE%\sgs-build).
  The repository only gains a gitignored dist\ holding the artifact.

  NOTE ON ENCODING: this file is deliberately ASCII-only and BOM-free.
  Windows PowerShell 5.1 decodes BOM-less scripts with the ANSI codepage, so
  UTF-8 Chinese comments become mojibake and the parser dies with an unreadable
  error. All Chinese text lives in the packaged user guide, NOTICE.txt, README.md
  and in the launcher's own console output.

.PARAMETER BuildRoot
  Build root directory. Default %USERPROFILE%\sgs-build.

.PARAMETER Version
  Version stamp baked into the package (default 'dev'). Pass the git short HEAD.

.PARAMETER PythonExe
  Pre-provisioned python.exe (3.14.x). Skips download and install when given.

.PARAMETER PythonVersion
  Python version to install when bootstrapping (default 3.14.4, matching the
  development .venv).

.PARAMETER SkipInstall
  Only use an existing interpreter; fail instead of downloading one.

.PARAMETER PrepareOnly
  Stop after Python + venv + deps (useful as a first smoke run).

.PARAMETER NoZip
  Skip the zip step; leave the folder only.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1 -Version f1839c6
#>
[CmdletBinding()]
param(
    [string]$BuildRoot = $(Join-Path $env:USERPROFILE 'sgs-build'),
    [string]$Version = 'dev',
    [string]$PythonVersion = '3.14.4',
    [string]$PythonExe = '',
    [string]$PyInstallerVersion = '6.22.3',
    [string]$HooksContribVersion = '2026.10',
    [string]$PydanticVersion = '2.13.5',
    [switch]$SkipInstall,
    [switch]$NoZip,
    [switch]$PrepareOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = Split-Path -Parent $PSScriptRoot
$Tree = Join-Path $BuildRoot 'tree'
$Dist = Join-Path $BuildRoot 'dist'
$Work = Join-Path $BuildRoot 'build'
$Venv = Join-Path $BuildRoot 'venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$FolderName = 'SGS-win64'

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text"
}

function Assert-LastExit([string]$What) {
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE)"
    }
}

# ---------------------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------------------
Write-Step 'preflight'

foreach ($required in @('src', 'tools', 'packaging')) {
    $path = Join-Path $Repo $required
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing $required under repo root: $Repo"
    }
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'This script only produces a Windows x64 package.'
}
Write-Host "   repo       $Repo"
Write-Host "   buildroot  $BuildRoot"

New-Item -ItemType Directory -Force -Path $BuildRoot, $Dist, $Work | Out-Null

# ---------------------------------------------------------------------------
# 1. Python 3.14 (per-user, installed into the build root)
# ---------------------------------------------------------------------------
Write-Step "python $PythonVersion"

$py = $PythonExe
if (-not $py) {
    $py = Join-Path (Join-Path $BuildRoot ('python' + $PythonVersion.Replace('.', ''))) 'python.exe'
}

if (-not (Test-Path -LiteralPath $py)) {
    if ($SkipInstall) {
        throw "No interpreter at $py and -SkipInstall was given."
    }
    $downloadDir = Join-Path $BuildRoot 'download'
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
    $installer = Join-Path $downloadDir "python-$PythonVersion-amd64.exe"
    if (-not (Test-Path -LiteralPath $installer)) {
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
        Write-Host "   download   $url"
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    }
    $installDir = Split-Path -Parent $py
    Write-Host "   install    $installDir (current user only, no PATH change)"
    $installArgs = @(
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=0',
        'Include_launcher=0',
        'Include_test=0',
        'Include_tcltk=0',
        'Include_doc=0',
        'Shortcuts=0',
        "TargetDir=$installDir"
    )
    $proc = Start-Process -FilePath $installer -ArgumentList $installArgs -Wait -PassThru
    if (-not (Test-Path -LiteralPath $py)) {
        throw "Python install failed (installer exit $($proc.ExitCode)). Run it manually: $installer"
    }
}

$pyVersion = & $py -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) { throw "Interpreter does not run: $py" }
if (-not $pyVersion.StartsWith('3.14.')) {
    throw "Interpreter is $pyVersion but this build pins 3.14.x (baseline: 3.14.4)."
}
Write-Host "   python     $py ($pyVersion)"

# ---------------------------------------------------------------------------
# 2. venv + pinned dependencies
# ---------------------------------------------------------------------------
Write-Step 'build venv'

if (-not (Test-Path -LiteralPath $VenvPy)) {
    & $py -m venv $Venv
    Assert-LastExit 'venv creation'
}
& $VenvPy -m pip install --disable-pip-version-check --no-input --quiet --upgrade pip
Assert-LastExit 'pip upgrade'

$pinned = @(
    "pyinstaller==$PyInstallerVersion",
    "pyinstaller-hooks-contrib==$HooksContribVersion",
    "pydantic==$PydanticVersion"
)
& $VenvPy -m pip install --disable-pip-version-check --no-input --quiet @pinned
Assert-LastExit 'dependency install'
Write-Host "   deps       $($pinned -join ', ')"

if ($PrepareOnly) {
    Write-Host ''
    Write-Host 'PREPARE_OK'
    Write-Host "PYTHON=$py"
    Write-Host "VENV=$Venv"
    exit 0
}

# ---------------------------------------------------------------------------
# 3. Staging tree (read-only copy; the repository is never touched)
# ---------------------------------------------------------------------------
Write-Step 'staging tree'

if (Test-Path -LiteralPath $Tree) {
    Remove-Item -LiteralPath $Tree -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Tree | Out-Null

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
foreach ($dir in @('src', 'tools', 'packaging')) {
    $src = Join-Path $Repo $dir
    $dst = Join-Path $Tree $dir
    robocopy $src $dst /E /XD __pycache__ /XF *.pyc *.pyo /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Copy of $dir failed (robocopy exit $LASTEXITCODE)"
    }
}
foreach ($file in @('pyproject.toml', 'LICENSE')) {
    $src = Join-Path $Repo $file
    if (Test-Path -LiteralPath $src) {
        [System.IO.File]::WriteAllText(
            (Join-Path $Tree $file),
            [System.IO.File]::ReadAllText($src),
            $utf8NoBom
        )
    }
}
Write-Host "   staging    $Tree"

# ---------------------------------------------------------------------------
# 4. build-info.json (version stamp; staging only)
# ---------------------------------------------------------------------------
Write-Step 'build stamp'

$pyinstallerReal = (& $VenvPy -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
$pydanticReal = (& $VenvPy -c "import pydantic; print(pydantic.VERSION)").Trim()
$info = [ordered]@{
    version     = $Version
    built_at    = (Get-Date -Format 'yyyy-MM-dd HH:mm')
    python      = $pyVersion
    pyinstaller = $pyinstallerReal
    hooks       = $HooksContribVersion
    pydantic    = $pydanticReal
    host        = "$env:COMPUTERNAME / $([Environment]::OSVersion.VersionString)"
}
$infoJson = $info | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText(
    (Join-Path $Tree 'packaging\build-info.json'),
    $infoJson,
    $utf8NoBom
)
Write-Host "   $($infoJson -replace "`r?`n", ' ')"

# ---------------------------------------------------------------------------
# 5. PyInstaller
# ---------------------------------------------------------------------------
Write-Step 'pyinstaller (onedir)'

Push-Location $Tree
try {
    & $VenvPy -m PyInstaller --clean --noconfirm --log-level WARN `
        --distpath $Dist --workpath $Work (Join-Path $Tree 'packaging\SGS.spec')
    Assert-LastExit 'PyInstaller'
}
finally {
    Pop-Location
}

$built = Join-Path $Dist 'SGS'
if (-not (Test-Path -LiteralPath (Join-Path $built 'SGS.exe'))) {
    throw "No SGS.exe after the build: $built"
}

# ---------------------------------------------------------------------------
# 6. Final folder layout
# ---------------------------------------------------------------------------
Write-Step 'assemble artifact'

$final = Join-Path $Dist $FolderName
if (Test-Path -LiteralPath $final) {
    Remove-Item -LiteralPath $final -Recurse -Force
}
Rename-Item -LiteralPath $built -NewName $FolderName

# The user guide is the single *.txt in packaging\ besides NOTICE.txt. It is
# looked up from the filesystem instead of being spelled out here, because this
# script must stay ASCII-only (a Chinese literal would be decoded with the ANSI
# codepage by PowerShell 5.1 and turn into mojibake).
$guides = @(Get-ChildItem -LiteralPath (Join-Path $Tree 'packaging') -Filter '*.txt' |
        Where-Object { $_.Name -ne 'NOTICE.txt' })
if ($guides.Count -ne 1) {
    throw "Expected exactly one user guide .txt in packaging\, found $($guides.Count)."
}
# Keep the BOM so classic Notepad opens the Chinese text as UTF-8.
$guideName = $guides[0].Name
$guideText = [System.IO.File]::ReadAllText($guides[0].FullName).Replace('{{VERSION}}', $Version)
[System.IO.File]::WriteAllText(
    (Join-Path $final $guideName),
    $guideText,
    (New-Object System.Text.UTF8Encoding($true))
)
Write-Host "   guide      $guideName"
foreach ($extra in @('NOTICE.txt')) {
    $from = Join-Path $Tree "packaging\$extra"
    if (Test-Path -LiteralPath $from) {
        Copy-Item -LiteralPath $from -Destination $final
    }
}
$licenseSrc = Join-Path $Tree 'LICENSE'
if (Test-Path -LiteralPath $licenseSrc) {
    Copy-Item -LiteralPath $licenseSrc -Destination (Join-Path $final 'LICENSE.txt')
}

$sum = (Get-ChildItem -LiteralPath $final -Recurse -File | Measure-Object -Property Length -Sum).Sum
$folderMb = if ($sum) { [math]::Round($sum / 1MB, 1) } else { 0 }
$exeMb = [math]::Round((Get-Item -LiteralPath (Join-Path $final 'SGS.exe')).Length / 1MB, 1)
Write-Host "   folder     $final ($folderMb MB, SGS.exe $exeMb MB)"

# ---------------------------------------------------------------------------
# 7. Zip
# ---------------------------------------------------------------------------
if (-not $NoZip) {
    Write-Step 'zip'
    $stamp = Get-Date -Format 'yyyyMMdd'
    $zip = Join-Path $Dist "$FolderName-$Version-$stamp.zip"
    if (Test-Path -LiteralPath $zip) {
        Remove-Item -LiteralPath $zip -Force
    }
    # NOT Compress-Archive, and NOT ZipFile.CreateFromDirectory either: both of
    # them write entry names with backslashes on .NET Framework
    # ("SGS-win64\_internal\..."), which is not a valid zip layout. Explorer
    # tolerates it, 7-Zip / WinRAR / unzip elsewhere do not. So build the archive
    # entry by entry with explicit "/" separators.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $base = $final.TrimEnd('\')
    $stream = [System.IO.File]::Create($zip)
    try {
        $archive = New-Object System.IO.Compression.ZipArchive(
            $stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            foreach ($file in Get-ChildItem -LiteralPath $final -Recurse -File) {
                # Keep the top-level folder inside the archive so extracting never
                # scatters SGS.exe / _internal into the user's current directory.
                $inner = $file.FullName.Substring($base.Length + 1).Replace('\', '/')
                $relative = "$FolderName/$inner"
                [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $archive, $file.FullName, $relative,
                    [System.IO.Compression.CompressionLevel]::Optimal)
            }
        }
        finally { $archive.Dispose() }
    }
    finally { $stream.Dispose() }
    $zipMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
    Write-Host "   zip        $zip ($zipMb MB)"
    Write-Host ''
    Write-Host 'BUILD_OK'
    Write-Host "ZIP=$zip"
    Write-Host "ZIP_MB=$zipMb"
    Write-Host "EXE=$(Join-Path $final 'SGS.exe')"
    Write-Host "FOLDER_MB=$folderMb"
}
else {
    Write-Host ''
    Write-Host 'BUILD_OK'
    Write-Host "EXE=$(Join-Path $final 'SGS.exe')"
    Write-Host "FOLDER_MB=$folderMb"
}
