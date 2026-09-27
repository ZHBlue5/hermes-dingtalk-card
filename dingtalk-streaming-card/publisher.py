"""Send coalesced card frames with stream retry and webhook fallback."""

from __future__ import annotations

import logging
from typing import Protocol

from turnbook import Frame

logger = logging.getLogger(__name__)

TERMINAL_ORDER = ("params", "stream")

_KIND_RANK = {kind: i for i, kind in enumerate(TERMINAL_ORDER)}


class CardApi(Protocol):
    async def stream(self, frame: Frame) -> None: ...

    async def update_params(self, frame: Frame) -> None: ...

    async def webhook(self, frame: Frame) -> None: ...


class Publisher:
    """Dispatches frames to DingTalk card APIs in terminal order."""

    async def send(self, api: CardApi, frames: list[Frame]) -> None:
        ordered = sorted(frames, key=lambda f: _KIND_RANK.get(f.kind, len(TERMINAL_ORDER)))
        for frame in ordered:
            if frame.kind == "params":
                if not await self._send_params(api, frame):
                    return
            elif frame.kind == "stream":
                if not await self._send_stream(api, frame):
                    return
            elif frame.kind == "webhook":
                await api.webhook(frame)

    async def _send_params(self, api: CardApi, frame: Frame) -> bool:
        try:
            await api.update_params(frame)
        except Exception:
            logger.exception("card params failed out_track_id=%s", frame.out_track_id)
            return False
        return True

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
        try:
            await api.webhook(frame)
        except Exception:
            logger.exception("card webhook failed out_track_id=%s", frame.out_track_id)
            return False
        return True
