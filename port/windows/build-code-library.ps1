# Builds the game's code library from YOUR Monster Hunter 4 Ultimate USA v1.3.0 update (.cia)
# and installs it where this Azahar looks for it (%APPDATA%\Azahar\recomp), then installs the
# update in Azahar as well. The first time it downloads the C compiler (llvm-mingw, about
# 180 MB). Everything it makes from the game stays on this PC. Close Azahar before running it.
# Part of the MH4U PC port's Windows package (see README-mh4u.txt).
#
# Double-click "Build code library.bat", or:
#   powershell -ExecutionPolicy Bypass -File build-code-library.ps1 [-Cia "path\to\update.cia"]
param([string]$Cia = "", [switch]$NoPause)
$ErrorActionPreference = "Stop"

function Finish($code) {
    if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
    exit $code
}

$tool = Join-Path $PSScriptRoot "mh4u-tools\tools\build_game.py"
if (-not (Test-Path $tool)) { Write-Host "Can't find $tool (keep this script next to the mh4u-tools folder)"; Finish 1 }

# Python: the "py" launcher from python.org, or a "python" that really runs (the Microsoft
# Store alias without Python installed doesn't)
$python = $null
foreach ($candidate in @(@("py", "-3"), @("python"))) {
    if (Get-Command $candidate[0] -ErrorAction SilentlyContinue) {
        $exe = $candidate[0]
        $pre = @($candidate | Select-Object -Skip 1)
        try { & $exe @pre --version *> $null } catch { continue }
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    }
}
if (-not $python) {
    Write-Host "Python 3.10 or newer is needed: install it from https://www.python.org/downloads/"
    Write-Host "(tick 'Add python.exe to PATH') and run this again."
    Finish 1
}

if (-not $Cia) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Your decrypted MH4U USA v1.3.0 update (.cia)"
    $dialog.Filter = "CIA files (*.cia)|*.cia|All files (*.*)|*.*"
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host "No file chosen."; Finish 1 }
    $Cia = $dialog.FileName
}
if (-not (Test-Path $Cia)) { Write-Host "Can't find $Cia"; Finish 1 }

$exe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1) + @($tool, "--dump", $Cia, "--install", "--fetch-compiler")
Write-Host "Building the code library from $Cia"
& $exe @pyArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Something went wrong (code $LASTEXITCODE): see the text above."
    Finish 1
}

$userDir = Join-Path $env:APPDATA "Azahar"

# The update into Azahar too (what File > Install CIA... does), with the port's runner next to
# this script; without it, or if that fails, the player installs it in Azahar
$updateInstalled = $false
$runner = Join-Path $PSScriptRoot "mh4u_runner.exe"
if (Test-Path $runner) {
    Write-Host "Installing the update in Azahar ($userDir)"
    & $runner --user $userDir --install $Cia
    $updateInstalled = ($LASTEXITCODE -eq 0)
    if (-not $updateInstalled) { Write-Host "That didn't work (code $LASTEXITCODE): install it in Azahar instead (below)." }
}

# The port's options, if there is no mh4u.ini yet (docs/PLAYING.md in the repository has them all)
$ini = Join-Path $userDir "mh4u.ini"
if (-not (Test-Path $ini)) {
    New-Item -ItemType Directory -Force -Path $userDir | Out-Null
    Set-Content -Path $ini -Value @(
        "# MH4U PC port options (docs/PLAYING.md in the repository). Delete a line to go back to the game's own.",
        "fps = 60",
        "widescreen = 16:9",
        "wide_render = 1",
        "# the touch screen in a corner of View > Screen Layout > Custom Layout (corner, height %, opacity %)",
        "touch_overlay = top-right 30 85",
        "# no dither grid over an upscaled picture",
        "dither = off",
        "# no glow over bright parts, and sharp cutscenes (emulation gets their focus blur wrong)",
        "bloom = off",
        "depth_of_field = off")
    Write-Host "Made $ini (60 fps, 16:9, the touch screen in a corner)"
}
# Azahar's own settings, only if it has none yet: the custom layout, which the port draws with
# the game filling the window and the touch screen in a corner
$qtini = Join-Path $userDir "config\qt-config.ini"
if (-not (Test-Path $qtini)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $qtini) | Out-Null
    Set-Content -Path $qtini -Value @(
        "[Layout]",
        "layout_option\default=false",
        "layout_option=6")
    Write-Host "Screen layout: Custom (the game fills the window, the touch screen in a corner)"
}

Write-Host ""
Write-Host "Done. In azahar.exe (next to this script):"
if (-not $updateInstalled) { Write-Host "  - File > Install CIA... > your v1.3.0 update (.cia)" }
Write-Host "  - File > Load File... > your decrypted game (.3ds / .cci / .cxi)"
Write-Host "  - Tools > MH4U Port Options... for the frame rate, widescreen and the rest"
Write-Host "  Azahar says so at the start if the game runs without the port's code library."
Finish 0
