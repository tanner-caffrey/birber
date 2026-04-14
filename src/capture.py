import logging
import time

import cv2
import numpy as np

from .config import CaptureConfig

logger = logging.getLogger(__name__)


class CaptureSource:
    """Captures frames from an RTSP stream with automatic reconnection."""

    def __init__(self, config: CaptureConfig):
        self.config = config
        self._cap: cv2.VideoCapture | None = None
        self._frame_count = 0
        self._fps_start = time.monotonic()
        self._fps = 0.0

    def connect(self) -> bool:
        """Open the RTSP stream. Returns True on success."""
        self.disconnect()
        logger.info("Connecting to %s", self.config.rtsp_url)
        self._cap = cv2.VideoCapture(self.config.rtsp_url, cv2.CAP_FFMPEG)
        if self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, self.config.buffer_size)
            logger.info("Connected successfully")
            self._fps_start = time.monotonic()
            self._frame_count = 0
            return True
        logger.warning("Failed to connect")
        self._cap = None
        return False

    def disconnect(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def read(self) -> np.ndarray | None:
        """Read a single frame. Returns None on failure."""
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None
        self._frame_count += 1
        elapsed = time.monotonic() - self._fps_start
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_start = time.monotonic()
        return frame

    @property
    def fps(self) -> float:
        return self._fps

    def reconnect_loop(self) -> bool:
        """Try to reconnect, blocking with delay. Returns True on success."""
        logger.info(
            "Reconnecting in %d seconds...", self.config.reconnect_delay
        )
        time.sleep(self.config.reconnect_delay)
        return self.connect()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
