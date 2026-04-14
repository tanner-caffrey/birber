import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .config import RtmpConfig, StreamConfig

logger = logging.getLogger(__name__)


class RTSPOutputStream:
    """Pushes annotated frames to MediaMTX as an RTSP stream via ffmpeg."""

    def __init__(self, config: StreamConfig):
        self.config = config
        self._process: subprocess.Popen | None = None

    def start(self):
        """Start the ffmpeg process that accepts raw frames on stdin."""
        if not self.config.enabled:
            logger.info("Output stream disabled")
            return

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.config.width}x{self.config.height}",
            "-r", str(self.config.fps),
            "-i", "-",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-x264opts", "bframes=0",
        ]
        if self.config.tune:
            cmd += ["-tune", self.config.tune]
        cmd += [
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            self.config.output_url,
        ]

        logger.info("Starting output stream to %s", self.config.output_url)
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def write_frame(self, frame: np.ndarray):
        """Write a frame to the output stream."""
        if self._process is None or self._process.stdin is None:
            return
        if self._process.poll() is not None:
            stderr = ""
            if self._process.stderr:
                try:
                    stderr = self._process.stderr.read().decode(errors="replace")[-500:]
                except Exception:
                    pass
            logger.warning("Output stream died (rc=%s): %s", self._process.returncode, stderr)
            self.start()
            return
        try:
            # Resize if needed
            h, w = frame.shape[:2]
            if w != self.config.width or h != self.config.height:
                import cv2
                frame = cv2.resize(frame, (self.config.width, self.config.height))
            self._process.stdin.write(frame.tobytes())
        except BrokenPipeError:
            logger.warning("Output stream pipe broken, restarting...")
            self.start()

    def stop(self):
        if self._process is not None:
            logger.info("Stopping output stream")
            try:
                self._process.stdin.close()
            except Exception:
                pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


class RTMPOutputStream:
    """Pushes annotated frames to an RTMP/RTMPS server via ffmpeg."""

    def __init__(self, config: RtmpConfig, width: int = 1920, height: int = 1080, fps: int = 30):
        self.config = config
        self.width = width
        self.height = height
        self.fps = fps
        self._process: subprocess.Popen | None = None

    def start(self):
        if not self.config.enabled:
            logger.info("RTMP stream disabled")
            return

        url = f"{self.config.url}/{self.config.stream_key}"
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "-",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-b:v", self.config.bitrate,
            "-g", str(self.config.keyint),
            "-keyint_min", str(self.config.keyint),
            "-x264opts", "bframes=0",
            "-tune", "zerolatency",
            "-f", "flv",
            url,
        ]

        logger.info("Starting RTMP stream to %s", self.config.url)
        logger.info("RTMP cmd: %s", " ".join(cmd))
        self._rtmp_log_path = Path(tempfile.gettempdir()) / "rtmp_ffmpeg.log"
        self._rtmp_log = open(self._rtmp_log_path, "w")
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=self._rtmp_log,
            stderr=subprocess.STDOUT,
        )

    def write_frame(self, frame: np.ndarray):
        if self._process is None or self._process.stdin is None:
            return
        if self._process.poll() is not None:
            stderr = ""
            if self._process.stdout:
                try:
                    stderr = self._process.stdout.read().decode(errors="replace")[-500:]
                except Exception:
                    pass
            try:
                with open(self._rtmp_log_path) as f:
                    stderr = f.read()[-500:]
            except Exception:
                stderr = ""
            logger.warning("RTMP stream died (rc=%s): %s", self._process.returncode, stderr)
            self.start()
            return
        try:
            h, w = frame.shape[:2]
            if w != self.width or h != self.height:
                import cv2
                frame = cv2.resize(frame, (self.width, self.height))
            self._process.stdin.write(frame.tobytes())
        except BrokenPipeError:
            logger.warning("RTMP stream pipe broken, restarting...")
            self.start()

    def stop(self):
        if self._process is not None:
            logger.info("Stopping RTMP stream")
            try:
                self._process.stdin.close()
            except Exception:
                pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
