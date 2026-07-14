"""In-process SSE fan-out for the required single Uvicorn worker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request


class SSEBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[tuple[int, str, dict[str, Any]]]] = set()
        self._next_event_id = 1

    async def publish(self, event_name: str, data: dict[str, Any]) -> None:
        event_id = self._next_event_id
        self._next_event_id += 1
        for queue in list(self._subscribers):
            queue.put_nowait((event_id, event_name, data))

    async def stream(self, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[int, str, dict[str, Any]]] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while not await request.is_disconnected():
                try:
                    event_id, event_name, data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield self._format_event(event_id, event_name, data)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers.discard(queue)

    @staticmethod
    def _format_event(event_id: int, event_name: str, data: dict[str, Any]) -> str:
        return f"event: {event_name}\nid: {event_id}\ndata: {json.dumps(data)}\n\n"

