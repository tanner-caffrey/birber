import json
import logging

from ..config import MqttConfig
from .base import BirdEvent, EventEmitter

logger = logging.getLogger(__name__)


class MqttEmitter(EventEmitter):
    """Publishes bird events to an MQTT broker."""

    def __init__(self, config: MqttConfig):
        self.config = config
        self._client = None

    async def start(self):
        if not self.config.enabled:
            return
        try:
            import paho.mqtt.client as mqtt

            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id="birber",
            )
            self._client.connect(self.config.broker, self.config.port)
            self._client.loop_start()
            logger.info(
                "MQTT emitter connected to %s:%d",
                self.config.broker,
                self.config.port,
            )
        except Exception as e:
            logger.warning("MQTT connection failed: %s", e)
            self._client = None

    async def emit(self, event: BirdEvent):
        if not self.config.enabled or self._client is None:
            return
        payload = json.dumps(event.to_dict())
        try:
            self._client.publish(self.config.topic, payload)
        except Exception as e:
            logger.warning("MQTT publish failed: %s", e)

    async def stop(self):
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
