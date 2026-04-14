import asyncio
import json
import logging
from pathlib import Path

import aiohttp
from aiohttp import web

from .config import WebConfig
from .database import SightingsDB
from .events.base import BirdEvent

STREAM_PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Birber — Live Bird Feeder</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #1a1a2e; color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            display: flex; flex-direction: column; align-items: center;
            min-height: 100vh; padding: 1rem;
        }
        h1 { margin: 0.5rem 0; font-size: 1.5rem; color: #a8d8a8; }
        #player-wrap {
            width: 100%; max-width: 960px;
            background: #000; border-radius: 8px; overflow: hidden;
            margin: 1rem 0; aspect-ratio: 16/9; position: relative;
        }
        video, iframe { width: 100%; height: 100%; object-fit: contain; border: 0; }
        #status {
            position: absolute; bottom: 8px; left: 8px;
            background: rgba(0,0,0,0.6); padding: 4px 10px;
            border-radius: 4px; font-size: 0.8rem;
            z-index: 2;
        }
        #events {
            width: 100%; max-width: 960px;
        }
        #events h2 { font-size: 1.1rem; margin-bottom: 0.5rem; color: #a8d8a8; }
        #event-list {
            list-style: none; max-height: 300px; overflow-y: auto;
        }
        #event-list li {
            background: #16213e; padding: 0.5rem 0.75rem;
            border-radius: 4px; margin-bottom: 0.25rem;
            font-size: 0.85rem; font-family: monospace;
        }
        .species { color: #a8d8a8; font-weight: bold; }
        .confidence { color: #f0c040; }
        .time { color: #888; }
        #history-toggle {
            background: none; border: 1px solid #444; color: #aaa;
            padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer;
            font-size: 0.85rem; margin-top: 0.75rem; width: 100%;
            text-align: left;
        }
        #history-toggle:hover { border-color: #a8d8a8; color: #e0e0e0; }
        #history-toggle .arrow { display: inline-block; transition: transform 0.2s; margin-right: 0.4rem; }
        #history-toggle.open .arrow { transform: rotate(90deg); }
        #history-list {
            list-style: none; max-height: 400px; overflow-y: auto;
            display: none; margin-top: 0.25rem;
        }
        #history-list.open { display: block; }
        #history-list li {
            background: #0f1a30; padding: 0.5rem 0.75rem;
            border-radius: 4px; margin-bottom: 0.25rem;
            font-size: 0.85rem; font-family: monospace;
        }
        #load-more {
            background: none; border: 1px solid #333; color: #888;
            padding: 0.3rem 0.6rem; border-radius: 4px; cursor: pointer;
            font-size: 0.8rem; margin-top: 0.25rem; width: 100%;
        }
        #load-more:hover { border-color: #a8d8a8; color: #e0e0e0; }
    </style>
</head>
<body>
    <h1>Birber — Live Bird Feeder</h1>
    <div id="player-wrap">
        <div id="player"></div>
        <div id="status">Connecting...</div>
    </div>
    <div id="events">
        <h2>Recent Detections</h2>
        <ul id="event-list"></ul>
        <button id="history-toggle"><span class="arrow">&#9654;</span> Show Older Detections</button>
        <ul id="history-list"></ul>
        <button id="load-more" style="display:none">Load more...</button>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <script>
        const player = document.getElementById("player");
        const status = document.getElementById("status");
        const eventList = document.getElementById("event-list");

        function isPrivateHostname(hostname) {
            return hostname === "localhost"
                || hostname === "127.0.0.1"
                || hostname === "[::1]"
                || /^10\./.test(hostname)
                || /^192\.168\./.test(hostname)
                || /^172\.(1[6-9]|2\d|3[0-1])\./.test(hostname);
        }

        function preferWebRtc() {
            return location.protocol === "http:" || isPrivateHostname(location.hostname);
        }

        async function startHlsPlayback() {
            const video = document.createElement("video");
            video.autoplay = true;
            video.muted = true;
            video.playsInline = true;
            player.replaceChildren(video);

            const hlsUrl = "/hls/birber-annotated/index.m3u8";
            if (Hls.isSupported()) {
                const hls = new Hls({
                    liveSyncDurationCount: 3,
                    liveMaxLatencyDurationCount: 6,
                    maxLiveSyncPlaybackRate: 1.05,
                    lowLatencyMode: false,
                    backBufferLength: 30,
                });
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    status.textContent = "Live (HLS)";
                    video.play();
                });
                hls.on(Hls.Events.ERROR, (_, data) => {
                    if (data.fatal) {
                        status.textContent = "Reconnecting...";
                        setTimeout(() => hls.loadSource(hlsUrl), 3000);
                    }
                });
                return true;
            }
            if (video.canPlayType("application/vnd.apple.mpegurl")) {
                video.src = hlsUrl;
                video.addEventListener("loadedmetadata", () => {
                    status.textContent = "Live (HLS)";
                    video.play();
                }, { once: true });
                return true;
            }
            return false;
        }

        async function startWebRtcPlayback() {
            const webrtcPort = "8889";
            const iframe = document.createElement("iframe");
            iframe.allow = "autoplay; fullscreen; camera; microphone";
            iframe.src = `${location.protocol}//${location.hostname}:${webrtcPort}/birber-annotated`;
            player.replaceChildren(iframe);
            status.textContent = "Live (WebRTC)";

            const watchdog = setTimeout(async () => {
                console.warn("WebRTC iframe timed out, falling back to HLS");
                await startHlsPlayback();
            }, 5000);

            iframe.addEventListener("load", () => clearTimeout(watchdog), { once: true });
            return true;
        }

        async function startPlayback() {
            if (preferWebRtc()) {
                try {
                    status.textContent = "Connecting (WebRTC)...";
                    await startWebRtcPlayback();
                    return;
                } catch (err) {
                    console.warn("WebRTC failed, falling back to HLS", err);
                }
            }
            status.textContent = "Connecting (HLS)...";
            const ok = await startHlsPlayback();
            if (!ok) {
                status.textContent = "Playback unavailable";
            }
        }

        startPlayback();

        // WebSocket for live events
        function connectWS() {
            const proto = location.protocol === "https:" ? "wss:" : "ws:";
            const ws = new WebSocket(proto + "//" + location.host + "/ws");
            ws.onmessage = (e) => {
                const evt = JSON.parse(e.data);
                const time = new Date(evt.timestamp).toLocaleTimeString();
                const conf = (evt.confidence * 100).toFixed(0);
                const li = document.createElement("li");
                li.innerHTML = `<span class="time">${time}</span> `
                    + `<span class="species">${evt.species}</span> `
                    + `<span class="confidence">${conf}%</span>`;
                eventList.prepend(li);
                while (eventList.children.length > 50) eventList.lastChild.remove();
            };
            ws.onclose = () => setTimeout(connectWS, 3000);
        }
        connectWS();

        // Older detections (from API)
        const historyToggle = document.getElementById("history-toggle");
        const historyList = document.getElementById("history-list");
        const loadMore = document.getElementById("load-more");
        let historyOffset = 0;
        const historyLimit = 25;
        let historyLoaded = false;

        function renderSighting(s) {
            const li = document.createElement("li");
            const time = new Date(s.timestamp).toLocaleString();
            const conf = (s.species_confidence * 100).toFixed(0);
            li.innerHTML = `<span class="time">${time}</span> `
                + `<span class="species">${s.species}</span> `
                + `<span class="confidence">${conf}%</span>`;
            return li;
        }

        async function loadHistory() {
            const res = await fetch(`/api/sightings?limit=${historyLimit}&offset=${historyOffset}`);
            const data = await res.json();
            data.forEach(s => historyList.appendChild(renderSighting(s)));
            historyOffset += data.length;
            loadMore.style.display = data.length < historyLimit ? "none" : "block";
        }

        historyToggle.addEventListener("click", async () => {
            const opening = !historyList.classList.contains("open");
            historyList.classList.toggle("open");
            historyToggle.classList.toggle("open");
            if (opening && !historyLoaded) {
                historyLoaded = true;
                await loadHistory();
            }
        });

        loadMore.addEventListener("click", loadHistory);
    </script>
</body>
</html>"""

EMBED_PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; }
        body { background: transparent; overflow: hidden; }
        video, iframe { width: 100%; height: 100vh; object-fit: contain; border: 0; }
    </style>
</head>
<body>
    <div id="player"></div>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <script>
        const player = document.getElementById("player");

        function isPrivateHostname(hostname) {
            return hostname === "localhost"
                || hostname === "127.0.0.1"
                || hostname === "[::1]"
                || /^10\./.test(hostname)
                || /^192\.168\./.test(hostname)
                || /^172\.(1[6-9]|2\d|3[0-1])\./.test(hostname);
        }

        function preferWebRtc() {
            return location.protocol === "http:" || isPrivateHostname(location.hostname);
        }

        async function startHlsPlayback() {
            const video = document.createElement("video");
            video.autoplay = true;
            video.muted = true;
            video.playsInline = true;
            player.replaceChildren(video);

            const hlsUrl = "/hls/birber-annotated/index.m3u8";
            if (Hls.isSupported()) {
                const hls = new Hls({
                    liveSyncDurationCount: 3,
                    liveMaxLatencyDurationCount: 6,
                    maxLiveSyncPlaybackRate: 1.05,
                    lowLatencyMode: false,
                    backBufferLength: 30,
                });
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    video.play();
                });
                hls.on(Hls.Events.ERROR, (_, data) => {
                    if (data.fatal) {
                        setTimeout(() => hls.loadSource(hlsUrl), 3000);
                    }
                });
                return true;
            }
            if (video.canPlayType("application/vnd.apple.mpegurl")) {
                video.src = hlsUrl;
                video.addEventListener("loadedmetadata", () => video.play(), { once: true });
                return true;
            }
            return false;
        }

        async function startWebRtcPlayback() {
            const webrtcPort = "8889";
            const iframe = document.createElement("iframe");
            iframe.allow = "autoplay; fullscreen; camera; microphone";
            iframe.src = `${location.protocol}//${location.hostname}:${webrtcPort}/birber-annotated`;
            player.replaceChildren(iframe);

            const watchdog = setTimeout(async () => {
                await startHlsPlayback();
            }, 5000);

            iframe.addEventListener("load", () => clearTimeout(watchdog), { once: true });
            return true;
        }

        async function startPlayback() {
            if (preferWebRtc()) {
                try {
                    await startWebRtcPlayback();
                    return;
                } catch (_) {
                }
            }
            await startHlsPlayback();
        }

        startPlayback();
    </script>
</body>
</html>"""

REVIEW_PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Birber — Review &amp; Label</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #1a1a2e; color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            display: flex; flex-direction: column; align-items: center;
            min-height: 100vh; padding: 1rem;
        }
        h1 { margin: 0.5rem 0; font-size: 1.5rem; color: #a8d8a8; }
        .stats { font-size: 0.85rem; color: #888; margin-bottom: 1rem; }
        #card {
            width: 100%; max-width: 700px; background: #16213e;
            border-radius: 8px; padding: 1.5rem; text-align: center;
        }
        #crop-img {
            max-width: 100%; max-height: 400px; border-radius: 4px;
            margin-bottom: 1rem; background: #000;
        }
        .prediction {
            font-size: 1.3rem; margin-bottom: 0.5rem;
        }
        .prediction .species { color: #f0c040; font-weight: bold; }
        .prediction .conf { color: #888; font-size: 0.9rem; }
        .alts {
            font-size: 0.8rem; color: #666; margin-bottom: 1rem;
        }
        .alts span { margin: 0 0.3rem; }
        .alts .regional { color: #a8d8a8; }
        .actions {
            display: flex; gap: 0.5rem; justify-content: center;
            flex-wrap: wrap; margin-bottom: 1rem;
        }
        .actions button {
            padding: 0.5rem 1.2rem; border-radius: 4px; cursor: pointer;
            font-size: 0.95rem; border: none;
        }
        .btn-confirm { background: #2d6a4f; color: #fff; }
        .btn-confirm:hover { background: #40916c; }
        .btn-skip { background: #333; color: #aaa; }
        .btn-skip:hover { background: #444; }
        .btn-not-bird { background: #5a1a1a; color: #faa; }
        .btn-not-bird:hover { background: #7a2a2a; }
        .correct-wrap {
            display: flex; gap: 0.5rem; justify-content: center;
            align-items: center;
        }
        #correct-input {
            background: #0f1a30; border: 1px solid #444; color: #e0e0e0;
            padding: 0.5rem 0.75rem; border-radius: 4px; font-size: 0.95rem;
            width: 250px;
        }
        #correct-input:focus { border-color: #a8d8a8; outline: none; }
        #correct-input::placeholder { color: #555; }
        .btn-submit { background: #1a4a8a; color: #fff; }
        .btn-submit:hover { background: #2a5a9a; }
        .shortcuts {
            font-size: 0.75rem; color: #555; margin-top: 1rem;
        }
        .shortcuts kbd {
            background: #333; padding: 2px 6px; border-radius: 3px;
            border: 1px solid #555; font-family: monospace;
        }
        #empty { color: #888; font-size: 1.1rem; padding: 2rem; }
        .quick-species {
            display: flex; gap: 0.3rem; flex-wrap: wrap;
            justify-content: center; margin: 0.5rem 0;
        }
        .quick-species button {
            background: #0f1a30; border: 1px solid #333; color: #a8d8a8;
            padding: 0.3rem 0.6rem; border-radius: 4px; cursor: pointer;
            font-size: 0.8rem;
        }
        .quick-species button:hover { border-color: #a8d8a8; }
    </style>
</head>
<body>
    <h1>Birber — Review &amp; Label</h1>
    <div class="stats" id="stats"></div>
    <div id="card">
        <div id="empty">Loading...</div>
    </div>
    <div class="shortcuts">
        <kbd>Enter</kbd> confirm &nbsp;
        <kbd>S</kbd> skip &nbsp;
        <kbd>N</kbd> not a bird &nbsp;
        <kbd>Tab</kbd> focus correction input
    </div>
    <script>
        let current = null;
        let queue = [];
        let reviewed = 0;
        let total = 0;

        const card = document.getElementById("card");
        const stats = document.getElementById("stats");

        // Common species for quick-pick buttons
        const quickSpecies = QUICK_SPECIES_JSON;

        function updateStats() {
            stats.textContent = `${reviewed} reviewed / ${total} total (${total - reviewed} remaining)`;
        }

        async function loadQueue() {
            const res = await fetch("/api/crops/unreviewed?limit=50");
            const data = await res.json();
            queue = data.crops;
            total = data.total;
            reviewed = data.reviewed;
            updateStats();
            showNext();
        }

        function showNext() {
            if (queue.length === 0) {
                card.innerHTML = '<div id="empty">All caught up! No crops to review.</div>';
                return;
            }
            current = queue.shift();
            const preds = current.predictions || [];
            const top = preds[0] || {species: "Unknown", boosted: 0};

            let altsHtml = preds.slice(1, 5).map(p => {
                const cls = p.regional ? 'regional' : '';
                return `<span class="${cls}">${p.species} ${(p.boosted*100).toFixed(0)}%</span>`;
            }).join(" | ");

            let quickHtml = quickSpecies.map(s =>
                `<button onclick="submitLabel('${s}')">${s}</button>`
            ).join("");

            card.innerHTML = `
                <img id="crop-img" src="/crops/${current.id}.jpg" alt="crop">
                <div class="prediction">
                    <span class="species">${top.species}</span>
                    <span class="conf">(${(top.boosted*100).toFixed(0)}%${top.regional ? ' regional' : ''})</span>
                </div>
                <div class="alts">${altsHtml || 'no alternatives'}</div>
                <div class="actions">
                    <button class="btn-confirm" onclick="submitLabel(null)">Correct</button>
                    <button class="btn-not-bird" onclick="submitLabel('not_a_bird')">Not a bird</button>
                    <button class="btn-skip" onclick="skip()">Skip</button>
                </div>
                <div class="quick-species">${quickHtml}</div>
                <div class="correct-wrap" style="margin-top:0.5rem">
                    <input id="correct-input" type="text" placeholder="Or type species name..."
                           onkeydown="if(event.key==='Enter'){submitLabel(this.value);this.value=''}">
                    <button class="btn-submit" onclick="submitTyped()">Set</button>
                </div>
            `;
        }

        async function submitLabel(label) {
            if (!current) return;
            // null means "confirm the prediction"
            const body = {
                id: current.id,
                label: label === null ? current.predictions[0].species : label,
            };
            await fetch("/api/crops/label", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            });
            reviewed++;
            updateStats();
            showNext();
        }

        function submitTyped() {
            const input = document.getElementById("correct-input");
            if (input && input.value.trim()) {
                submitLabel(input.value.trim());
                input.value = "";
            }
        }

        function skip() {
            showNext();
        }

        // Keyboard shortcuts
        document.addEventListener("keydown", (e) => {
            if (e.target.tagName === "INPUT") return;
            if (e.key === "Enter") submitLabel(null);
            else if (e.key === "s" || e.key === "S") skip();
            else if (e.key === "n" || e.key === "N") submitLabel("not_a_bird");
            else if (e.key === "Tab") {
                e.preventDefault();
                document.getElementById("correct-input")?.focus();
            }
        });

        // Also let number keys 1-5 pick from predictions
        document.addEventListener("keydown", (e) => {
            if (e.target.tagName === "INPUT") return;
            const num = parseInt(e.key);
            if (num >= 1 && num <= 5 && current && current.predictions[num-1]) {
                submitLabel(current.predictions[num-1].species);
            }
        });

        loadQueue();
    </script>
</body>
</html>"""

logger = logging.getLogger(__name__)


class WebServer:
    """HTTP server providing REST API, WebSocket, and image serving."""

    def __init__(self, config: WebConfig, db: SightingsDB, data_dir: str,
                 crops_dir: str = "data/crops", hls_upstream: str = "http://mediamtx:8888"):
        self.config = config
        self.db = db
        self.data_dir = Path(data_dir)
        self.crops_dir = Path(crops_dir)
        self.hls_upstream = hls_upstream
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._app = web.Application()
        self._http_session: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self):
        self._app.router.add_get("/", self._stream_page)
        self._app.router.add_get("/stream", self._stream_page)
        self._app.router.add_get("/embed", self._embed_page)
        self._app.router.add_get("/ws", self._ws_handler)
        self._app.router.add_get("/api/sightings", self._get_sightings)
        self._app.router.add_get("/api/sightings/latest", self._get_latest)
        self._app.router.add_get("/api/summary", self._get_summary)
        self._app.router.add_get("/api/health", self._get_health)
        # Review/labeling UI + API
        self._app.router.add_get("/review", self._review_page)
        self._app.router.add_get("/api/crops/unreviewed", self._get_unreviewed_crops)
        self._app.router.add_post("/api/crops/label", self._label_crop)
        self._app.router.add_get("/api/crops/stats", self._get_crop_stats)
        # HLS proxy — pass through playlist and segments from MediaMTX
        self._app.router.add_get("/hls/{path:.*}", self._hls_proxy)
        # Serve crop images
        if self.crops_dir.exists():
            self._app.router.add_static("/crops", self.crops_dir, show_index=False)
        # Serve saved images
        self._app.router.add_static(
            "/images", self.data_dir, show_index=False
        )

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        peer = request.remote
        logger.info("Web WebSocket client connected: %s", peer)
        try:
            async for _ in ws:
                pass  # We only send, ignore incoming
        finally:
            self._ws_clients.discard(ws)
            logger.info("Web WebSocket client disconnected: %s", peer)
        return ws

    async def broadcast(self, event: BirdEvent):
        """Broadcast a bird event to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        payload = json.dumps(event.to_dict())
        disconnected = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(payload)
            except (ConnectionError, ConnectionResetError):
                disconnected.add(ws)
        self._ws_clients -= disconnected

    async def _get_sightings(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "50"))
        offset = int(request.query.get("offset", "0"))
        species = request.query.get("species")
        rows = self.db.get_sightings(limit=limit, offset=offset, species=species)
        return web.json_response(rows)

    async def _get_latest(self, request: web.Request) -> web.Response:
        rows = self.db.get_sightings(limit=1)
        if rows:
            return web.json_response(rows[0])
        return web.json_response(None)

    async def _get_summary(self, request: web.Request) -> web.Response:
        summary = self.db.get_summary()
        total = self.db.get_total_count()
        return web.json_response({"total": total, "by_species": summary})

    async def _get_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _stream_page(self, request: web.Request) -> web.Response:
        return web.Response(text=STREAM_PAGE, content_type="text/html")

    async def _embed_page(self, request: web.Request) -> web.Response:
        return web.Response(text=EMBED_PAGE, content_type="text/html")

    async def _review_page(self, request: web.Request) -> web.Response:
        # Build quick-species list from config's regional_species
        from .config import load_config
        config = load_config()
        quick = config.classification.regional_species[:15]
        page = REVIEW_PAGE.replace("QUICK_SPECIES_JSON", json.dumps(quick))
        return web.Response(text=page, content_type="text/html")

    def _scan_crops(self):
        """Scan crops directory for metadata files."""
        crops = []
        for meta_path in sorted(self.crops_dir.glob("*.json"), reverse=True):
            try:
                meta = json.loads(meta_path.read_text())
                crops.append(meta)
            except Exception:
                continue
        return crops

    async def _get_unreviewed_crops(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "50"))
        all_crops = self._scan_crops()
        total = len(all_crops)
        reviewed = sum(1 for c in all_crops if c.get("reviewed"))
        unreviewed = [c for c in all_crops if not c.get("reviewed")][:limit]
        return web.json_response({
            "crops": unreviewed,
            "total": total,
            "reviewed": reviewed,
        })

    async def _label_crop(self, request: web.Request) -> web.Response:
        body = await request.json()
        crop_id = body.get("id")
        label = body.get("label")
        if not crop_id or not label:
            return web.json_response({"error": "id and label required"}, status=400)

        meta_path = self.crops_dir / f"{crop_id}.json"
        if not meta_path.exists():
            return web.json_response({"error": "crop not found"}, status=404)

        meta = json.loads(meta_path.read_text())
        meta["label"] = label
        meta["reviewed"] = True
        meta_path.write_text(json.dumps(meta, indent=2))

        return web.json_response({"ok": True})

    async def _get_crop_stats(self, request: web.Request) -> web.Response:
        all_crops = self._scan_crops()
        total = len(all_crops)
        reviewed = sum(1 for c in all_crops if c.get("reviewed"))
        labels = {}
        for c in all_crops:
            if c.get("label"):
                labels[c["label"]] = labels.get(c["label"], 0) + 1
        return web.json_response({
            "total": total,
            "reviewed": reviewed,
            "unreviewed": total - reviewed,
            "labels": labels,
        })

    async def _hls_proxy(self, request: web.Request) -> web.StreamResponse:
        """Proxy HLS requests to MediaMTX internally."""
        path = request.match_info["path"]
        upstream_url = f"{self.hls_upstream}/{path}"

        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()

        try:
            async with self._http_session.get(upstream_url) as upstream:
                # Determine content type from the file extension
                if path.endswith(".m3u8"):
                    ct = "application/vnd.apple.mpegurl"
                elif path.endswith(".ts"):
                    ct = "video/mp2t"
                elif path.endswith(".mp4") or path.endswith(".m4s"):
                    ct = "video/mp4"
                elif path.endswith(".mp4") or path.endswith("init.mp4"):
                    ct = "video/mp4"
                else:
                    ct = upstream.headers.get("Content-Type", "application/octet-stream")

                resp = web.StreamResponse(
                    status=upstream.status,
                    headers={
                        "Content-Type": ct,
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "no-cache",
                    },
                )
                await resp.prepare(request)

                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    await resp.write(chunk)

                await resp.write_eof()
                return resp
        except aiohttp.ClientError as e:
            logger.warning("HLS proxy error: %s", e)
            return web.Response(status=502, text="Stream unavailable")

    async def start(self):
        if not self.config.enabled:
            logger.info("Web server disabled")
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.config.port)
        await site.start()
        logger.info("Web server listening on port %d", self.config.port)

    async def stop(self):
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
