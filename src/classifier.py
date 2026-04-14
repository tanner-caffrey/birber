import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from transformers import pipeline

from .config import ClassificationConfig

logger = logging.getLogger(__name__)


@dataclass
class Classification:
    """A species classification result."""
    species: str
    confidence: float


class BirdClassifier:
    """Classifies bird species with optional regional boosting and crop saving."""

    def __init__(self, config: ClassificationConfig, device: str = "cpu",
                 crops_dir: str = "data/crops"):
        self.config = config
        self._crops_dir = Path(crops_dir)
        self._crops_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Loading classifier: %s (device=%s)", config.model, device)
        device_id = 0 if device == "cuda" else -1
        self._pipe = pipeline(
            "image-classification",
            model=config.model,
            device=device_id,
        )
        self._regional = set(s.lower() for s in config.regional_species)
        if self._regional:
            logger.info(
                "Regional boosting enabled: %d species (%.1fx boost)",
                len(self._regional), config.regional_boost,
            )

    def _save_crop(self, crop_bgr: np.ndarray, top_results: list[tuple]):
        """Save crop image and metadata for later review/training."""
        crop_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        img_path = self._crops_dir / f"{crop_id}.jpg"
        meta_path = self._crops_dir / f"{crop_id}.json"

        cv2.imwrite(str(img_path), crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])

        meta = {
            "id": crop_id,
            "predicted": top_results[0][0] if top_results else "unknown",
            "predictions": [
                {"species": s, "raw": round(r, 4), "boosted": round(b, 4), "regional": reg}
                for s, r, b, reg in top_results[:5]
            ],
            "label": None,  # filled in by review UI
            "reviewed": False,
        }
        meta_path.write_text(json.dumps(meta, indent=2))

    def classify(
        self, frame: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> list[Classification]:
        """Classify the bird species from a cropped region of the frame."""
        x, y, w, h = bbox
        pad_x = int(w * 0.15)
        pad_y = int(h * 0.15)
        fh, fw = frame.shape[:2]
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(fw, x + w + pad_x)
        y2 = min(fh, y + h + pad_y)

        crop = frame[y1:y2, x1:x2]
        rgb_crop = crop[:, :, ::-1]
        pil_image = Image.fromarray(rgb_crop)

        results = self._pipe(pil_image, top_k=self.config.top_k)

        # Apply regional boosting
        scored = []
        for r in results:
            species = r["label"].replace("_", " ").title()
            raw_conf = r["score"]
            is_regional = species.lower() in self._regional
            boosted = min(raw_conf * self.config.regional_boost, 1.0) if is_regional else raw_conf
            scored.append((species, raw_conf, boosted, is_regional))

        scored.sort(key=lambda x: x[2], reverse=True)

        if scored:
            s = scored[0]
            tag = " [regional]" if s[3] else ""
            logger.info(
                "Classification: %s (raw=%.1f%% boosted=%.1f%%)%s",
                s[0], s[1] * 100, s[2] * 100, tag,
            )

        # Save crop for training/review
        self._save_crop(crop, scored)

        classifications = []
        for species, raw_conf, boosted, _ in scored:
            if boosted >= self.config.confidence_threshold:
                classifications.append(Classification(
                    species=species,
                    confidence=boosted,
                ))

        return classifications
