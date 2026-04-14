@echo off
REM Birber — one command to start everything.
REM
REM Usage:
REM   start.bat --gpu --tunnel                   (Pi Zero camera)
REM   start.bat --gpu --tunnel --capture          (Elgato capture card)
REM   start.bat --gpu --tunnel --capture --stream --key=YOUR_KEY  (Elgato + RTMP)

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Ensure data directories exist
if not exist data\captures mkdir data\captures
if not exist data\crops mkdir data\crops

REM Stop any existing containers first
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.tunnel.yml down 2>nul

set COMPOSE_FILES=-f docker-compose.yml
set CAPTURE=0
set "BIRBER_RTMP_ENABLED="

for %%a in (%*) do (
    if /i "%%a"=="--gpu" set COMPOSE_FILES=!COMPOSE_FILES! -f docker-compose.gpu.yml
    if /i "%%a"=="--tunnel" set COMPOSE_FILES=!COMPOSE_FILES! -f docker-compose.tunnel.yml
    if /i "%%a"=="--capture" set CAPTURE=1
    if /i "%%a"=="--stream" set BIRBER_RTMP_ENABLED=1
    echo %%a | findstr /B /C:"--key=" >nul && for /f "tokens=1,* delims==" %%x in ("%%a") do set "BIRBER_STREAM_KEY=%%y"
)

echo %COMPOSE_FILES% | findstr /C:"gpu" >nul && echo GPU mode enabled.
echo %COMPOSE_FILES% | findstr /C:"tunnel" >nul && echo Tunnel mode enabled.
if defined BIRBER_RTMP_ENABLED echo RTMP streaming enabled.

if %CAPTURE%==1 (
    echo Source: Elgato capture card
    set BIRBER_CAPTURE_URL=rtsp://mediamtx:8554/birdcam
) else (
    if not defined BIRBER_CAPTURE_URL (
        echo ERROR: No capture source set. Use --capture for Elgato, or set BIRBER_CAPTURE_URL in .env
        exit /b 1
    )
    echo Source: Network camera at %BIRBER_CAPTURE_URL%
)

echo Starting Docker services...
docker compose %COMPOSE_FILES% up -d --build

if %CAPTURE%==0 (
    echo.
    echo Docker services started. No local capture [using network camera].
    exit /b 0
)

echo.

REM === Host capture (Elgato) ===
set DEVICE=Elgato HD60 X
set WIDTH=1920
set HEIGHT=1080
set FPS=30
set PRESET=veryfast
set CRF=20
set TUNE=zerolatency

if exist config.yaml (
    for /f "tokens=1,* delims=: " %%a in (config.yaml) do (
        if "%%a"=="device_name" set "DEVICE=%%b"
        if "%%a"=="width" set "WIDTH=%%b"
        if "%%a"=="height" set "HEIGHT=%%b"
        if "%%a"=="framerate" set "FPS=%%b"
        if "%%a"=="preset" set "PRESET=%%b"
        if "%%a"=="crf" set "CRF=%%b"
        if "%%a"=="tune" set "TUNE=%%b"
    )
    set "DEVICE=!DEVICE:"=!"
    set "PRESET=!PRESET:"=!"
    set "TUNE=!TUNE:"=!"
)

echo Device:  %DEVICE%
echo Quality: preset=%PRESET% crf=%CRF% tune=%TUNE%
echo.

echo Waiting for MediaMTX to be ready...
:wait_loop
curl -s http://localhost:9997/v3/paths/list >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

set TUNE_FLAG=
if not "%TUNE%"=="" set TUNE_FLAG=-tune %TUNE%

echo Starting ffmpeg capture from %DEVICE%...
ffmpeg -f dshow -video_size %WIDTH%x%HEIGHT% -framerate %FPS% ^
  -i video="%DEVICE%" ^
  -pix_fmt yuv420p ^
  -c:v libx264 -preset %PRESET% -crf %CRF% %TUNE_FLAG% ^
  -f rtsp -rtsp_transport tcp rtsp://localhost:8554/birdcam
