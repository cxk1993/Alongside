@echo off
REM Alongside nssm install - run as Administrator
REM ASCII only. No spaces in nssm env values except PSK token.
setlocal
set ROOT=E:\ai work\work\life
set NSSM=%ROOT%\deploy\tools\nssm\nssm.exe
set BRIDGE_CMD=%ROOT%\services\bridge\run-bridge-service.cmd
set FRPC_CMD=%ROOT%\deploy\tools\frp\run-frpc-service.cmd
set STARTUP=C:\Users\lcyovo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup

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

echo ==== reconfigure lifeos-bridge ====
"%NSSM%" stop lifeos-bridge >nul 2>&1
"%NSSM%" set lifeos-bridge Application "%BRIDGE_CMD%"
"%NSSM%" reset lifeos-bridge AppParameters
"%NSSM%" set lifeos-bridge AppDirectory "%ROOT%\services\bridge"
"%NSSM%" set lifeos-bridge AppEnvironmentExtra BRIDGE_PSK=%BRIDGE_PSK% BRIDGE_PORT=8790
"%NSSM%" set lifeos-bridge Start SERVICE_AUTO_START
"%NSSM%" set lifeos-bridge AppStdout "%ROOT%\services\bridge\nssm-out.log"
"%NSSM%" set lifeos-bridge AppStderr "%ROOT%\services\bridge\nssm-err.log"
"%NSSM%" set lifeos-bridge AppRestartDelay 3000

echo ==== reconfigure lifeos-frpc ====
"%NSSM%" stop lifeos-frpc >nul 2>&1
"%NSSM%" set lifeos-frpc Application "%FRPC_CMD%"
"%NSSM%" reset lifeos-frpc AppParameters
"%NSSM%" set lifeos-frpc AppDirectory "%ROOT%\deploy\tools\frp"
"%NSSM%" set lifeos-frpc Start SERVICE_AUTO_START
"%NSSM%" set lifeos-frpc AppStdout "%ROOT%\deploy\tools\frp\nssm-out.log"
"%NSSM%" set lifeos-frpc AppStderr "%ROOT%\deploy\tools\frp\nssm-err.log"
"%NSSM%" set lifeos-frpc AppRestartDelay 3000

echo ==== free ports before start ====
taskkill /F /IM frpc.exe >nul 2>&1
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 2 /nobreak >nul

echo ==== start ====
"%NSSM%" resume lifeos-bridge >nul 2>&1
"%NSSM%" resume lifeos-frpc >nul 2>&1
"%NSSM%" start lifeos-bridge
"%NSSM%" start lifeos-frpc
timeout /t 5 /nobreak >nul

echo ==== status ====
"%NSSM%" status lifeos-bridge
"%NSSM%" status lifeos-frpc
sc query lifeos-bridge | findstr STATE
sc query lifeos-frpc | findstr STATE
powershell -NoProfile -Command "(Test-NetConnection 127.0.0.1 -Port 8790 -WarningAction SilentlyContinue).TcpTestSucceeded"

if exist "%STARTUP%\lifeos-bridge.cmd" del /f /q "%STARTUP%\lifeos-bridge.cmd"
if exist "%STARTUP%\lifeos-frpc.cmd" del /f /q "%STARTUP%\lifeos-frpc.cmd"

echo Done.
endlocal
