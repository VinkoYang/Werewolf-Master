from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional


AsyncCallback = Callable[[], Awaitable[None]]


class AsyncTimer:
    """Utility timer that executes an async callback after a delay."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    def start(self, seconds: int, callback: AsyncCallback) -> None:
        self.cancel()
        self._task = asyncio.create_task(self._run(seconds, callback))

    def cancel(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self, seconds: int, callback: AsyncCallback) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        await callback()


class VoteTimer(AsyncTimer):
    """Specialized timer used for vote deadlines."""


class BadgeTransferTimer(AsyncTimer):
    """Timer dedicated to badge transfer windows."""
