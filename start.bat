@echo off
setlocal

rem ============================================================
rem  UND Cortex - start script (Windows)
rem  Usage:
rem    start.bat            - start base services only
rem    start.bat --gpu      - include vLLM, requires NVIDIA GPU
rem ============================================================

set "PROFILE_ARG="
set "WAIT_TIMEOUT=180"
if /I "%~1"=="--gpu" set "PROFILE_ARG=--profile gpu"
if /I "%~1"=="--gpu" set "WAIT_TIMEOUT=900"

cd /d "%~dp0"

echo [1/5] Checking Docker Desktop...
docker info > nul 2>&1
if errorlevel 1 goto :ERR_DOCKER
echo   OK

echo [2/5] Checking .env file...
if not exist ".env" goto :ENV_MISSING
echo   OK
goto :ENV_DONE
:ENV_MISSING
if not exist ".env.example" goto :ERR_ENV_EXAMPLE
copy /Y ".env.example" ".env" > nul
echo   .env created from .env.example. Please review the values.
:ENV_DONE

echo [3/5] Pulling external images...
rem Images that can no longer be pulled (e.g. minio/minio removed from Docker Hub)
rem fall back to the local copy. If an image is missing locally too, step 5/5 fails.
docker compose %PROFILE_ARG% pull --ignore-buildable --ignore-pull-failures
if errorlevel 1 echo   WARN: some images could not be pulled - using local images.

echo [4/5] Building local images...
docker compose %PROFILE_ARG% build
if errorlevel 1 goto :ERR_BUILD

echo [5/5] Starting containers, waiting for healthy, timeout %WAIT_TIMEOUT%s...
docker compose %PROFILE_ARG% up -d --wait --wait-timeout %WAIT_TIMEOUT%
if errorlevel 1 goto :ERR_HEALTH

echo.
echo ============================================================
echo  UND Cortex is up
echo ============================================================
echo  Web UI         : http://localhost:3010
echo  Backend API    : http://localhost:8002
echo  API docs       : http://localhost:8002/docs
echo  MinIO console  : http://localhost:9001
echo  Postgres       : localhost:5432
echo  Redis          : localhost:6379
if defined PROFILE_ARG echo  vLLM           : http://localhost:8001
echo ============================================================
echo  Logs   docker compose logs -f SERVICE_NAME
echo  Stop   stop.bat        or  stop.bat --clean
echo  Smoke  powershell -ExecutionPolicy Bypass -File scripts\smoke.ps1
echo ============================================================
exit /b 0

:ERR_DOCKER
echo   ERROR: Docker Desktop is not running. Start Docker Desktop first.
exit /b 1

:ERR_ENV_EXAMPLE
echo   ERROR: .env.example not found.
exit /b 1


:ERR_BUILD
echo   ERROR: build failed.
exit /b 1

:ERR_HEALTH
echo.
echo   ERROR: not all services became healthy in time.
echo   recent logs, last 100 lines:
docker compose %PROFILE_ARG% logs --tail=100
exit /b 1
