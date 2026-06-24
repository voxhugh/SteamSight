#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Steam Portal one-key control
.DESCRIPTION
  .\steam_portal.ps1 start    启动
  .\steam_portal.ps1 stop     停止
  .\steam_portal.ps1 restart  重启
  .\steam_portal.ps1 status   状态
  .\steam_portal.ps1 log      日志 (tail -f)
  .\steam_portal.ps1 purge    清缓存 + 重启
#>

param(
  [Parameter(Position=0,Mandatory)]
  [ValidateSet('start','stop','restart','status','log','purge')]
  [string]$Action
)

# config
$ROOT    = 'C:\Users\voxhu\.qclaw\workspace\steam_portal'
$PORT    = 8766
$LOG     = "$ROOT\server.log"
$PIDFILE = "$ROOT\server.pid"
$URL     = "http://localhost:$PORT"

# colored output helpers
function C   { Write-Host " $($args -join ' ')" -ForegroundColor Cyan }
function OK  { Write-Host " [OK] $($args -join ' ')" -ForegroundColor Green }
function WRN { Write-Host " [!] $($args -join ' ')" -ForegroundColor Yellow }
function ERR { Write-Host " [X] $($args -join ' ')" -ForegroundColor Red }
function DIM { Write-Host "      $($args -join ' ')" -ForegroundColor DarkGray }

# find process listening on a port
function Get-ListenerPid($Port) {
  $line = netstat -ano 2>$null | Select-String LISTENING | Select-String ":$Port\s"
  if ($line) { return [int](($line -split '\s+')[-1]) }
  return $null
}

# pid file helpers
function Read-PidFile {
  if (Test-Path $PIDFILE) {
    $c = Get-Content $PIDFILE -Raw -ErrorAction 0
    $n = 0
    if ([int]::TryParse(($c -replace '\s',''), [ref]$n)) { return $n }
  }
  return $null
}
function Write-PidFile([int]$v) { $v.ToString() | Out-File $PIDFILE -Encoding utf8 -NoNewline }
function Remove-PidFile {
  if (Test-Path $PIDFILE) { Remove-Item $PIDFILE -Force }
}

# ======================== status ========================
if ($Action -eq 'status') {
  C "== Steam Portal =="
  $ppid = Get-ListenerPid $PORT
  if (-not $ppid) { WRN "not running (port $PORT)"; exit 0 }

  OK "running"
  try {
    $p = Get-Process -Id $ppid -ErrorAction Stop
    DIM "PID      : $($p.Id)"
    DIM "port     : $PORT"
    DIM "started  : $($p.StartTime.ToString('HH:mm:ss'))"
    DIM "uptime   : $(((Get-Date)-$p.StartTime).ToString('h\:mm'))"

    try {
      $r = Invoke-RestMethod "$URL/api/status" -TimeoutSec 5
      DIM "API      : $($r.status)"
      DIM "proxy    : $(if($r.proxy_active){'yes'}else{'no'})"
      if ($r.proxy_port) { DIM "proxy_pt : $($r.proxy_port)" }
    } catch { DIM "API      : unreachable" }
  } catch { WRN "PID $ppid not found" }

  $cf = Get-ChildItem "$ROOT\data\cache" -File -ErrorAction 0
  if ($cf) {
    $sz = '{0:N1} KB' -f (($cf | Measure-Object Length -Sum).Sum / 1KB)
    DIM "cache    : $($cf.Count) files / $sz"
  }
  exit 0
}

# ======================== stop ========================
if ($Action -eq 'stop') {
  C "stopping..."
  $targets = @()
  $saved = Read-PidFile
  if ($saved) { $targets += $saved }
  $pp = Get-ListenerPid $PORT
  if ($pp -and $pp -notin $targets) { $targets += $pp }

  if ($targets.Count -eq 0) { WRN "not running"; exit 0 }

  foreach ($ppid in $targets) {
    try {
      $p = Get-Process -Id $ppid -ErrorAction Stop
      DIM "PID $ppid -> graceful stop..."
      $p.CloseMainWindow() | Out-Null
      Wait-Process -Id $ppid -Timeout 3 -ErrorAction 0 2>$null
      if (-not $p.HasExited) { DIM "force kill..."; $p.Kill() }
      OK "PID $ppid done"
    } catch { WRN "PID $ppid gone, skipped" }
  }
  Remove-PidFile
  Start-Sleep 1
  if (Get-ListenerPid $PORT) { WRN "port $PORT still in use" }
  else { OK "port $PORT released" }
  exit 0
}

# ======================== start / purge ========================
if ($Action -in 'start','purge') {
  if ($Action -eq 'purge') {
    C "purging cache + pycache..."
    Remove-Item "$ROOT\data\*.json" -Force -ErrorAction 0
    Get-ChildItem "$ROOT\backend" -Recurse -Directory -ErrorAction 0 |
      Where-Object { $_.Name -eq '__pycache__' } |
      Remove-Item -Recurse -Force -ErrorAction 0
    if (Get-ListenerPid $PORT) { & $PSCommandPath stop }
  }

  $existing = Get-ListenerPid $PORT
  if ($existing) { WRN "already running (PID $existing)"; exit 0 }

  # python check
  try { $v = & py --version 2>&1; if ($LASTEXITCODE -ne 0) { throw } }
  catch { ERR "Python not found"; exit 1 }
  DIM "Python: $v"

  if (-not (Test-Path "$ROOT\run_server.py")) { ERR "run_server.py missing"; exit 1 }

  DIM "cleaning pycache..."
  Get-ChildItem "$ROOT\backend" -Recurse -Directory -ErrorAction 0 |
    Where-Object { $_.Name -eq '__pycache__' } |
    Remove-Item -Recurse -Force -ErrorAction 0

  DIM "starting (port $PORT)..."
  $logDir = Split-Path $LOG -Parent
  if (-not (Test-Path $logDir)) { New-Item $logDir -ItemType Directory -Force | Out-Null }

  $proc = Start-Process -FilePath py -ArgumentList "-3 run_server.py" `
    -WorkingDirectory $ROOT -WindowStyle Hidden -RedirectStandardOutput $LOG -PassThru
  Write-PidFile $proc.Id

  Write-Host "  waiting..." -NoNewline -ForegroundColor DarkGray
  $waited = 0; $ok = $false
  while ($waited -lt 45) {
    Start-Sleep 1; $waited++
    Write-Host '.' -NoNewline -ForegroundColor DarkGray
    if (Get-ListenerPid $PORT) { $ok = $true; break }
    if ($proc.HasExited) {
      Start-Sleep 0.5
      if (-not (Get-ListenerPid $PORT)) { break }
      $ok = $true; break
    }
  }
  Write-Host ''

  if ($ok) {
    $finalPid = Get-ListenerPid $PORT
    OK "Steam Portal is running"
    DIM "PID : $finalPid"
    DIM "url : $URL"
    DIM "ply : $URL/profile.html?user=76561199086286116"
    DIM "log : $LOG"
  } else {
    ERR "timeout (45s), check log:"
    if (Test-Path $LOG) { Get-Content $LOG -Tail 20 }
    exit 1
  }
  exit 0
}

# ======================== restart ========================
if ($Action -eq 'restart') {
  C "restarting..."
  & $PSCommandPath stop
  Start-Sleep 2
  & $PSCommandPath start
  exit 0
}

# ======================== log ========================
if ($Action -eq 'log') {
  if (-not (Test-Path $LOG)) { WRN "no log file yet"; exit 0 }
  DIM "Ctrl+C to exit"
  Get-Content $LOG -Tail 30 -Wait
  exit 0
}
