#!/usr/bin/env bash
# Pushes a capture device to MediaMTX (running in Docker).
# Reads encoding settings from config.yaml.
#
# To list available V4L2 devices:
#   v4l2-ctl --list-devices

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# Defaults
DEVICE="/dev/video0"
WIDTH=1920
HEIGHT=1080
FPS=30
PRESET="veryfast"
CRF=20
TUNE="zerolatency"

# Parse from config.yaml if present
if [ -f config.yaml ]; then
    parse() { grep -m1 "^  $1:" config.yaml | sed 's/.*: *"\?\([^"]*\)"\?/\1/' ; }
    [ -n "$(parse width)" ]     && WIDTH="$(parse width)"
    [ -n "$(parse height)" ]    && HEIGHT="$(parse height)"
    [ -n "$(parse framerate)" ] && FPS="$(parse framerate)"
    [ -n "$(parse preset)" ]    && PRESET="$(parse preset)"
    [ -n "$(parse crf)" ]       && CRF="$(parse crf)"
    [ -n "$(parse tune)" ]      && TUNE="$(parse tune)"
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

echo "Starting ffmpeg capture..."
ffmpeg -f v4l2 -video_size "${WIDTH}x${HEIGHT}" -framerate "$FPS" \
  -i "$DEVICE" \
  -pix_fmt yuv420p \
  -c:v libx264 -preset "$PRESET" -crf "$CRF" $TUNE_FLAG \
  -f rtsp -rtsp_transport tcp rtsp://localhost:8554/birdcam
