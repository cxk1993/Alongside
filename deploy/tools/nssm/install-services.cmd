@echo off
REM Alongside nssm install - run as Administrator
REM ASCII only. No spaces in nssm env values except PSK token.
setlocal
REM Repo root = three levels up from this script (deploy\tools\nssm -> repo root),
REM so this works on any machine without editing paths.
pushd "%~dp0..\..\.." & set "ROOT=%CD%" & popd
set NSSM=%ROOT%\deploy\tools\nssm\nssm.exe
set BRIDGE_CMD=%ROOT%\services\bridge\run-bridge-service.cmd
set FRPC_CMD=%ROOT%\deploy\tools\frp\run-frpc-service.cmd
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

if not exist "%NSSM%" echo [err] missing nssm & exit /b 1
if not exist "%BRIDGE_CMD%" echo [err] missing bridge cmd & exit /b 1
if not exist "%FRPC_CMD%" echo [err] missing frpc cmd & exit /b 1

set BRIDGE_PSK=
for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
  if /I "%%A"=="BRIDGE_PSK" set "BRIDGE_PSK=%%B"
)
if not defined BRIDGE_PSK (
  echo [err] BRIDGE_PSK missing in .env
  exit /b 1
)
echo PSK ready.

REM free ports
taskkill /F /IM frpc.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'serve\\.py|bridge\\.main' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo ==== reconfigure alongside-bridge ====
"%NSSM%" stop alongside-bridge >nul 2>&1
"%NSSM%" set alongside-bridge Application "%BRIDGE_CMD%"
"%NSSM%" reset alongside-bridge AppParameters
"%NSSM%" set alongside-bridge AppDirectory "%ROOT%\services\bridge"
"%NSSM%" set alongside-bridge AppEnvironmentExtra BRIDGE_PSK=%BRIDGE_PSK% BRIDGE_PORT=8790
"%NSSM%" set alongside-bridge Start SERVICE_AUTO_START
"%NSSM%" set alongside-bridge AppStdout "%ROOT%\services\bridge\nssm-out.log"
"%NSSM%" set alongside-bridge AppStderr "%ROOT%\services\bridge\nssm-err.log"
"%NSSM%" set alongside-bridge AppRestartDelay 3000

echo ==== reconfigure alongside-frpc ====
"%NSSM%" stop alongside-frpc >nul 2>&1
"%NSSM%" set alongside-frpc Application "%FRPC_CMD%"
"%NSSM%" reset alongside-frpc AppParameters
"%NSSM%" set alongside-frpc AppDirectory "%ROOT%\deploy\tools\frp"
"%NSSM%" set alongside-frpc Start SERVICE_AUTO_START
"%NSSM%" set alongside-frpc AppStdout "%ROOT%\deploy\tools\frp\nssm-out.log"
"%NSSM%" set alongside-frpc AppStderr "%ROOT%\deploy\tools\frp\nssm-err.log"
"%NSSM%" set alongside-frpc AppRestartDelay 3000

echo ==== free ports before start ====
taskkill /F /IM frpc.exe >nul 2>&1
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 2 /nobreak >nul

echo ==== start ====
"%NSSM%" resume alongside-bridge >nul 2>&1
"%NSSM%" resume alongside-frpc >nul 2>&1
"%NSSM%" start alongside-bridge
"%NSSM%" start alongside-frpc
timeout /t 5 /nobreak >nul

echo ==== status ====
"%NSSM%" status alongside-bridge
"%NSSM%" status alongside-frpc
sc query alongside-bridge | findstr STATE
sc query alongside-frpc | findstr STATE
powershell -NoProfile -Command "(Test-NetConnection 127.0.0.1 -Port 8790 -WarningAction SilentlyContinue).TcpTestSucceeded"

if exist "%STARTUP%\alongside-bridge.cmd" del /f /q "%STARTUP%\alongside-bridge.cmd"
if exist "%STARTUP%\alongside-frpc.cmd" del /f /q "%STARTUP%\alongside-frpc.cmd"

echo Done.
endlocal
