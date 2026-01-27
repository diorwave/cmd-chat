import asyncio
import logging
from typing import Optional
from sanic import Websocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, Websocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: Websocket) -> None:
        async with self._lock:
            self.active_connections[user_id] = websocket
        logger.info(f"User {user_id} connected")

    async def disconnect(self, user_id: str) -> None:
        async with self._lock:
            if user_id in self.active_connections:
                del self.active_connections[user_id]
        logger.info(f"User {user_id} disconnected")

    async def broadcast(self, message: str, exclude_user: Optional[str] = None) -> None:
        # Get snapshot of connections to minimize lock duration
        async with self._lock:
            connections_snapshot = list(self.active_connections.items())
        
        disconnected = []
        for user_id, connection in connections_snapshot:
            if exclude_user and user_id == exclude_user:
                continue
            try:
                await connection.send(message)
            except Exception as e:
                logger.debug(f"Failed to send to user {user_id}: {type(e).__name__}")
                disconnected.append(user_id)

        # Clean up disconnected users
        if disconnected:
            async with self._lock:
                for user_id in disconnected:
                    if user_id in self.active_connections:
                        del self.active_connections[user_id]

    async def send_personal(self, user_id: str, message: str) -> bool:
        async with self._lock:
            if connection := self.active_connections.get(user_id):
                try:
                    await connection.send(message)
                    return True
                except Exception as e:
                    logger.debug(f"Failed to send personal message to user {user_id}: {type(e).__name__}")
                    return False
        return False
