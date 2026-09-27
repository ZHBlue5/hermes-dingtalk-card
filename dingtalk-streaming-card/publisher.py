"""Send coalesced card frames with stream retry and webhook fallback."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Protocol

from .turnbook import Frame

logger = logging.getLogger(__name__)

TERMINAL_ORDER = ("params", "stream")

_KIND_RANK = {kind: i for i, kind in enumerate(TERMINAL_ORDER)}


class CardApi(Protocol):
    async def stream(self, frame: Frame) -> None: ...

    async def update_params(self, frame: Frame) -> None: ...

    async def webhook(self, frame: Frame) -> None: ...


class Publisher:
    """Dispatches frames to DingTalk card APIs in terminal order."""

    def __init__(self) -> None:
        """每条 out_track_id 一把锁，串行化同卡发送。"""
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, out_track_id: str) -> asyncio.Lock:
        lock = self._locks.get(out_track_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[out_track_id] = lock
        return lock

    async def send(self, api: CardApi, frames: list[Frame]) -> None:
        ordered = sorted(frames, key=lambda f: _KIND_RANK.get(f.kind, len(TERMINAL_ORDER)))
        by_track: dict[str, list[Frame]] = defaultdict(list)
        for frame in ordered:
            by_track[frame.out_track_id].append(frame)

        async def _send_track(track_id: str, track_frames: list[Frame]) -> None:
            async with self._lock_for(track_id):
                for frame in track_frames:
                    if frame.kind == "params":
                        # params 失败不中断同批后续 is_finalize
                        await self._send_params(api, frame)
                    elif frame.kind == "stream":
                        if not await self._send_stream(api, frame):
                            return
                    elif frame.kind == "webhook":
                        await api.webhook(frame)

        await asyncio.gather(
            *(_send_track(tid, tframes) for tid, tframes in by_track.items())
        )

    async def _send_params(self, api: CardApi, frame: Frame) -> bool:
        last_err: Exception | None = None
        for _ in range(2):
            try:
                await api.update_params(frame)
                return True
            except Exception as exc:
                last_err = exc
                logger.exception(
                    "card params failed out_track_id=%s", frame.out_track_id
                )
        return False

    async def _send_stream(self, api: CardApi, frame: Frame) -> bool:
        if frame.is_error:
            try:
                await api.stream(frame)
            except Exception:
                logger.exception("card stream failed out_track_id=%s", frame.out_track_id)
                return False
            return True

        last_err: Exception | None = None
        for _ in range(2):
            try:
                await api.stream(frame)
                return True
            except Exception as exc:
                last_err = exc

        footer = str(last_err).split("\n", 1)[0][:80]
        err_params = Frame(
            frame.chat_id,
            frame.turn_id,
            frame.out_track_id,
            "params",
            params={"status": "error", "headerColor": "danger", "footer": footer},
        )
        if not await self._send_params(api, err_params):
            return False
        # 仅回答正文 webhook 补发；thinking 失败不得进会话
        if frame.key != "content":
            return False
        try:
            await api.webhook(frame)
        except Exception:
            logger.exception("card webhook failed out_track_id=%s", frame.out_track_id)
            return False
        return True
