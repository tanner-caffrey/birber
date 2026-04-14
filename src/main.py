import asyncio
import logging
import signal
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from queue import SimpleQueue

import cv2
import numpy as np
import torch

from .capture import CaptureSource
from .classifier import BirdClassifier
from .config import load_config
from .database import SightingsDB
from .detector import BirdDetector
from .events import BirdEvent, MqttEmitter, WebhookEmitter, WebSocketEmitter
from .motion import MotionDetector
from .storage import FrameStorage
from .stream import RTMPOutputStream, RTSPOutputStream
from .web import WebServer

logger = logging.getLogger("birber")


def _is_static_screen(frame, threshold: float = 15.0) -> bool:
    """Detect if the frame is a static/no-signal screen (low color variance)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    std = gray.std()
    # Also check if frame is mostly dark (capture card no-signal screen)
    mean = gray.mean()
    logger.debug("Frame stats: mean=%.1f std=%.1f", mean, std)
    # Static if low variance OR very dark (no-signal / standby screen)
    return std < threshold or mean < 30


def _render_info_screen(width: int, height: int, db) -> np.ndarray:
    """Render an info screen showing recent sightings."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    # Dark background with slight blue tint
    frame[:] = (30, 20, 15)

    # Title
    cv2.putText(frame, "Birber", (width // 2 - 120, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.8, (168, 216, 168), 3)
    cv2.putText(frame, "Camera offline - waiting for signal...", (width // 2 - 250, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 120, 120), 1)

    # Recent sightings
    y = 160
    cv2.putText(frame, "Recent Sightings", (60, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (168, 216, 168), 2)
    y += 15

    try:
        sightings = db.get_sightings(limit=12)
        if not sightings:
            y += 40
            cv2.putText(frame, "No sightings yet", (80, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 1)
        else:
            for s in sightings:
                y += 40
                if y > height - 40:
                    break
                species = s.get("species", "Unknown")
                conf = s.get("species_confidence", 0)
                ts = s.get("timestamp", "")
                # Format timestamp
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(ts)
                    time_str = dt.strftime("%b %d %I:%M %p")
                except Exception:
                    time_str = ts[:16]

                cv2.putText(frame, f"{species}", (80, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 2)
                cv2.putText(frame, f"{conf:.0%}  -  {time_str}", (80, y + 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 120, 120), 1)
                y += 20
    except Exception:
        pass

    # Summary
    try:
        summary = db.get_summary()
        total = db.get_total_count()
        if summary:
            y = height - 80
            cv2.putText(frame, f"Total: {total} sightings, {len(summary)} species", (60, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (100, 100, 100), 1)
    except Exception:
        pass

    return frame


def _power_cycle_kasa(ip: str, off_seconds: int = 3):
    """Turn a Kasa smart plug off and back on."""
    import asyncio as _asyncio
    from kasa import Discover

    async def _cycle():
        plug = await Discover.discover_single(ip)
        await plug.turn_off()
        await _asyncio.sleep(off_seconds)
        await plug.turn_on()

    _asyncio.run(_cycle())


def resolve_device(preference: str) -> str:
    """Resolve 'auto' device preference to 'cuda' or 'cpu'."""
    if preference == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return preference


class BirdTracker:
    """Tracks birds by position and accumulates classification votes."""

    def __init__(self, iou_threshold: float = 0.3, min_votes: int = 5,
                 consensus_ratio: float = 0.5, expire_seconds: float = 3.0,
                 crop_interval: float = 30.0):
        self.iou_threshold = iou_threshold
        self.min_votes = min_votes
        self.consensus_ratio = consensus_ratio
        self.expire_seconds = expire_seconds
        self.crop_interval = crop_interval
        # Each tracked bird: {bbox, votes: Counter, confirmed_species, confirmed_conf, last_seen, reported, last_crop_time}
        self._birds: list[dict] = []

    @staticmethod
    def _iou(a, b):
        """Intersection over union of two (x, y, w, h) boxes."""
        ax1, ay1, aw, ah = a
        bx1, by1, bw, bh = b
        ax2, ay2 = ax1 + aw, ay1 + ah
        bx2, by2 = bx1 + bw, by1 + bh
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0

    def _find_match(self, bbox):
        """Find an existing tracked bird that overlaps with this bbox."""
        best_idx, best_iou = -1, 0
        for i, bird in enumerate(self._birds):
            score = self._iou(bbox, bird["bbox"])
            if score > best_iou:
                best_iou = score
                best_idx = i
        if best_iou >= self.iou_threshold:
            return best_idx
        return -1

    def update(self, det, species: str, confidence: float, now: float):
        """Add a classification vote for a detection. Returns (consensus_result, should_save_crop)."""
        bbox = (det.x, det.y, det.w, det.h)
        idx = self._find_match(bbox)

        if idx >= 0:
            bird = self._birds[idx]
            bird["bbox"] = bbox  # Update position
            bird["votes"][species] += 1
            bird["last_seen"] = now
        else:
            bird = {
                "bbox": bbox,
                "votes": Counter({species: 1}),
                "confirmed_species": None,
                "confirmed_conf": 0.0,
                "last_seen": now,
                "reported": False,
                "last_crop_time": 0,
            }
            self._birds.append(bird)

        # Check for consensus
        consensus = None
        total = sum(bird["votes"].values())
        top_species, top_count = bird["votes"].most_common(1)[0]
        if total >= self.min_votes and top_count / total >= self.consensus_ratio:
            bird["confirmed_species"] = top_species
            # Smooth confidence with EMA to avoid jumpy % display
            if bird["confirmed_conf"] > 0:
                bird["confirmed_conf"] = 0.3 * confidence + 0.7 * bird["confirmed_conf"]
            else:
                bird["confirmed_conf"] = confidence
            if not bird["reported"]:
                bird["reported"] = True
                consensus = (top_species, bird["confirmed_conf"])

        # Save a crop on consensus, or periodically for training variety
        save_crop = False
        if consensus:
            save_crop = True
        elif now - bird["last_crop_time"] >= self.crop_interval:
            save_crop = True

        if save_crop:
            bird["last_crop_time"] = now

        return consensus, save_crop

    def get_display_labels(self):
        """Get current labels for overlay display."""
        results = []
        for bird in self._birds:
            if bird["confirmed_species"]:
                results.append((
                    bird["bbox"],
                    bird["confirmed_species"],
                    bird["confirmed_conf"],
                ))
            else:
                # Show tentative top vote with "?" suffix
                top_species, _ = bird["votes"].most_common(1)[0]
                results.append((bird["bbox"], top_species + "?", 0.0))
        return results

    def expire(self, now: float):
        """Remove birds that haven't been seen recently. Returns expired confirmed species."""
        expired = []
        remaining = []
        for bird in self._birds:
            if now - bird["last_seen"] > self.expire_seconds:
                if bird["confirmed_species"]:
                    expired.append(bird["confirmed_species"])
            else:
                remaining.append(bird)
        self._birds = remaining
        return expired


class OverlayRenderer:
    """Draws smooth, fading overlays based on BirdTracker state."""

    def __init__(self, persist_seconds: float = 2.0):
        self.persist_seconds = persist_seconds

    def draw(self, frame, birds: list[dict]):
        """Draw overlays for all tracked birds."""
        now = time.monotonic()
        result = frame.copy()

        for bird in birds:
            age = now - bird["last_seen"]
            if age > self.persist_seconds:
                continue

            alpha = max(0.0, min(1.0, 1.0 - (age / self.persist_seconds)))

            x, y = int(bird["bbox"][0]), int(bird["bbox"][1])
            w, h = int(bird["bbox"][2]), int(bird["bbox"][3])

            color = (0, int(255 * alpha), 0)
            cv2.rectangle(result, (x, y), (x + w, y + h), color, 2)

            # Build label from current state
            if bird["confirmed_species"]:
                label = f"{bird['confirmed_species']} ({bird['confirmed_conf']:.0%})"
            else:
                top_species, _ = bird["votes"].most_common(1)[0]
                label = f"{top_species}?"

            label_y = max(y - 10, 20)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            overlay = result.copy()
            cv2.rectangle(overlay, (x, label_y - th - 4), (x + tw + 4, label_y + 4), (0, 0, 0), -1)
            cv2.addWeighted(overlay, alpha * 0.6, result, 1 - alpha * 0.6, 0, result)

            cv2.putText(
                result, label, (x + 2, label_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2,
            )

        return result


def capture_loop(
    config,
    capture,
    motion,
    detector,
    classifier,
    storage,
    stream,
    rtmp,
    db,
    event_queue: SimpleQueue,
    shutdown_flag,
):
    """Capture + overlay loop with async ML processing in a separate thread."""
    tracker = BirdTracker(iou_threshold=0.3, min_votes=5, consensus_ratio=0.5, expire_seconds=3.0)
    overlay = OverlayRenderer(persist_seconds=2.0)
    last_power_cycle = time.monotonic()
    cycle_interval = config.capture.power_cycle_interval * 60  # convert to seconds
    kasa_ip = config.capture.kasa_plug_ip

    # Shared frame slot for the ML thread (always holds the latest frame)
    _ml_lock = threading.Lock()
    _ml_frame = [None]

    def ml_worker():
        """Background thread: grabs latest frame, runs detection + classification."""
        while not shutdown_flag.is_set():
            with _ml_lock:
                frame = _ml_frame[0]
                _ml_frame[0] = None

            if frame is None:
                time.sleep(0.01)
                continue

            has_motion, _ = motion.detect(frame)
            if not has_motion:
                tracker.expire(time.monotonic())
                continue

            detections = detector.detect(frame)
            if not detections:
                tracker.expire(time.monotonic())
                continue

            now = time.monotonic()
            timestamp = datetime.now(timezone.utc)
            timestamp_str = timestamp.isoformat()

            for det in detections:
                classifications = classifier.classify(
                    frame, (det.x, det.y, det.w, det.h)
                )
                if not classifications:
                    continue

                top = classifications[0]
                result, save_crop = tracker.update(det, top.species, top.confidence, now)

                if save_crop:
                    classifier.save_pending_crop()

                if result:
                    species, conf = result

                    image_path = storage.save_frame(
                        frame, species, conf, det, timestamp
                    )

                    db.log_sighting(
                        timestamp=timestamp_str,
                        species=species,
                        species_confidence=conf,
                        detection_confidence=det.confidence,
                        image_path=image_path,
                        bbox=(det.x, det.y, det.w, det.h),
                    )

                    bird_event = BirdEvent(
                        timestamp=timestamp_str,
                        species=species,
                        confidence=conf,
                        detection_confidence=det.confidence,
                        bbox=(det.x, det.y, det.w, det.h),
                        image_path=image_path,
                    )
                    event_queue.put(bird_event)

                    logger.info(
                        "🐦 %s (%.0f%%) detected at (%d,%d) [det: %.0f%%]",
                        species,
                        conf * 100,
                        det.x,
                        det.y,
                        det.confidence * 100,
                    )

            tracker.expire(now)

    # Power-cycle on startup
    if kasa_ip:
        logger.info("Power-cycling camera on startup via Kasa plug %s", kasa_ip)
        try:
            _power_cycle_kasa(kasa_ip)
        except Exception as e:
            logger.warning("Kasa power-cycle failed: %s", e)

    if not capture.connect():
        logger.error("Failed to connect to capture source")
        return

    logger.info("Birber is running. Watching for birds...")

    ml_thread = threading.Thread(target=ml_worker, name="ml-pipeline", daemon=True)
    ml_thread.start()

    # FPS tracking
    frame_count = 0
    fps_start = time.monotonic()
    output_frame_interval = 1.0 / max(config.stream.fps, 1)
    next_output_time = time.monotonic()

    while not shutdown_flag.is_set():
        # Power-cycle the camera on a schedule to prevent auto-shutoff
        if kasa_ip and cycle_interval > 0:
            now_cycle = time.monotonic()
            if now_cycle - last_power_cycle >= cycle_interval:
                last_power_cycle = now_cycle
                logger.info("Power-cycling camera via Kasa plug %s", kasa_ip)
                try:
                    _power_cycle_kasa(kasa_ip)
                except Exception as e:
                    logger.warning("Kasa power-cycle failed: %s", e)

        frame = capture.read()

        if frame is None:
            if not capture.reconnect_loop():
                continue
            frame = capture.read()
            if frame is None:
                continue

        # Detect static/no-signal screen
        if _is_static_screen(frame):
            info = _render_info_screen(config.stream.width, config.stream.height, db)
            stream.write_frame(info)
            rtmp.write_frame(info)
            continue

        # Post latest frame to ML thread (non-blocking, always newest)
        with _ml_lock:
            _ml_frame[0] = frame

        # Draw overlay and publish at the configured output FPS
        out = overlay.draw(frame, tracker._birds)
        now_output = time.monotonic()
        if now_output >= next_output_time:
            stream.write_frame(out)
            rtmp.write_frame(out)
            next_output_time = now_output + output_frame_interval

        # FPS tracking
        frame_count += 1
        now_fps = time.monotonic()
        if now_fps - fps_start >= 5.0:
            fps = frame_count / (now_fps - fps_start)
            logger.info("Frame loop: %.1f fps", fps)
            frame_count = 0
            fps_start = now_fps

    ml_thread.join(timeout=5)

    # Cleanup synchronous resources
    capture.disconnect()
    stream.stop()
    rtmp.stop()


async def event_dispatcher(event_queue: SimpleQueue, emitters, web_server, shutdown):
    """Async task that drains the event queue and emits events."""
    while not shutdown.is_set():
        try:
            # Check for events without blocking the event loop
            while not event_queue.empty():
                bird_event = event_queue.get_nowait()
                for emitter in emitters:
                    try:
                        await emitter.emit(bird_event)
                    except Exception as e:
                        logger.warning("Event emit failed: %s", e)
                try:
                    await web_server.broadcast(bird_event)
                except Exception as e:
                    logger.warning("Web broadcast failed: %s", e)
        except Exception as e:
            logger.warning("Event dispatcher error: %s", e)
        await asyncio.sleep(0.1)


async def run():
    config = load_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    device = resolve_device(config.processing.device)
    logger.info("Using device: %s", device)

    # Initialize components
    capture = CaptureSource(config.capture)
    motion = MotionDetector(config.processing)
    detector = BirdDetector(config.detection, device=device)
    classifier = BirdClassifier(config.classification, device=device)
    db = SightingsDB(config.database)
    storage = FrameStorage(config.storage)
    stream = RTSPOutputStream(config.stream)
    import os
    # Override capture URL via env var (set by start.bat --capture flag)
    capture_url = os.environ.get("BIRBER_CAPTURE_URL")
    if capture_url:
        config.capture.rtsp_url = capture_url
    # RTMP can be enabled via env var (set by start.bat --stream flag)
    if os.environ.get("BIRBER_RTMP_ENABLED"):
        config.rtmp.enabled = True
    stream_key = os.environ.get("BIRBER_STREAM_KEY")
    if stream_key:
        config.rtmp.stream_key = stream_key
    rtmp = RTMPOutputStream(config.rtmp, config.stream.width, config.stream.height, config.stream.fps)
    crops_dir = "data/crops"
    web_server = WebServer(config.web, db, config.storage.save_directory, crops_dir=crops_dir)

    emitters = [
        WebhookEmitter(config.events.webhook),
        MqttEmitter(config.events.mqtt),
        WebSocketEmitter(config.events.websocket),
    ]

    # Shared state between threads
    event_queue = SimpleQueue()
    shutdown = asyncio.Event()

    def on_signal(*_):
        logger.info("Shutdown signal received")
        shutdown.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Start async services
    await web_server.start()
    for emitter in emitters:
        await emitter.start()
    stream.start()
    rtmp.start()

    # Start the event dispatcher
    dispatcher_task = asyncio.create_task(
        event_dispatcher(event_queue, emitters, web_server, shutdown)
    )

    # Run the capture loop in a thread so it doesn't block the event loop
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")

    try:
        await loop.run_in_executor(
            executor,
            capture_loop,
            config,
            capture,
            motion,
            detector,
            classifier,
            storage,
            stream,
            rtmp,
            db,
            event_queue,
            shutdown,
        )
    finally:
        logger.info("Shutting down...")
        shutdown.set()
        dispatcher_task.cancel()

        # Print summary
        summary = db.get_summary()
        total = db.get_total_count()
        if summary:
            logger.info("Session summary (%d total sightings):", total)
            for species, count in summary.items():
                logger.info("  %s: %d", species, count)

        await web_server.stop()
        db.close()
        for emitter in emitters:
            await emitter.stop()
        executor.shutdown(wait=False)

        logger.info("Goodbye!")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
