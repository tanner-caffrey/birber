import json
import logging

import aiohttp

from ..config import WebhookConfig
from .base import BirdEvent, EventEmitter

logger = logging.getLogger(__name__)


class WebhookEmitter(EventEmitter):
    """Sends bird events as HTTP POST requests to configured URLs."""

    def __init__(self, config: WebhookConfig):
        self.config = config
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        if not self.config.enabled:
            return
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.timeout)
        )
        logger.info("Webhook emitter started (%d URLs)", len(self.config.urls))

    async def emit(self, event: BirdEvent):
        if not self.config.enabled or self._session is None:
            return
        payload = json.dumps(event.to_dict())
        headers = {"Content-Type": "application/json"}

        for url in self.config.urls:
            try:
                async with self._session.post(
                    url, data=payload, headers=headers
                ) as resp:
                    if resp.status >= 400:
                        logger.warning(
                            "Webhook %s returned %d", url, resp.status
                        )
            except Exception as e:
                logger.warning("Webhook %s failed: %s", url, e)

    async def stop(self):
        if self._session is not None:
            await self._session.close()
            self._session = None
