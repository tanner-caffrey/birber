import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CaptureConfig:
    rtsp_url: str = "rtsp://host.docker.internal:8554/birdcam"
    reconnect_delay: int = 5
    buffer_size: int = 1
    device_name: str = "Elgato HD60 X"
    width: int = 1920
    height: int = 1080
    framerate: int = 30
    preset: str = "veryfast"
    crf: int = 20
    tune: str = "zerolatency"
    kasa_plug_ip: str = ""
    power_cycle_interval: int = 20


@dataclass
class ProcessingConfig:
    device: str = "auto"
    frame_skip: int = 5
    motion_threshold: int = 2500
    motion_blur_kernel: int = 21
    reference_update_interval: int = 300


@dataclass
class DetectionConfig:
    model: str = "yolov8n.pt"
    confidence_threshold: float = 0.45
    bird_class_id: int = 14


@dataclass
class ClassificationConfig:
    model: str = "dennisjooo/Birds-Classifier-EfficientNetB2"
    confidence_threshold: float = 0.01
    top_k: int = 10
    regional_boost: float = 3.0
    regional_species: list[str] = field(default_factory=list)


@dataclass
class StreamConfig:
    enabled: bool = True
    output_url: str = "rtsp://host.docker.internal:8554/birber-annotated"
    fps: int = 30
    width: int = 1920
    height: int = 1080
    preset: str = "ultrafast"
    crf: int = 23
    tune: str = "zerolatency"


@dataclass
class RtmpConfig:
    enabled: bool = False
    url: str = ""
    stream_key: str = ""
    preset: str = "veryfast"
    bitrate: str = "2500k"
    keyint: int = 30  # 1s at 30fps


@dataclass
class WebhookConfig:
    enabled: bool = False
    urls: list[str] = field(default_factory=list)
    timeout: int = 10


@dataclass
class MqttConfig:
    enabled: bool = False
    broker: str = "localhost"
    port: int = 1883
    topic: str = "birber/sightings"


@dataclass
class WebSocketConfig:
    enabled: bool = True
    port: int = 8765


@dataclass
class EventsConfig:
    cooldown: int = 30
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)


@dataclass
class WebConfig:
    enabled: bool = True
    port: int = 8080


@dataclass
class TunnelConfig:
    enabled: bool = False
    token: str = ""


@dataclass
class StorageConfig:
    save_directory: str = "data/captures"
    jpeg_quality: int = 92


@dataclass
class DatabaseConfig:
    path: str = "data/sightings.db"


@dataclass
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    rtmp: RtmpConfig = field(default_factory=RtmpConfig)
    events: EventsConfig = field(default_factory=EventsConfig)
    web: WebConfig = field(default_factory=WebConfig)
    tunnel: TunnelConfig = field(default_factory=TunnelConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


def _dict_to_dataclass(cls, data: dict):
    """Recursively convert a dict to a nested dataclass."""
    if not isinstance(data, dict):
        return data
    field_types = {f.name: f.type for f in cls.__dataclass_fields__.values()}
    kwargs = {}
    for key, value in data.items():
        if key in field_types:
            ft = field_types[key]
            # Resolve string annotations to actual types
            if isinstance(ft, str):
                ft = eval(ft)
            if hasattr(ft, "__dataclass_fields__") and isinstance(value, dict):
                kwargs[key] = _dict_to_dataclass(ft, value)
            else:
                kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | None = None) -> Config:
    """Load configuration from YAML file, falling back to defaults."""
    if path is None:
        path = os.environ.get("BIRBER_CONFIG", "config.yaml")

    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        return _dict_to_dataclass(Config, raw)

    return Config()
