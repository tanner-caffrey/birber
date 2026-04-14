import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import StorageConfig
from .detector import Detection

logger = logging.getLogger(__name__)


class FrameStorage:
    """Saves annotated frames to disk organized by date."""

    def __init__(self, config: StorageConfig):
        self.config = config
        self._base_dir = Path(config.save_directory)

    def save_frame(
        self,
        frame: np.ndarray,
        species: str,
        confidence: float,
        detection: Detection,
        timestamp: datetime | None = None,
    ) -> str:
        """Save the full frame with bounding box overlay.

        Returns the relative path to the saved image.
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Create date-based subdirectory
        date_dir = self._base_dir / timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        # Draw bounding box and label on a copy
        annotated = frame.copy()
        color = (0, 255, 0)
        cv2.rectangle(
            annotated,
            (detection.x, detection.y),
            (detection.x + detection.w, detection.y + detection.h),
            color,
            2,
        )
        label = f"{species} ({confidence:.0%})"
        label_y = max(detection.y - 10, 20)
        cv2.putText(
            annotated,
            label,
            (detection.x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
        )

        # Build filename
        safe_species = species.lower().replace(" ", "_")
        time_str = timestamp.strftime("%H%M%S")
        filename = f"{time_str}_{safe_species}_{confidence:.0%}.jpg".replace("%", "pct")
        filepath = date_dir / filename

        params = [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality]
        cv2.imwrite(str(filepath), annotated, params)
        logger.debug("Saved frame: %s", filepath)

        return str(filepath)
