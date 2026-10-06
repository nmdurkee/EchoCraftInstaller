<#
EchoCraft installer: Minecraft (Vivecraft) inside Echo VR, joining the EchoCraft Minecraft server.

What it does, in order:
  1. Checks your Echo VR install (the known final client, with the community plugin loader).
  2. Renames your Echo folder to "ready-at-dawn-echo-arena (original_backup)" and copies it back to the
     original name. EchoCraft is installed only into the copy; the backup is never touched.
  3. Downloads Prism Launcher, Java 21, Python 3.14, Fabric API and Vivecraft from their official sources
     (each checked against a pinned SHA-256) into %LOCALAPPDATA%\EchoCraft\app.
  4. Builds the EchoCraft map patch from YOUR Echo files and installs it into the copy.
  5. Installs the EchoCraft Echo plugins and turns on Echo's local API.
  6. Opens Prism so you can sign in with your Microsoft (Minecraft) account and run Minecraft once.

Uninstall: Uninstall-EchoCraft.cmd (deletes the modified copy and renames the backup back).
#>
[CmdletBinding()]
param([switch]$Uninstall, [string]$UserData)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Step([string]$text) { Write-Host ''; Write-Host "==> $text" -ForegroundColor Cyan }
function Info([string]$text) { Write-Host "    $text" }
function Warn([string]$text) { Write-Host "    WARNING: $text" -ForegroundColor Yellow }
function Ask([string]$question) { (Read-Host "    $question [y/N]") -match '^(y|yes)$' }
function WriteText([string]$path, [string]$text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false))) }

# ---- Elevation: Echo lives under Program Files. Keep the signed-in user's LOCALAPPDATA across the UAC prompt. ----
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $UserData) { $UserData = $env:LOCALAPPDATA }
if (-not $admin) {
    # Run elevated in its own window and keep it open at the end so messages can be read.
    $quote = { param($s) "'" + $s.Replace("'", "''") + "'" }
    $call = "& $(& $quote $PSCommandPath) -UserData $(& $quote $UserData)"
    if ($Uninstall) { $call += ' -Uninstall' }
    $call += "; `$code = `$LASTEXITCODE; Read-Host 'Press Enter to close this window'; exit `$code"
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $call)
    Write-Host 'Asking Windows for administrator rights (Echo is installed under Program Files)...'
    $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $process.ExitCode
}

$here = $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $here 'installer.json') -Raw | ConvertFrom-Json
$echo = $config.echo.path
$backupName = (Split-Path $echo -Leaf) + ' (original_backup)'
$backup = Join-Path (Split-Path $echo -Parent) $backupName
$local = Join-Path $UserData 'EchoCraft'
$app = Join-Path $local 'app'

function Assert-Closed {
    $running = @(Get-Process -Name echovr, javaw, java, prismlauncher -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'echovr' -or $_.Name -eq 'prismlauncher' -or ($_.Path -and $_.Path.StartsWith($app, [StringComparison]::OrdinalIgnoreCase)) })
    if ($running.Count) { throw "Close these first: $(($running | ForEach-Object Name | Sort-Object -Unique) -join ', ')." }
}

function Copy-EchoFromBackup {
    Info "Copying $backupName -> $(Split-Path $echo -Leaf) (several GB, this takes a few minutes)..."
    & robocopy.exe $backup $echo /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying Echo failed (robocopy code $LASTEXITCODE)." }
}

function Remove-EchoCopy {
    # Only ever delete the working copy, and only while the untouched backup is intact next to it.
    if (-not (Test-Path -LiteralPath (Join-Path $backup 'bin\win10\echovr.exe'))) { throw 'The backup is missing echovr.exe; refusing to delete the working copy.' }
    if ([IO.Path]::GetFullPath($echo) -ne [IO.Path]::GetFullPath($config.echo.path)) { throw 'Unexpected Echo path.' }
    if (Test-Path -LiteralPath $echo) { Remove-Item -LiteralPath $echo -Recurse -Force }
}

# ================================================= Uninstall ======================================================
if ($Uninstall) {
    Step 'Uninstalling EchoCraft'
    Assert-Closed
    if (Test-Path -LiteralPath $backup) {
        if (-not (Ask "Delete the EchoCraft copy of Echo and restore '$backupName'?")) { Info 'Nothing changed.'; exit 0 }
        Remove-EchoCopy
        Rename-Item -LiteralPath $backup -NewName (Split-Path $echo -Leaf)
        Info 'Your original Echo folder is back in place.'
    } else { Warn "No '$backupName' folder found; Echo was left as it is." }
    if ((Test-Path -LiteralPath $local) -and (Ask "Also delete $local (Prism, Minecraft downloads, your Prism sign-in)?")) {
        Remove-Item -LiteralPath $local -Recurse -Force
        Info 'EchoCraft data removed.'
    }
    Write-Host ''; Write-Host 'EchoCraft uninstalled.' -ForegroundColor Green
    exit 0
}

try {
# ================================================= 1. Checks ======================================================
Step 'Checking your Echo VR install'
Assert-Closed
$installedBefore = Test-Path -LiteralPath $backup
$source = if ($installedBefore) { $backup } else { $echo }
$exe = Join-Path $source 'bin\win10\echovr.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Echo VR was not found at $source. EchoCraft needs Echo installed at the default Meta location." }
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $config.echo.exeSha256) { throw 'Your echovr.exe is not the Echo build EchoCraft was made for (the final Echo VR client).' }
$loader = Join-Path $source 'bin\win10\dbgcore.dll'
if (-not (Test-Path -LiteralPath $loader)) { throw 'No plugin loader (bin\win10\dbgcore.dll) in your Echo folder. Set up community Echo VR first (you must be able to play Echo online), then run this again.' }
if ((Get-FileHash -LiteralPath $loader -Algorithm SHA256).Hash -ne $config.echo.loaderSha256) {
    Warn 'Your Echo plugin loader (dbgcore.dll) is a different version than the one EchoCraft was tested with. EchoCraft may not load.'
    if (-not (Ask 'Continue anyway?')) { exit 1 }
}
Info 'Echo VR found and recognised.'

# ================================================= 2. Backup + copy ===============================================
Step 'Backing up Echo'
if ($installedBefore) {
    Info "Found the backup from an earlier install: $backup"
    if (-not (Ask 'Replace the current EchoCraft copy with a fresh copy of the backup? (needed to reinstall)')) { exit 1 }
    Remove-EchoCopy
    Copy-EchoFromBackup
} else {
    $size = (Get-ChildItem -LiteralPath $echo -Recurse -File -Force | Measure-Object Length -Sum).Sum
    $free = (Get-PSDrive -Name ($echo.Substring(0, 1))).Free
    Info ("Echo uses {0:N1} GB; {1:N1} GB free." -f ($size / 1GB), ($free / 1GB))
    if ($free -lt $size + 2GB) { throw 'Not enough free disk space for a full copy of Echo.' }
    try { Rename-Item -LiteralPath $echo -NewName $backupName }
    catch { throw "Could not rename the Echo folder. Close the Meta Horizon app and anything using Echo, then try again. ($($_.Exception.Message))" }
    Info "Original renamed to: $backup"
    try { Copy-EchoFromBackup }
    catch {
        Warn 'Copy failed; putting your original folder back.'
        if (Test-Path -LiteralPath $echo) { Remove-Item -LiteralPath $echo -Recurse -Force }
        Rename-Item -LiteralPath $backup -NewName (Split-Path $echo -Leaf)
        throw
    }
}
if ((Get-FileHash -LiteralPath (Join-Path $echo 'bin\win10\echovr.exe') -Algorithm SHA256).Hash -ne $config.echo.exeSha256) { throw 'The Echo copy did not verify.' }
Info "Working copy ready: $echo"

# ================================================= 3. Downloads ===================================================
Step 'Downloading Prism Launcher, Java, Python, Fabric API and Vivecraft'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$cache = Join-Path $local 'downloads'
New-Item -ItemType Directory -Force -Path $cache, $app | Out-Null
function Get-Pinned([string]$name) {
    $item = $config.downloads.$name
    $file = Join-Path $cache ($name + [IO.Path]::GetExtension(([Uri]$item.url).AbsolutePath))
    if (-not (Test-Path -LiteralPath $file) -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $item.sha256) {
        Info "Downloading $name..."
        Invoke-WebRequest -Uri $item.url -OutFile $file -UseBasicParsing
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $item.sha256) { Remove-Item -LiteralPath $file; throw "$name download did not match its pinned SHA-256." }
    } else { Info "$name already downloaded." }
    return $file
}
$prismZip = Get-Pinned 'prism'; $javaZip = Get-Pinned 'java'; $pythonZip = Get-Pinned 'python'
$fabricJar = Get-Pinned 'fabricApi'; $vivecraftJar = Get-Pinned 'vivecraft'

# ================================================= 4. App folder ==================================================
Step "Installing EchoCraft into $app"
Get-ChildItem -LiteralPath $here -Recurse -File | Unblock-File
$python = Join-Path $app 'python'
if (-not (Test-Path -LiteralPath (Join-Path $python 'python.exe'))) { Expand-Archive -LiteralPath $pythonZip -DestinationPath $python -Force }
$pth = Get-ChildItem -LiteralPath $python -Filter 'python*._pth' | Select-Object -First 1
WriteText $pth.FullName ("$($pth.BaseName).zip`r`n.`r`n..\tools`r`n")  # + EchoCraft's tools folder on sys.path
$javaRoot = Join-Path $app 'java'
if (-not (Get-ChildItem -LiteralPath $javaRoot -Recurse -Filter javaw.exe -ErrorAction SilentlyContinue)) { Expand-Archive -LiteralPath $javaZip -DestinationPath $javaRoot -Force }
$javaw = (Get-ChildItem -LiteralPath $javaRoot -Recurse -Filter javaw.exe | Select-Object -First 1).FullName
$prism = Join-Path $app 'client\PrismLauncher'
if (-not (Test-Path -LiteralPath (Join-Path $prism 'prismlauncher.exe'))) { Expand-Archive -LiteralPath $prismZip -DestinationPath $prism -Force }
if (-not (Test-Path -LiteralPath (Join-Path $prism 'portable.txt'))) { WriteText (Join-Path $prism 'portable.txt') 'portable' }
$tools = Join-Path $app 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
Copy-Item -Path (Join-Path $here 'payload\tools\*.py') -Destination $tools -Force
$mapInput = Join-Path $app 'runtime\world-replacement'
# The Echo copy is always fresh at this point, so any earlier map-patch record belongs to a deleted copy.
if (Test-Path -LiteralPath $mapInput) { Remove-Item -LiteralPath $mapInput -Recurse -Force }
New-Item -ItemType Directory -Force -Path $mapInput | Out-Null
Copy-Item -Path (Join-Path $here 'payload\map\*.json') -Destination $mapInput -Force

# Prism: launcher defaults, the EchoCraft instance (Minecraft 1.21.1 + Fabric), mods and configs.
$javaCfg = $javaw.Replace('\', '/')
$prismCfg = Join-Path $prism 'prismlauncher.cfg'
if (-not (Test-Path -LiteralPath $prismCfg)) { WriteText $prismCfg "[General]`r`nConfigVersion=1.3`r`nLanguage=en_US`r`nUseSystemLocale=true`r`nJavaPath=$javaCfg`r`nMinMemAlloc=1024`r`nMaxMemAlloc=4096`r`nSelectedInstance=EchoCraft`r`n" }
$instance = Join-Path $prism 'instances\EchoCraft'
$dotMinecraft = Join-Path $instance '.minecraft'
New-Item -ItemType Directory -Force -Path (Join-Path $dotMinecraft 'mods'), (Join-Path $dotMinecraft 'config') | Out-Null
WriteText (Join-Path $instance 'instance.cfg') "[General]`r`nname=EchoCraft`r`nInstanceType=OneSix`r`nOverrideJavaLocation=true`r`nJavaPath=$javaCfg`r`nOverrideMemory=true`r`nMinMemAlloc=1024`r`nMaxMemAlloc=4096`r`niconKey=default`r`nConfigVersion=1.3`r`n"
Copy-Item -LiteralPath (Join-Path $here 'payload\minecraft\mmc-pack.json') -Destination $instance -Force
$mods = Join-Path $dotMinecraft 'mods'
Get-ChildItem -LiteralPath $mods -Filter *.jar | Remove-Item -Force
Copy-Item -LiteralPath (Join-Path $here 'payload\minecraft\mods\echocraft-0.1.0-dev.jar') -Destination $mods
Copy-Item -LiteralPath $fabricJar -Destination (Join-Path $mods $config.downloads.fabricApi.file)
Copy-Item -LiteralPath $vivecraftJar -Destination (Join-Path $mods $config.downloads.vivecraft.file)
Copy-Item -LiteralPath (Join-Path $here 'payload\minecraft\vivecraft-client-config.json') -Destination (Join-Path $dotMinecraft 'config') -Force

# Echo <-> Minecraft exchange files.
$observation = Join-Path $app 'runtime\client-observation'
$transfer = Join-Path $local 'render-transfer'
New-Item -ItemType Directory -Force -Path $observation, $transfer | Out-Null
$m = $config.fixedCoordinates.Split(' ', [StringSplitOptions]::RemoveEmptyEntries)
if ($m.Count -ne 6) { throw 'installer.json fixedCoordinates must be "ox oy oz fx fy fz".' }
$alignment = "0 0 0 $($m[0]) $($m[1]) $($m[2]) 0 0 -1 $($m[3]) $($m[4]) $($m[5])"
WriteText (Join-Path $local 'fixed-coordinates.enabled') $config.fixedCoordinates
WriteText (Join-Path $local 'live-world-alignment.txt') $alignment
WriteText (Join-Path $observation 'live-world-alignment.txt') $alignment
WriteText (Join-Path $observation 'live-world.enabled') ''
WriteText (Join-Path $observation 'vivecraft-ui.path') (Join-Path $transfer 'gui.bin')
WriteText (Join-Path $observation 'engine-command.txt') ("`"$(Join-Path $python 'pythonw.exe')`" `"$(Join-Path $tools 'engine_host.py')`"")

function Write-EngineConfig([bool]$enabled) {
    $engine = [ordered]@{
        enabled = $enabled
        echoTrackingFile = (Join-Path $observation 'controller-state.json')
        echoExecutable = (Join-Path $echo 'bin\win10\echovr.exe')
        vivecraftMode = 'echo-ui'
        vivecraftSurfaceFile = (Join-Path $transfer 'gui.bin')
        gameMode = 'survival'
        servers = @($config.minecraftServers)
    }
    WriteText (Join-Path $dotMinecraft 'config\echocraft-engine.json') ($engine | ConvertTo-Json -Depth 4)
}
Write-EngineConfig $false  # a normal, visible Minecraft until the first launch has downloaded everything

# ================================================= 5. Map patch ===================================================
Step 'Building the EchoCraft map patch from your Echo files'
& (Join-Path $python 'python.exe') (Join-Path $tools 'build_client_map.py')
if ($LASTEXITCODE -ne 0) { throw 'The map patch failed (see the message above). Echo was not changed beyond the copy.' }

# ================================================= 6. Echo plugins + API ==========================================
Step 'Installing the Echo plugins'
$plugins = Join-Path $echo 'bin\win10\plugins'
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
# Echo's loader loads EVERY file in plugins\, so never leave old EchoCraft copies there.
Get-ChildItem -LiteralPath $plugins -Filter 'EchoCraft*' | Remove-Item -Force
foreach ($name in 'EchoCraftClient.dll', 'EchoCraftClient-LICENSE.txt', 'EchoCraftPhysics.dll') {
    Copy-Item -LiteralPath (Join-Path $here "payload\echo-plugins\$name") -Destination $plugins
}
WriteText (Join-Path $plugins 'EchoCraftClient.path') $observation
WriteText (Join-Path $plugins 'EchoCraftPhysics.request') 'live-collision-v5'
Info "Plugins installed in $plugins"

$settings = Join-Path $UserData 'rad\loneecho\settings_mp_v2.json'
if (Test-Path -LiteralPath $settings) {
    $json = Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json
    if (-not $json.game) { $json | Add-Member -NotePropertyName game -NotePropertyValue ([pscustomobject]@{}) }
    if ($json.game.EnableAPIAccess -ne $true) {
        Copy-Item -LiteralPath $settings -Destination "$settings.before-echocraft" -Force
        $json.game | Add-Member -NotePropertyName EnableAPIAccess -NotePropertyValue $true -Force
        WriteText $settings ($json | ConvertTo-Json -Depth 10)
        Info 'Turned on Echo''s local API (Settings > Game > API Access).'
    } else { Info 'Echo''s local API is already on.' }
} else {
    Warn 'Echo''s settings file was not found. Start Echo once, then turn on Settings > Game > "API Access" in Echo.'
}

# ================================================= 7. Minecraft sign-in ===========================================
Step 'Minecraft sign-in'
Info 'Prism Launcher is opening. In Prism:'
Info '  1. Click the account button (top right) > Manage Accounts > Add Microsoft, and sign in with the'
Info '     Microsoft account that owns Minecraft Java Edition.'
Info '  2. Select the "EchoCraft" instance and click Launch. Wait for the Minecraft title screen'
Info '     (the first launch downloads Minecraft), then close Minecraft and Prism.'
Start-Process explorer.exe -ArgumentList "`"$(Join-Path $prism 'prismlauncher.exe')`""   # not as administrator
$accounts = Join-Path $prism 'accounts.json'
while ($true) {
    Read-Host '    Press Enter once you have signed in, launched Minecraft once and closed it' | Out-Null
    $signedIn = (Test-Path -LiteralPath $accounts) -and @((Get-Content -LiteralPath $accounts -Raw | ConvertFrom-Json).accounts).Count -gt 0
    $downloaded = Test-Path -LiteralPath (Join-Path $prism 'libraries\com\mojang\minecraft\1.21.1')
    if ($signedIn -and $downloaded) { break }
    if (-not $signedIn) { Warn 'No Microsoft account found in Prism yet.' }
    if (-not $downloaded) { Warn 'Minecraft 1.21.1 has not been downloaded yet (launch the EchoCraft instance once).' }
    if (Ask 'Skip this check and finish anyway?') { break }
}
Assert-Closed
Write-EngineConfig $true
Info 'Minecraft is now set to run hidden inside Echo and join the EchoCraft server.'

Write-Host ''
Write-Host 'EchoCraft is installed.' -ForegroundColor Green
Info 'Join the EchoCraft Echo server the usual way (Discord / Spark link). Echo starts Minecraft by itself;'
Info 'look around for a few seconds after loading in while the world locks on.'
Info "Your original Echo is kept at: $backup"
} catch {
    Write-Host ''
    Write-Host "Install stopped: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
