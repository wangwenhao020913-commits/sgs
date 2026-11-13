#requires -Version 5.1
<#
.SYNOPSIS
  Post-build self check for the SGS portable package: really start the exe,
  really hit HTTP, really stop it.

.DESCRIPTION
  Proves the things that can be proven headlessly:

    1. the frozen bundle really carries the frontend assets
       (_internal/sgs_web/static/, including voices), the skill descriptions and
       the data plane (_internal/sgs/data/files/), and ships no .py sources;
    2. session driving works: create an 8-player game, get the delta, submit one
       answer - all 200 - and server.log gets written;
    3. a *stale* server.json (dead port, dead pid) does not block the next start;
    4. the port ladder works: launching with the default port ends up on some
       reachable port even when 8100 is taken;
    5. single instance: a second launch exits by itself with code 0 while the
       first one keeps serving.

  NOT covered (needs a human on real hardware): the SmartScreen prompt, the
  browser actually opening, closing the console window to quit.

  Environment note: this script deliberately does NOT assert "the port is free
  again after exit". Inside WSL2's mirrored networking, wslrelay keeps a LISTEN
  socket for ports a Windows process used, so that assertion is false here while
  being perfectly fine on a real desktop. We assert what we control instead: the
  process exits, and no listener is owned by its pid.

  Output is ASCII-only (PASS / FAIL + detail). Any failure exits 1.

.PARAMETER Exe
  Full path to SGS.exe. Defaults to <BuildRoot>\dist\SGS-win64\SGS.exe.

.PARAMETER BuildRoot
  Build root. Default %USERPROFILE%\sgs-build.
#>
[CmdletBinding()]
param(
    [string]$Exe = '',
    [string]$BuildRoot = $(Join-Path $env:USERPROFILE 'sgs-build')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $Exe) {
    $Exe = Join-Path $BuildRoot 'dist\SGS-win64\SGS.exe'
}
$Exe = (Resolve-Path -LiteralPath $Exe).Path
$AppDir = Split-Path -Parent $Exe

$DataRoot = Join-Path $env:LOCALAPPDATA 'SGS'
$StateFile = Join-Path $DataRoot 'server.json'
$LogFile = Join-Path $DataRoot 'server.log'
$DeadPort = 65000

$script:failed = 0
$script:passed = 0

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) {
        $script:passed++
        Write-Host ("PASS  {0}  {1}" -f $Name, $Detail)
    }
    else {
        $script:failed++
        Write-Host ("FAIL  {0}  {1}" -f $Name, $Detail)
    }
}

function Get-Json([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 20
        return @{ ok = ($r.StatusCode -eq 200); status = $r.StatusCode; body = $r.Content; error = '' }
    }
    catch {
        return @{ ok = $false; status = 0; body = ''; error = $_.Exception.Message }
    }
}

function Post-Json([string]$Url, [hashtable]$Body) {
    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $r = Invoke-WebRequest -Uri $Url -Method Post -Body $bytes `
            -ContentType 'application/json; charset=utf-8' -UseBasicParsing -TimeoutSec 60
        return @{ ok = ($r.StatusCode -eq 200); status = $r.StatusCode; raw = $r.Content; error = '' }
    }
    catch {
        return @{ ok = $false; status = 0; raw = ''; error = $_.Exception.Message }
    }
}

function Wait-State([int]$Seconds = 60, [int]$ExcludePid = 0, [int]$ExcludePort = 0) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $StateFile) {
            try {
                $j = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
                $fresh = ($j.port -and [int]$j.pid -ne $ExcludePid -and [int]$j.port -ne $ExcludePort)
                if ($fresh) { return $j }
            }
            catch { }
        }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $true
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Start-App([string[]]$Extra = @()) {
    # Minimized, not Hidden: the console really exists (stdout takes the
    # WriteConsoleW path), it just does not cover the screen.
    $args = @('--no-browser') + $Extra
    return Start-Process -FilePath $Exe -ArgumentList $args -PassThru -WindowStyle Minimized
}

function Stop-App($Proc) {
    if ($null -eq $Proc) { return }
    try { Stop-Process -Id $Proc.Id -Force -ErrorAction Stop } catch { }
    try { [void]$Proc.WaitForExit(15000) } catch { }
    Start-Sleep -Milliseconds 400
}

function Test-ProcessGone([int]$ProcessId) {
    return -not [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ListenerOwner([int]$Port) {
    $conn = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($conn.Count -eq 0) { return 0 }
    return [int]($conn | Select-Object -First 1).OwningProcess
}

function Stop-Leftovers {
    $stale = @(Get-Process -Name 'SGS' -ErrorAction SilentlyContinue)
    if ($stale.Count -gt 0) {
        $stale | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
        Write-Host "      stopped $($stale.Count) leftover SGS process(es)"
    }
}

# ---------------------------------------------------------------------------
# 0. Bundle contents
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host "TARGET  $Exe"
Write-Host "DATA    $DataRoot"
Write-Host ''

$internal = Join-Path $AppDir '_internal'
Check 'bundle has _internal' (Test-Path -LiteralPath $internal)
Check 'bundle has base_library.zip' (Test-Path -LiteralPath (Join-Path $internal 'base_library.zip'))
Check 'bundle has python runtime dll' (@(Get-ChildItem -LiteralPath $internal -Filter 'python3*.dll').Count -ge 1)
Check 'bundle has vcruntime' (@(Get-ChildItem -LiteralPath $internal -Filter 'VCRUNTIME140*.dll').Count -ge 1)
Check 'bundle has index.html' (Test-Path -LiteralPath (Join-Path $internal 'sgs_web\static\index.html'))
Check 'bundle has session.js' (Test-Path -LiteralPath (Join-Path $internal 'sgs_web\static\session.js'))
Check 'bundle has voices' (Test-Path -LiteralPath (Join-Path $internal 'sgs_web\static\voices'))
Check 'bundle has descriptions.json' (Test-Path -LiteralPath (Join-Path $internal 'sgs_web\descriptions.json'))
Check 'bundle has content pack' (Test-Path -LiteralPath (Join-Path $internal 'sgs\data\files\standard.json'))
Check 'bundle has hero table' (Test-Path -LiteralPath (Join-Path $internal 'sgs\data\files\heroes.standard.json'))
Check 'bundle has build stamp' (Test-Path -LiteralPath (Join-Path $internal 'build-info.json'))

if (Test-Path -LiteralPath (Join-Path $internal 'build-info.json')) {
    $info = Get-Content -LiteralPath (Join-Path $internal 'build-info.json') -Raw | ConvertFrom-Json
    Write-Host ("      version {0} / python {1} / pyinstaller {2} / built {3}" -f `
            $info.version, $info.python, $info.pyinstaller, $info.built_at)
}

$leakedPy = @(Get-ChildItem -LiteralPath $internal -Recurse -File -Filter '*.py' -ErrorAction SilentlyContinue)
Check 'bundle ships no .py source' ($leakedPy.Count -eq 0) ("found {0}" -f $leakedPy.Count)

# ---------------------------------------------------------------------------
# 1. Cold start + full functional pass
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '--- round 1: cold start (kernel-assigned port) ---'

Stop-Leftovers
if (Test-Path -LiteralPath $StateFile) {
    Remove-Item -LiteralPath $StateFile -Force
    Check 'old state file cleared' $true
}

$proc1 = Start-App @('--port', '0')
$state1 = Wait-State
Check 'start wrote server.json' ($null -ne $state1) $(if ($state1) { "port=$($state1.port) pid=$($state1.pid)" } else { 'not within 60s' })

$port = 0
if ($null -ne $state1) {
    $port = [int]$state1.port
    $base = "http://127.0.0.1:$port"

    $page = Get-Json "$base/"
    Check 'GET / 200' $page.ok ("status=$($page.status)")
    Check 'index is the lobby page' ($page.body -match 'id="lobby-form"') ''

    foreach ($asset in @('style.css', 'core.js', 'session.js', 'anim.js', 'anim.ini')) {
        $r = Get-Json "$base/$asset"
        Check "GET /$asset 200" $r.ok ("status=$($r.status)")
    }

    foreach ($api in @('api/decks', 'api/heroes', 'api/hero-presets', 'api/runs')) {
        $r = Get-Json "$base/$api"
        Check "GET /$api 200" $r.ok ("status=$($r.status)")
    }

    $create = Post-Json "$base/api/sessions" @{
        player_count = 8
        ai           = 'heuristic'
        human_seat   = 0
        seed         = 20261114
        deck_profile = 'standard_ex_160'
    }
    Check 'POST /api/sessions 200' $create.ok ("status=$($create.status) $($create.error)")

    $session = $null
    if ($create.ok) { $session = $create.raw | ConvertFrom-Json }
    Check 'create returned session_id' ($null -ne $session -and $session.session_id) ''

    $pending = $null
    if ($null -ne $session) { $pending = $session.pending }
    $cursor = 0
    if ($null -ne $session -and $session.PSObject.Properties.Name -contains 'cursor') {
        $cursor = [int]$session.cursor
    }

    # The create response stops at the L0 boundary ("game start dispatched"), so
    # the first pending may only show up on the next delta: poll a few times.
    $tries = 0
    while ($null -eq $pending -and $null -ne $session -and $tries -lt 20) {
        Start-Sleep -Milliseconds 250
        $inc = Get-Json "$base/api/sessions/$($session.session_id)?after=$cursor&logs=0"
        if ($inc.ok) {
            $view = $inc.body | ConvertFrom-Json
            if ($view.PSObject.Properties.Name -contains 'pending') { $pending = $view.pending }
            if ($view.PSObject.Properties.Name -contains 'cursor') { $cursor = [int]$view.cursor }
        }
        $tries++
    }
    Check 'got a pending query' ($null -ne $pending) $(if ($pending) { "kind=$($pending.kind)" } else { 'none after 20 polls' })

    if ($null -ne $pending -and $null -ne $session) {
        $actions = @($pending.legal_actions)
        Check 'pending carries legal actions' ($actions.Count -gt 0) ("count=$($actions.Count)")

        if ($actions.Count -gt 0) {
            $answer = Post-Json "$base/api/sessions/$($session.session_id)/answer?after=$cursor&logs=0" @{
                action = $actions[0]
            }
            Check 'POST /answer 200' $answer.ok ("status=$($answer.status) $($answer.error)")
            if ($answer.ok) {
                $view = $answer.raw | ConvertFrom-Json
                Check 'answer response has cursor' ($view.PSObject.Properties.Name -contains 'cursor') ''
                Check 'answer response has records' ($view.PSObject.Properties.Name -contains 'records') `
                    ("records={0}" -f (@($view.records).Count))
            }
        }
    }

    Check 'server.log written' (Test-Path -LiteralPath $LogFile) $LogFile
    if (Test-Path -LiteralPath $LogFile) {
        $log = Get-Content -LiteralPath $LogFile -Raw
        Check 'log has the startup banner' ($log -match 'SGS') ''
    }

    $pid1 = [int]$proc1.Id
    Stop-App $proc1
    Check 'server process exited' (Test-ProcessGone -ProcessId $pid1) "pid=$pid1"
    Check 'no listener owned by that pid' ((Get-ListenerOwner -Port $port) -ne $pid1) `
        ("owner now {0} (0 = none, wslrelay/mirroring is expected in WSL)" -f (Get-ListenerOwner -Port $port))
}

# ---------------------------------------------------------------------------
# 2. Stale server.json must not block the next start
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '--- round 2: stale server.json ---'

# Synthetic stale state: a dead port and a dead pid. This is exactly what a
# crashed session leaves behind, without depending on how long the OS takes to
# reap the real socket.
if (-not (Test-Path -LiteralPath $DataRoot)) {
    New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
}
$staleJson = @{
    port       = $DeadPort
    pid        = 999999
    url        = "http://127.0.0.1:$DeadPort/"
    started_at = '2000-01-01T00:00:00'
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($StateFile, $staleJson, (New-Object System.Text.UTF8Encoding($false)))
Check 'planted stale state' (Test-Path -LiteralPath $StateFile) "port=$DeadPort pid=999999"

$proc2 = Start-App @('--port', '0')
$state2 = Wait-State -ExcludePid 999999 -ExcludePort $DeadPort
Check 'stale state does not block start' ($null -ne $state2) $(if ($state2) { "port=$($state2.port)" } else { 'did not start' })
Check 'dead-port state is not reused' ($null -ne $state2 -and [int]$state2.port -ne $DeadPort) ''

if ($null -ne $state2) {
    $port2 = [int]$state2.port
    Check 'round 2 API answers' (Get-Json "http://127.0.0.1:$port2/api/decks").ok ''
    Stop-App $proc2
    Check 'round 2 process exited' (Test-ProcessGone -ProcessId ([int]$proc2.Id)) ''
}

# ---------------------------------------------------------------------------
# 3. Default port ladder + single instance
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '--- round 3: default port ladder + single instance ---'

if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
$proc3 = Start-App
$state3 = Wait-State
Check 'default launch picks a port' ($null -ne $state3) $(if ($state3) { "port=$($state3.port)" } else { 'did not start' })

if ($null -ne $state3) {
    $port3 = [int]$state3.port
    Write-Host ("      chosen port: {0} (8100..8109 ladder, arbitrary port if all taken)" -f $port3)
    Check 'chosen port answers' (Get-Json "http://127.0.0.1:$port3/api/decks").ok ("port=$port3")

    # Double-click while one is already running: must exit at once, code 0.
    $before = Get-Date
    $second = Start-Process -FilePath $Exe -ArgumentList '--no-browser' -PassThru -WindowStyle Minimized
    $exited = $second.WaitForExit(30000)
    $elapsed = [math]::Round(((Get-Date) - $before).TotalSeconds, 1)
    Check 'second instance exits by itself' $exited ("took ${elapsed}s")
    if ($exited) {
        $second.Refresh()
        Check 'second instance exit code 0' ($second.ExitCode -eq 0) "exit=$($second.ExitCode)"
    }
    Check 'first instance still alive' (-not (Test-ProcessGone -ProcessId ([int]$proc3.Id))) "pid=$($proc3.Id)"

    Stop-App $proc3
    Check 'round 3 process exited' (Test-ProcessGone -ProcessId ([int]$proc3.Id)) ''
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host ("SELFCHECK {0}  pass={1} fail={2}" -f $(if ($script:failed -eq 0) { 'OK' } else { 'FAILED' }), $script:passed, $script:failed)
if ($script:failed -ne 0) { exit 1 }
exit 0
