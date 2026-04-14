# birber

Watches a bird feeder camera, identifies species, and logs what shows up.

Uses YOLOv8 to detect birds in the frame, then runs them through an EfficientNetB2 classifier trained on 525 species. When a bird is identified, it saves the frame, logs it to SQLite, and fires events (webhook, MQTT, WebSocket). There's a web UI with a live stream and detection feed, and it can push through a Cloudflare tunnel so you can check on your feeder from anywhere.

## How it works

```
Camera → Capture Card → ffmpeg → MediaMTX (RTSP)
                                      ↓
                              Docker container:
                              1. Read frame
                              2. Motion detection (skip static frames)
                              3. YOLOv8n: is there a bird?
                              4. EfficientNetB2: what species?
                              5. Vote tracker (consensus over multiple frames)
                              6. Log to SQLite, save frame, fire events
                              7. Push annotated stream back to MediaMTX
```

The annotated stream has bounding boxes and species labels drawn on it. When the camera is off or showing a no-signal screen, it switches to an info screen showing recent sightings.

## Setup

You need Docker and ffmpeg.

```bash
cp config.example.yaml config.yaml  # edit this
```

Create a `.env` file:
```
TUNNEL_TOKEN=            # Cloudflare tunnel token (optional)
BIRBER_STREAM_KEY=       # RTMP stream key (optional)
BIRBER_CAPTURE_URL=      # network camera URL, used when not using --capture
```

### Windows

```
.\start.bat --gpu --tunnel --capture
```

### Linux

```
chmod +x start.sh
./start.sh --tunnel --capture
```

### Flags

| Flag | What it does |
|------|-------------|
| `--gpu` | CUDA acceleration (NVIDIA only) |
| `--tunnel` | Starts Cloudflare tunnel |
| `--capture` | Runs local ffmpeg capture from the capture card into MediaMTX |
| `--stream` | Pushes the annotated feed to an RTMP server |

Without `--capture`, it reads from `BIRBER_CAPTURE_URL` (e.g. a network camera or Pi streaming MJPEG).

### Stop

Windows: `.\stop.bat`
Linux: `./stop.sh`

## Config

`config.yaml` controls everything. See `config.example.yaml` for the full reference.

Key things to customize:

- **`capture.device_name`** — your capture card's name (check `ffmpeg -list_devices true -f dshow -i dummy` on Windows, `v4l2-ctl --list-devices` on Linux)
- **`classification.regional_species`** — birds common in your area get a confidence boost so the model doesn't confuse your house sparrows for McKay's buntings
- **`capture.kasa_plug_ip`** — if your camera has an auto-shutoff (like Canon DSLRs with the 30-minute limit), put it on a Kasa smart plug and birber will power-cycle it on a timer
- **`events.*`** — configure webhook URLs, MQTT broker, or WebSocket port

## Web UI

Once running, open `http://localhost:8080`:

- `/` — live stream with detection feed
- `/embed` — bare video player (for OBS/Streamlabs browser source)
- `/review` — label crops for training data (keyboard-driven: Enter to confirm, 1-5 to pick alternatives, N for not-a-bird)
- `/api/sightings` — JSON API (`?limit=50&offset=0&species=House Sparrow`)
- `/api/summary` — species counts
- `/ws` — WebSocket for real-time events

## Network camera (no capture card)

If you don't want to use a capture card, you can stream from a Raspberry Pi or any device running a webcam:

```bash
# On the Pi, install ustreamer:
sudo apt install ustreamer

# Start streaming:
ustreamer --device /dev/video0 --host 0.0.0.0 --port 8080 --format MJPEG --resolution 1920x1080
```

Set `BIRBER_CAPTURE_URL=http://<pi-ip>:8080/stream` in `.env` and run without `--capture`.

## Classification accuracy

The EfficientNetB2 model was trained on curated bird photos, not compressed video from capture cards. It's not great at single-frame classification from a camera feed. To compensate:

- **Regional boosting** — species in your `regional_species` list get a 3x confidence multiplier, so the model prefers plausible local birds over exotic lookalikes
- **Vote tracking** — instead of trusting any single frame, birber accumulates votes across multiple frames and only reports a species when there's consensus (default: 5 votes, 50% agreement)
- **Training data collection** — every crop the classifier sees is saved to `data/crops/`. Use the `/review` page to label them. Once you have enough labeled data, you can fine-tune the model on your specific camera setup.

## Project structure

```
├── start.bat / start.sh       # start everything
├── stop.bat / stop.sh         # stop everything
├── config.example.yaml        # config template
├── docker-compose.yml         # main services
├── docker-compose.gpu.yml     # GPU overlay
├── docker-compose.tunnel.yml  # Cloudflare tunnel overlay
├── Dockerfile
├── host/
│   ├── mediamtx.yml           # RTSP server config
│   ├── start-capture.bat/sh   # host-side ffmpeg capture
├── src/
│   ├── main.py                # capture loop + ML pipeline
│   ├── capture.py             # RTSP/HTTP frame capture
│   ├── motion.py              # background subtraction
│   ├── detector.py            # YOLOv8 bird detection
│   ├── classifier.py          # species classification + regional boost
│   ├── database.py            # SQLite sightings log
│   ├── storage.py             # frame saving
│   ├── stream.py              # RTSP + RTMP output
│   ├── web.py                 # web UI, API, HLS proxy
│   └── events/                # webhook, MQTT, WebSocket emitters
└── data/
    ├── sightings.db           # SQLite database
    ├── captures/              # saved frames with bounding boxes
    └── crops/                 # classifier crops for training
```
