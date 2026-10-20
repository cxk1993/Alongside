@echo off
REM Alongside note bridge (nssm Application points here)
REM BRIDGE_PSK comes from nssm AppEnvironmentExtra
setlocal
REM Everything resolves relative to this script -> works on any machine.
cd /d "%~dp0"
if not defined BRIDGE_PSK (
  echo BRIDGE_PSK is not set. 1>&2
  exit /b 1
)
if not defined BRIDGE_PORT set BRIDGE_PORT=8790
if not defined BRIDGE_CONFIG set "BRIDGE_CONFIG=%~dp0config.yaml"
"%~dp0..\api\.venv\Scripts\python.exe" "%~dp0serve.py"
endlocal
