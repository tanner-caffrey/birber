import asyncio
import json
import logging

import websockets

from ..config import WebSocketConfig
from .base import BirdEvent, EventEmitter

logger = logging.getLogger(__name__)


class WebSocketEmitter(EventEmitter):
    """WebSocket server that broadcasts bird events to connected clients."""

    def __init__(self, config: WebSocketConfig):
        self.config = config
        self._clients: set[websockets.WebSocketServerProtocol] = set()
        self._server = None

    async def _handler(self, websocket: websockets.WebSocketServerProtocol):
        self._clients.add(websocket)
        client_addr = websocket.remote_address
        logger.info("WebSocket client connected: %s", client_addr)
        try:
            async for _ in websocket:
                pass  # We only send, ignore incoming messages
        finally:
            self._clients.discard(websocket)
            logger.info("WebSocket client disconnected: %s", client_addr)

    async def start(self):
        if not self.config.enabled:
            return
        self._server = await websockets.serve(
            self._handler,
            "0.0.0.0",
            self.config.port,
        )
        logger.info("WebSocket server listening on port %d", self.config.port)

    async def emit(self, event: BirdEvent):
        if not self.config.enabled or not self._clients:
            return
        payload = json.dumps(event.to_dict())
        # Broadcast to all connected clients
        disconnected = set()
        for client in self._clients:
            try:
                await client.send(payload)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
        self._clients -= disconnected

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
