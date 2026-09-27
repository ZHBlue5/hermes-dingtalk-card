"""Merge streaming card frames within a time window."""

from __future__ import annotations

from turnbook import Frame


class Coalescer:
    """Buffers frames by track/kind/key and emits on interval or terminal signals."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._pending: dict[tuple[str, str, str], tuple[Frame, float]] = {}

    def push(self, frame: Frame, now: float) -> list[Frame]:
        if frame.immediate or frame.is_finalize or frame.is_error:
            out = self.flush(now, force=True)
            out.append(frame)
            return out
        key = (frame.out_track_id, frame.kind, frame.key)
        if key in self._pending:
            _, first = self._pending[key]
            self._pending[key] = (frame, first)
        else:
            self._pending[key] = (frame, now)
        return []

    def flush(self, now: float, *, force: bool = False) -> list[Frame]:
        out: list[Frame] = []
        remove: list[tuple[str, str, str]] = []
        for key, (frame, first) in self._pending.items():
            if force or now - first >= self.interval:
                out.append(frame)
                remove.append(key)
        for key in remove:
            del self._pending[key]
        return out
