# Alongside · nssm 安装/修复本机常驻服务
# ★ 管理员 PowerShell 执行
#   cd "<repo root>"
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\deploy\tools\nssm\install-services.ps1

$ErrorActionPreference = "Stop"
# Repo root = three levels up from this script (deploy\tools\nssm -> repo root),
# so the script works on any machine without editing paths.
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$nssm = Join-Path $root "deploy\tools\nssm\nssm.exe"
$bridgeCmd = Join-Path $root "services\bridge\run-bridge-service.cmd"
$frpcCmd = Join-Path $root "deploy\tools\frp\run-frpc-service.cmd"
$envFile = Join-Path $root ".env"

if (-not (Test-Path $nssm)) { throw "missing nssm: $nssm" }
if (-not (Test-Path $bridgeCmd)) { throw "missing $bridgeCmd" }
if (-not (Test-Path $frpcCmd)) { throw "missing $frpcCmd" }

$psk = $null
foreach ($line in Get-Content $envFile -ErrorAction Stop) {
  if ($line -match '^BRIDGE_PSK=(.+)$') { $psk = $Matches[1].Trim() }
}
if (-not $psk) { throw "BRIDGE_PSK missing in $envFile" }
Write-Host "BRIDGE_PSK length: $($psk.Length)"

# 停掉手工进程，避免端口占用
Get-Process frpc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*bridge.main*' -or $_.CommandLine -like '*serve.py*' -or $_.CommandLine -like '*8790*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

function Install-Service([string]$Name, [string]$App, [string]$Args) {
  if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
    Write-Host "Install $Name -> $App"
    & $nssm install $Name $App | Out-Null
  } else {
    Write-Host "Update $Name"
    & $nssm set $Name Application $App | Out-Null
  }
  & $nssm set $Name AppParameters $Args | Out-Null
  & $nssm set $Name Start SERVICE_AUTO_START | Out-Null
  & $nssm set $Name AppExit Default Restart | Out-Null
  & $nssm set $Name AppRestartDelay 3000 | Out-Null
  & $nssm set $Name AppStopMethodConsole 5000 | Out-Null
}

Install-Service "alongside-bridge" "C:\Windows\System32\cmd.exe" "/c `"$bridgeCmd`""
& $nssm set alongside-bridge AppDirectory (Join-Path $root "services\bridge") | Out-Null
& $nssm set alongside-bridge AppEnvironmentExtra "BRIDGE_PSK=$psk" "BRIDGE_PORT=8790" "BRIDGE_CONFIG=$root\services\bridge\config.yaml" | Out-Null
& $nssm set alongside-bridge AppStdout (Join-Path $root "services\bridge\nssm-out.log") | Out-Null
& $nssm set alongside-bridge AppStderr (Join-Path $root "services\bridge\nssm-err.log") | Out-Null

Install-Service "alongside-frpc" "C:\Windows\System32\cmd.exe" "/c `"$frpcCmd`""
& $nssm set alongside-frpc AppDirectory (Join-Path $root "deploy\tools\frp") | Out-Null
& $nssm set alongside-frpc AppStdout (Join-Path $root "deploy\tools\frp\nssm-out.log") | Out-Null
& $nssm set alongside-frpc AppStderr (Join-Path $root "deploy\tools\frp\nssm-err.log") | Out-Null

foreach ($Name in @("alongside-bridge","alongside-frpc")) {
  Write-Host "Restart $Name"
  & $nssm restart $Name 2>&1 | Out-Null
  $st = & $nssm status $Name 2>&1
  if ("$st" -notmatch 'SERVICE_RUNNING') {
    Write-Host "start $Name (was $st)"
    & $nssm start $Name 2>&1 | Out-Host
  }
}

Start-Sleep -Seconds 4
Write-Host ""
Write-Host "===== STATUS ====="
Get-Service alongside-bridge, alongside-frpc | Format-Table Name,Status,StartType -AutoSize
& $nssm status alongside-bridge
& $nssm status alongside-frpc
$bp = Test-NetConnection 127.0.0.1 -Port 8790 -WarningAction SilentlyContinue
Write-Host "port 8790: $($bp.TcpTestSucceeded)"

# 清理登录启动项（服务接管后避免双开）
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
foreach ($f in @("alongside-bridge.cmd","alongside-frpc.cmd")) {
  $p = Join-Path $startup $f
  if (Test-Path $p) {
    Remove-Item $p -Force
    Write-Host "removed startup $p"
  }
}

Write-Host ""
Write-Host "若仍非 Running，请把下列日志发我："
Write-Host "  services\bridge\nssm-err.log"
Write-Host "  deploy\tools\frp\nssm-err.log"
