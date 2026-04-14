#!/usr/bin/env bash
cd "$(dirname "$0")"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.tunnel.yml down 2>/dev/null
echo "Stopped."
