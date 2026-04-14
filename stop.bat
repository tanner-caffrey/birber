@echo off
cd /d "%~dp0"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.tunnel.yml down 2>nul
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down 2>nul
docker compose down 2>nul
echo Stopped.
