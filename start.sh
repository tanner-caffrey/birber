#!/usr/bin/env bash
# Birber — one command to start everything.
#
# Usage:
#   ./start.sh --gpu --tunnel                   (Pi Zero camera)
#   ./start.sh --gpu --tunnel --capture          (capture card)
#   ./start.sh --gpu --tunnel --capture --stream  (+ RTMP streaming)

set -e
cd "$(dirname "$0")"

# Ensure data directories exist
mkdir -p data/captures data/crops

# Stop any existing containers first
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.tunnel.yml down 2>/dev/null || true

COMPOSE_FILES="-f docker-compose.yml"
CAPTURE=0
export BIRBER_RTMP_ENABLED=""
export BIRBER_CAPTURE_URL=""

for arg in "$@"; do
    case "$arg" in
        --gpu)     COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.gpu.yml" ;;
        --tunnel)  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.tunnel.yml" ;;
        --capture) CAPTURE=1 ;;
        --stream)  export BIRBER_RTMP_ENABLED=1 ;;
        --key=*)   export BIRBER_STREAM_KEY="${arg#--key=}" ;;
    esac
done

echo "$COMPOSE_FILES" | grep -q "gpu" && echo "GPU mode enabled."
echo "$COMPOSE_FILES" | grep -q "tunnel" && echo "Tunnel mode enabled."
[ -n "$BIRBER_RTMP_ENABLED" ] && echo "RTMP streaming enabled."

if [ "$CAPTURE" -eq 1 ]; then
    echo "Source: Capture card"
    export BIRBER_CAPTURE_URL="rtsp://mediamtx:8554/birdcam"
else
    if [ -z "$BIRBER_CAPTURE_URL" ]; then
        echo "ERROR: No capture source set. Use --capture for Elgato, or set BIRBER_CAPTURE_URL in .env"
        exit 1
    fi
    echo "Source: Network camera at $BIRBER_CAPTURE_URL"
fi

echo "Starting Docker services..."
docker compose $COMPOSE_FILES up -d --build

if [ "$CAPTURE" -eq 0 ]; then
    echo
    echo "Docker services started. No local capture (using network camera)."
    exit 0
fi

echo

# Read capture settings from config.yaml
DEVICE="Elgato HD60 X"
WIDTH=1920
HEIGHT=1080
FPS=30
PRESET="veryfast"
CRF=20
TUNE="zerolatency"

if [ -f config.yaml ]; then
    parse() { grep -m1 "^  $1:" config.yaml | sed 's/.*: *"\?\([^"]*\)"\?/\1/'; }
    [ -n "$(parse device_name)" ] && DEVICE="$(parse device_name)"
    [ -n "$(parse width)" ]       && WIDTH="$(parse width)"
    [ -n "$(parse height)" ]      && HEIGHT="$(parse height)"
    [ -n "$(parse framerate)" ]   && FPS="$(parse framerate)"
    [ -n "$(parse preset)" ]      && PRESET="$(parse preset)"
    [ -n "$(parse crf)" ]         && CRF="$(parse crf)"
    [ -n "$(parse tune)" ]        && TUNE="$(parse tune)"
fi

echo "Device:  $DEVICE"
echo "Quality: preset=$PRESET crf=$CRF tune=$TUNE"
echo

echo "Waiting for MediaMTX to be ready..."
until curl -s http://localhost:9997/v3/paths/list >/dev/null 2>&1; do
    sleep 1
done

TUNE_FLAG=""
[ -n "$TUNE" ] && TUNE_FLAG="-tune $TUNE"

# Detect capture device (Linux uses V4L2, not DirectShow)
if [ -e /dev/video0 ]; then
    DEVICE_INPUT="-f v4l2 -i /dev/video0"
else
    echo "No video device found at /dev/video0"
    echo "List devices with: v4l2-ctl --list-devices"
    exit 1
fi

echo "Starting ffmpeg capture..."
ffmpeg $DEVICE_INPUT \
  -video_size "${WIDTH}x${HEIGHT}" -framerate "$FPS" \
  -pix_fmt yuv420p \
  -c:v libx264 -preset "$PRESET" -crf "$CRF" $TUNE_FLAG \
  -f rtsp -rtsp_transport tcp rtsp://localhost:8554/birdcam
