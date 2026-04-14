import logging
from dataclasses import dataclass

import numpy as np
from ultralytics import YOLO

from .config import DetectionConfig

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """A detected bird bounding box."""
    x: int
    y: int
    w: int
    h: int
    confidence: float


class BirdDetector:
    """Detects birds in frames using YOLOv8."""

    def __init__(self, config: DetectionConfig, device: str = "cpu"):
        self.config = config
        logger.info("Loading YOLO model: %s (device=%s)", config.model, device)
        self._model = YOLO(config.model)
        self._device = device

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run detection on a frame. Returns list of bird detections."""
        results = self._model(
            frame,
            device=self._device,
            classes=[self.config.bird_class_id],
            conf=self.config.confidence_threshold,
            verbose=False,
        )

        detections = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                conf = float(box.conf[0])
                detections.append(Detection(
                    x=int(x1),
                    y=int(y1),
                    w=int(x2 - x1),
                    h=int(y2 - y1),
                    confidence=conf,
                ))

        if detections:
            logger.debug("Detected %d bird(s)", len(detections))

        return detections
