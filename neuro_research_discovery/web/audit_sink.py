"""Buffered audit-log sink for the web UI's live event stream.

The existing `neuro_research_discovery.audit` logger emits one JSON line per
tool call to stderr. For the web UI we want the same lines pushed to every
connected browser via Server-Sent Events.

Mechanism:
- `WebAuditSink` is a logging.Handler that captures formatted records into
  a bounded deque AND fans them out to per-client asyncio.Queues.
- Each SSE connection registers a queue at connect and unregisters at
  disconnect. The audit logger's `propagate=False` setting in server.py
  means we have to attach this handler directly to that logger.
- New clients get a small backlog (last N lines) so the stream isn't empty
  on first connect.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import AsyncIterator


class WebAuditSink(logging.Handler):
    """Fan-out handler: keep a rolling backlog + push to N live queues."""

    def __init__(self, backlog: int = 200) -> None:
        super().__init__(level=logging.INFO)
        self._backlog: deque[str] = deque(maxlen=backlog)
        self._queues: list[asyncio.Queue[str]] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the FastAPI event loop. Must be called from inside that loop."""
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        # The audit logger emits already-formatted JSON in record.message.
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self._backlog.append(line)
            queues = list(self._queues)
        if not self._loop or not queues:
            return
        # We're typically called from the same loop the FastAPI app runs in
        # (every tool call goes through there). put_nowait is safe; fall
        # back to call_soon_threadsafe just in case logging fires off-loop.
        for q in queues:
            try:
                self._loop.call_soon_threadsafe(self._enqueue, q, line)
            except RuntimeError:
                # Loop closed; drop silently.
                pass

    @staticmethod
    def _enqueue(q: asyncio.Queue[str], line: str) -> None:
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            # Slow client; drop the oldest to keep the live tail flowing.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    async def stream(self) -> AsyncIterator[str]:
        """Async iterator a SSE endpoint can consume."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        with self._lock:
            # Send the existing backlog first so a fresh page isn't empty.
            for line in list(self._backlog):
                try:
                    q.put_nowait(line)
                except asyncio.QueueFull:
                    break
            self._queues.append(q)
        try:
            while True:
                line = await q.get()
                yield line
        finally:
            with self._lock:
                if q in self._queues:
                    self._queues.remove(q)
