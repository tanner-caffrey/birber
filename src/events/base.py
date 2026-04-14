from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass
class BirdEvent:
    """Event payload emitted when a bird is identified."""
    event: str = "bird_detected"
    timestamp: str = ""
    species: str = ""
    confidence: float = 0.0
    detection_confidence: float = 0.0
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    image_path: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        return d


class EventEmitter(ABC):
    """Base class for event emitters."""

    @abstractmethod
    async def start(self):
        """Initialize the emitter (connect, bind, etc.)."""

    @abstractmethod
    async def emit(self, event: BirdEvent):
        """Send an event."""

    @abstractmethod
    async def stop(self):
        """Clean up resources."""
