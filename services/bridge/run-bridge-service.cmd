@echo off
REM Alongside note bridge (nssm Application points here)
REM BRIDGE_PSK comes from nssm AppEnvironmentExtra
setlocal
cd /d "E:\ai work\work\life\services\bridge"
if not defined BRIDGE_PSK (
  echo BRIDGE_PSK is not set. 1>&2
  exit /b 1
)
if not defined BRIDGE_PORT set BRIDGE_PORT=8790
if not defined BRIDGE_CONFIG set BRIDGE_CONFIG=E:\ai work\work\life\services\bridge\config.yaml
"E:\ai work\work\life\services\api\.venv\Scripts\python.exe" "E:\ai work\work\life\services\bridge\serve.py"
endlocal
