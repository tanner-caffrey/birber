@echo off
REM Pushes the capture card to MediaMTX (running in Docker).
REM Reads encoding settings from config.yaml.
REM Close any other apps using the capture device first.
REM
REM To list available devices:
REM   ffmpeg -list_devices true -f dshow -i dummy

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

REM Parse settings from config.yaml (defaults if not found)
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
    REM Strip surrounding quotes
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
