# Hosts a network room for Monster Hunter 4 Ultimate's online play with Azahar's dedicated room
# server (azahar-room.exe, in the MH4U PC port's Windows package; keep this script next to it or
# in a folder inside the package). Players join with Multiplayer > Direct Connect to Room, or
# find the room in Multiplayer > Browse Public Rooms when it is listed in a lobby. All the game
# traffic goes through the room, over UDP (port 24872 unless you change it): forward that port
# on your router to this PC. docs/SERVER.md in the repository has the details.
#
# Settings: host-room.ini in %APPDATA%\MH4U Server (made with the defaults on the first run;
# edit it with Notepad). key=value arguments override it for one run:
#   "Host room.bat" name="Luis's hall" password=secret
#   powershell -ExecutionPolicy Bypass -File host-room.ps1 port=24873 [-NoPause]
# Close the room with q and Enter in its window: it then leaves the lobby and saves its ban
# list. (Closing the window works too, but forgets the bans made since it started.)
param([switch]$NoPause)
$ErrorActionPreference = "Stop"

function Finish($code) {
    if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
    exit $code
}

# One argument for a program's command line, by the C runtime's rules (CommandLineToArgvW):
# Windows PowerShell 5.1 doesn't escape a " inside an argument it passes, so a name or password
# with one would come apart
function ConvertTo-Argument([string]$text) {
    if ($text -and $text -notmatch '[\s"]') { return $text }
    $text = [regex]::Replace($text, '(\\*)"', { param($m) $m.Groups[1].Value * 2 + '\"' })
    $text = [regex]::Replace($text, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    return '"' + $text + '"'
}

# azahar-room.exe: next to this script, or in the folder above it
$room = $null
foreach ($dir in @($PSScriptRoot, (Split-Path $PSScriptRoot -Parent))) {
    $candidate = Join-Path $dir "azahar-room.exe"
    if (Test-Path -LiteralPath $candidate) { $room = $candidate; break }
}
if (-not $room) {
    Write-Host "Can't find azahar-room.exe next to this script or in the folder above it."
    Write-Host "It comes with the MH4U PC port's Windows package: keep this script in that folder."
    Finish 1
}

$folder = $env:MH4U_SERVER_DIR
if (-not $folder) { $folder = Join-Path $env:APPDATA "MH4U Server" }
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$ini = Join-Path $folder "host-room.ini"
if (-not (Test-Path -LiteralPath $ini)) {
    Set-Content -LiteralPath $ini -Encoding UTF8 -Value @(
        "# Settings for ""Host room.bat"": an MH4U room on Azahar's dedicated room server.",
        "# One key = value per line; delete a line to go back to its default.",
        "name = MH4U Gathering Hall",
        "description =",
        "# the room's UDP port: forward it on your router to this PC",
        "port = 24872",
        "max_members = 16",
        "# empty: no password",
        "password =",
        "# To list the room in a lobby (Multiplayer > Browse Public Rooms): the lobby's address",
        "# (http://...:24872) and the room's name and token there. In an open lobby the first",
        "# use claims the name; use your own Azahar username to be the room's moderator.",
        "lobby_url =",
        "username =",
        "token =",
        "# the room's bans, kept between runs (in this folder unless it's a full path)",
        "ban_list = room-ban-list.txt")
    Write-Host "Made $ini with the defaults: edit it (Notepad) to change the room."
}

$settings = @{ name = "MH4U Gathering Hall"; description = ""; port = "24872"; max_members = "16";
               password = ""; lobby_url = ""; username = ""; token = ""; ban_list = "room-ban-list.txt" }
function Set-Setting([string]$text) {
    $eq = $text.IndexOf("=")
    if ($eq -lt 1) { return $false }
    $key = $text.Substring(0, $eq).Trim().ToLower()
    if ($settings.ContainsKey($key)) {
        $settings[$key] = $text.Substring($eq + 1).Trim()
    } else {
        Write-Host "Unknown setting: $key"
    }
    return $true
}
foreach ($line in Get-Content -LiteralPath $ini) {
    $line = $line.Trim()
    if ($line -and -not $line.StartsWith("#")) { Set-Setting $line | Out-Null }
}
foreach ($arg in $args) {
    if (-not (Set-Setting "$arg")) { Write-Host "Not a setting: $arg (key=value, e.g. port=24873)"; Finish 1 }
}

$port = 0; $members = 0
if (-not $settings.name) { Write-Host "The room needs a name (name = ...)"; Finish 1 }
if (-not [int]::TryParse($settings.port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    Write-Host "port must be a number from 1 to 65535, not '$($settings.port)'"; Finish 1
}
if (-not [int]::TryParse($settings.max_members, [ref]$members) -or $members -lt 2 -or $members -gt 254) {
    Write-Host "max_members must be a number from 2 to 254, not '$($settings.max_members)'"; Finish 1
}
$banList = $settings.ban_list
if (-not [System.IO.Path]::IsPathRooted($banList)) { $banList = Join-Path $folder $banList }
if (-not (Test-Path -LiteralPath $banList)) {
    Set-Content -LiteralPath $banList -Encoding ASCII -Value "CitraRoom-BanList-1"  # an empty one
}

$roomArgs = @("--room-name", $settings.name, "--port", "$port", "--max_members", "$members",
              "--preferred-app", "Monster Hunter 4 Ultimate", "--preferred-app-id", "0004000000126300",
              "--ban-list-file", $banList)
if ($settings.description) { $roomArgs += @("--room-description", $settings.description) }
if ($settings.password) { $roomArgs += @("--password", $settings.password) }
$public = $settings.lobby_url -and $settings.username -and $settings.token
if ($public) {
    $roomArgs += @("--web-api-url", $settings.lobby_url, "--username", $settings.username,
                   "--token", $settings.token)
}

Write-Host "Room '$($settings.name)': UDP port $port, up to $members players, $(if ($settings.password) { 'with a password' } else { 'no password' })."
if ($public) {
    Write-Host "Listed in the lobby at $($settings.lobby_url) as $($settings.username): players find it in"
    Write-Host "Multiplayer > Browse Public Rooms (with that lobby as their Network Web API URL)."
} else {
    if ($settings.lobby_url) { Write-Host "(lobby_url needs username and token too, to list the room)" }
    Write-Host "Unlisted: players use Multiplayer > Direct Connect to Room, this PC's public address"
    Write-Host "(or its address in a virtual network such as Tailscale) and port $port."
}
try {
    $lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | ForEach-Object { $_.IPAddress }
    if ($lan) { Write-Host "This PC on its network: $($lan -join ', ') (forward UDP $port on the router to it)." }
} catch { }
Write-Host "If Windows asks, allow azahar-room.exe through the firewall. Settings: $ini"
Write-Host ""

# in this window, so that q and Enter here close it
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $room
$start.Arguments = ($roomArgs | ForEach-Object { ConvertTo-Argument $_ }) -join " "
$start.UseShellExecute = $false
$process = [System.Diagnostics.Process]::Start($start)
$process.WaitForExit()
$code = $process.ExitCode
Write-Host ""
Write-Host "The room has closed (code $code)."
Finish $code
