@echo off
setlocal

rem ============================================================
rem  UND Cortex - stop script (Windows)
rem  Usage:
rem    stop.bat           - stop containers, volumes preserved
rem    stop.bat --clean   - stop containers AND remove volumes + orphans
rem ============================================================

cd /d "%~dp0"

set "DOWN_ARGS="
set "CLEAN_FLAG=0"
if /I "%~1"=="--clean" set "DOWN_ARGS=-v --remove-orphans"
if /I "%~1"=="--clean" set "CLEAN_FLAG=1"

echo [1/2] Stopping containers...
docker compose --profile gpu down %DOWN_ARGS%
if errorlevel 1 goto :ERR_DOWN

echo [2/2] Remaining containers:
docker compose ps

echo.
echo ============================================================
echo  UND Cortex stopped
if "%CLEAN_FLAG%"=="1" echo  Volumes postgres / redis / minio / vllm / node_modules removed.
if "%CLEAN_FLAG%"=="0" echo  Volumes preserved. Use stop.bat --clean for full reset.
echo ============================================================
exit /b 0

:ERR_DOWN
echo   ERROR: down failed.
exit /b 1
