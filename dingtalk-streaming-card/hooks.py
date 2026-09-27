"""Hook bridge: update TurnBook in-memory and enqueue card frames.

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from coalesce import Coalescer
from publisher import Publisher
from turnbook import Frame, TurnBook

logger = logging.getLogger(__name__)

_INTERVAL = 0.4


class HookBridge:
    """Routes Hermes lifecycle hooks into TurnBook + coalesced Publisher frames."""

    def __init__(
        self,
        book: TurnBook,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        coalescer: Coalescer | None = None,
        publisher: Publisher | None = None,
        api: Any = None,
    ) -> None:
        """持有 TurnBook；loop 为 None 时只改内存，不调度协程。"""
        self.book = book
        self.loop = loop
        self.coalescer = coalescer or Coalescer(_INTERVAL)
        self.publisher = publisher or Publisher()
        self.api = api

    def set_loop(self, loop: asyncio.AbstractEventLoop | None, api: Any = None) -> None:
        """绑定网关事件循环与 CardApi 实现。"""
        self.loop = loop
        if api is not None:
            self.api = api

    def active_turn(self, chat_id: str) -> str:
        """返回该 chat 上最新的 running turn_id；没有则空串。"""
        latest = ""
        latest_started = -1.0
        for turn_id, card in self.book.turns.items():
            if card.chat_id == chat_id and card.status == "running" and card.started >= latest_started:
                latest = turn_id
                latest_started = card.started
        return latest

    def _now(self) -> float:
        return time.time()

    def _first_line(self, text: object, default: str) -> str:
        if text is None:
            return default
        line = str(text).split("\n", 1)[0].strip()
        return line or default

    def _ensure_open(self, turn_id: str, session_id: str, now: float) -> list[Frame]:
        if not turn_id or turn_id in self.book.turns:
            return []
        if not session_id or session_id not in self.book.routes:
            return []
        return self.book.open_turn(turn_id=turn_id, session_id=session_id, now=now)

    def _emit(self, frames: list[Frame], now: float) -> None:
        if not frames:
            return
        ready: list[Frame] = []
        for frame in frames:
            ready.extend(self.coalescer.push(frame, now))
        if self.loop is None or self.api is None or not ready:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.publisher.send(self.api, ready), self.loop)
        except Exception:
            logger.exception("failed to schedule card publisher")

    def _guard(self, name: str, fn: Any, **kwargs: Any) -> None:
        try:
            fn(**kwargs)
        except Exception:
            logger.exception("hook %s failed", name)

    def pre_gateway_dispatch(self, **kwargs: Any) -> None:
        """只 bind_route，不建卡。"""
        self._guard("pre_gateway_dispatch", self._pre_gateway_dispatch, **kwargs)

    def _pre_gateway_dispatch(self, **kwargs: Any) -> None:
        event = kwargs.get("event")
        source = getattr(event, "source", None) if event is not None else None
        session_id = kwargs.get("session_id") or ""
        session_key = kwargs.get("session_key") or ""
        chat_id = getattr(source, "chat_id", None) or kwargs.get("chat_id") or ""
        chat_type = getattr(source, "chat_type", None) or kwargs.get("chat_type") or "dm"
        if not session_id or not chat_id:
            return
        self.book.bind_route(
            session_id=str(session_id),
            session_key=str(session_key),
            chat_id=str(chat_id),
            chat_type=str(chat_type),
        )

    def on_stream_delta(self, **kwargs: Any) -> None:
        """任意 kind 首帧建卡；reasoning 写 thinking；text 不写 content。"""
        self._guard("on_stream_delta", self._on_stream_delta, **kwargs)

    def _on_stream_delta(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        now = self._now()
        session_id = kwargs.get("session_id") or ""
        frames = self._ensure_open(turn_id, session_id, now)
        kind = kwargs.get("kind") or "text"
        if kind == "reasoning":
            frames.extend(
                self.book.push_thinking(turn_id, kwargs.get("delta") or "", now=now)
            )
        # kind=text: 建卡即可，回答正文只走 send / edit_message
        model = kwargs.get("model")
        if model and turn_id in self.book.turns:
            self.book.turns[turn_id].params["model"] = str(model)
        self._emit(frames, now)

    def on_stream_end(self, **kwargs: Any) -> None:
        """不收口。"""
        self._guard("on_stream_end", self._on_stream_end, **kwargs)

    def _on_stream_end(self, **kwargs: Any) -> None:
        return

    def pre_tool_call(self, **kwargs: Any) -> None:
        """缺 turn_id 直接返回；非 dict args 当 {}；必要时先 open_turn。"""
        self._guard("pre_tool_call", self._pre_tool_call, **kwargs)

    def _pre_tool_call(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        now = self._now()
        session_id = kwargs.get("session_id") or ""
        frames = self._ensure_open(turn_id, session_id, now)
        args = kwargs.get("args")
        if not isinstance(args, dict):
            args = {}
        frames.extend(
            self.book.tool_started(
                turn_id,
                kwargs.get("tool_call_id") or "",
                kwargs.get("tool_name") or "",
                args,
                now=now,
            )
        )
        self._emit(frames, now)

    def post_tool_call(self, **kwargs: Any) -> None:
        """成功则 tool_finished；status=error 则 finish_error。"""
        self._guard("post_tool_call", self._post_tool_call, **kwargs)

    def _post_tool_call(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        now = self._now()
        status = (kwargs.get("status") or "").lower()
        if status == "error":
            err = (
                kwargs.get("error")
                or kwargs.get("error_message")
                or kwargs.get("result")
                or "失败"
            )
            frames = self.book.finish_error(turn_id, self._first_line(err, "失败"), now=now)
            self._emit(frames, now)
            return
        frames = self.book.tool_finished(turn_id, kwargs.get("tool_call_id") or "", now=now)
        self._emit(frames, now)

    def post_api_request(self, **kwargs: Any) -> None:
        """累加 usage；finish_reason 为 error/length 时 failed=True。"""
        self._guard("post_api_request", self._post_api_request, **kwargs)

    def _post_api_request(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        usage = kwargs.get("usage")
        if not isinstance(usage, dict):
            response = kwargs.get("response")
            usage = getattr(response, "usage", None) if response is not None else None
            if not isinstance(usage, dict) and usage is not None:
                usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
        if not isinstance(usage, dict):
            usage = {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = usage.get("completion_tokens")
        if completion is None:
            total = int(usage.get("total_tokens") or 0)
            completion = max(0, total - prompt)
        else:
            completion = int(completion)
        finish_reason = kwargs.get("finish_reason") or ""
        failed = finish_reason in {"error", "length"}
        self.book.add_usage(turn_id, prompt=prompt, completion=completion, failed=failed)
        model = kwargs.get("model")
        if model and turn_id in self.book.turns:
            self.book.turns[turn_id].params["model"] = str(model)

    def on_session_end(self, **kwargs: Any) -> None:
        """终态主信号：interrupted / failed / completed。"""
        self._guard("on_session_end", self._on_session_end, **kwargs)

    def _on_session_end(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        now = self._now()
        if kwargs.get("interrupted"):
            frames = self.book.finish_error(turn_id, "已停止", now=now)
        elif kwargs.get("failed"):
            footer = self._first_line(
                kwargs.get("error") or kwargs.get("reason"),
                "失败",
            )
            frames = self.book.finish_error(turn_id, footer, now=now)
        elif kwargs.get("completed"):
            frames = self.book.finish_ok(turn_id, now=now)
        else:
            return
        self._emit(frames, now)

    def agent_loop_stopped(self, **kwargs: Any) -> None:
        """按 session_key 收口所有 running 卡。"""
        self._guard("agent_loop_stopped", self._agent_loop_stopped, **kwargs)

    def _agent_loop_stopped(self, **kwargs: Any) -> None:
        session_key = kwargs.get("session_key") or ""
        if not session_key:
            return
        now = self._now()
        frames: list[Frame] = []
        for turn_id, card in list(self.book.turns.items()):
            if card.session_key == session_key and card.status == "running":
                frames.extend(self.book.finish_error(turn_id, "已停止", now=now))
        self._emit(frames, now)

    def api_request_error(self, **kwargs: Any) -> None:
        """预检/重试耗尽等路径的收口。"""
        self._guard("api_request_error", self._api_request_error, **kwargs)

    def _api_request_error(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        now = self._now()
        footer = self._first_line(
            kwargs.get("error_message") or kwargs.get("error") or kwargs.get("error_type"),
            "失败",
        )
        frames = self.book.finish_error(turn_id, footer, now=now)
        self._emit(frames, now)

    def emit_frames(self, frames: list[Frame], *, now: float | None = None) -> None:
        """适配器侧入队（send / edit 产生的帧）。"""
        self._emit(frames, now if now is not None else self._now())
