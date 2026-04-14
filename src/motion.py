import time

import cv2
import numpy as np

from .config import ProcessingConfig


class MotionDetector:
    """Detects motion using background subtraction with frame differencing."""

    def __init__(self, config: ProcessingConfig):
        self.config = config
        self._reference: np.ndarray | None = None
        self._last_update = 0.0

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(
            gray,
            (self.config.motion_blur_kernel, self.config.motion_blur_kernel),
            0,
        )

    def update_reference(self, frame: np.ndarray):
        """Set a new reference frame."""
        self._reference = self._preprocess(frame)
        self._last_update = time.monotonic()

    def detect(self, frame: np.ndarray) -> tuple[bool, int]:
        """Check for motion against the reference frame.

        Returns (has_motion, motion_score).
        """
        now = time.monotonic()

        if self._reference is None:
            self.update_reference(frame)
            return False, 0

        # Periodically refresh reference to adapt to lighting changes
        if now - self._last_update > self.config.reference_update_interval:
            self.update_reference(frame)
            return False, 0

        current = self._preprocess(frame)
        diff = cv2.absdiff(self._reference, current)
        _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        motion_score = sum(cv2.contourArea(c) for c in contours)
        has_motion = motion_score >= self.config.motion_threshold

        return has_motion, int(motion_score)
