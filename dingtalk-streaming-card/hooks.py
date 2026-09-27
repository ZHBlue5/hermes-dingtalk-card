"""Hook bridge: update TurnBook in-memory and enqueue card frames.

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .coalesce import Coalescer
from .publisher import Publisher
from .turnbook import Frame, TurnBook

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
        self._session_store: Any = None
        # session_key → (chat_id, chat_type)；宿主进线时尚无 session_id
        self._pending_chats: dict[str, tuple[str, str]] = {}
        self._flush_scheduled = False
        # hook 线程 push 与 gateway loop flush 不得交错改 coalescer
        self._coalesce_lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop | None, api: Any = None) -> None:
        """绑定网关事件循环与 CardApi 实现。"""
        self.loop = loop
        if api is not None:
            self.api = api

    def active_turn(self, chat_id: str) -> str:
        """恰好一张 running 卡时返回其 turn_id；多张则空串（走 webhook）。"""
        running = [
            turn_id
            for turn_id, card in self.book.turns.items()
            if card.chat_id == chat_id and card.status == "running"
        ]
        if len(running) != 1:
            return ""
        return running[0]

    def _now(self) -> float:
        return time.time()

    def _first_line(self, text: object, default: str) -> str:
        if text is None:
            return default
        line = str(text).split("\n", 1)[0].strip()
        return line or default

    def _bind_session_id(self, session_id: str) -> bool:
        """有 session_id 但尚无 route 时，用 pending chat + session_store 补 bind。"""
        if not session_id or session_id in self.book.routes:
            return session_id in self.book.routes
        store = self._session_store
        if store is None:
            return False
        try:
            entry = store.lookup_by_session_id(session_id)
        except Exception:
            logger.exception("lookup_by_session_id failed")
            return False
        if entry is None:
            return False
        session_key = getattr(entry, "session_key", None) or ""
        pending = self._pending_chats.get(str(session_key))
        if not pending:
            return False
        chat_id, chat_type = pending
        self.book.bind_route(
            session_id=str(session_id),
            session_key=str(session_key),
            chat_id=chat_id,
            chat_type=chat_type,
        )
        return True

    def _ensure_open(self, turn_id: str, session_id: str, now: float) -> list[Frame]:
        if not turn_id or turn_id in self.book.turns:
            return []
        if not session_id:
            return []
        if session_id not in self.book.routes and not self._bind_session_id(session_id):
            return []
        return self.book.open_turn(turn_id=turn_id, session_id=session_id, now=now)

    def _emit(self, frames: list[Frame], now: float) -> None:
        if not frames:
            return
        # 锁内：push / 拷贝待发帧 / 读 has_pending；锁外：publisher / 网络
        with self._coalesce_lock:
            ready: list[Frame] = []
            for frame in frames:
                ready.extend(self.coalescer.push(frame, now))
            ready_out = list(ready)
            need_flush = self.coalescer.has_pending
        if self.loop is None or self.api is None:
            return
        if ready_out:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.publisher.send(self.api, ready_out), self.loop
                )
            except Exception:
                logger.exception("failed to schedule card publisher")
        if need_flush:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        """在 coalescer.interval 后刷出到期帧（纯文本轮不必等到 finish）。"""
        if self.loop is None or self.api is None:
            return
        with self._coalesce_lock:
            if self._flush_scheduled:
                return
            self._flush_scheduled = True
            interval = self.coalescer.interval

        def _run() -> None:
            if self.api is None:
                with self._coalesce_lock:
                    self._flush_scheduled = False
                return
            with self._coalesce_lock:
                self._flush_scheduled = False
                due_out = list(self.coalescer.flush(time.time(), force=False))
                need_flush = self.coalescer.has_pending
            if due_out:
                try:
                    asyncio.create_task(self.publisher.send(self.api, due_out))
                except Exception:
                    logger.exception("failed to publish flushed card frames")
            if need_flush:
                self._schedule_flush()

        def _arm() -> None:
            self.loop.call_later(interval, _run)

        try:
            self.loop.call_soon_threadsafe(_arm)
        except Exception:
            with self._coalesce_lock:
                self._flush_scheduled = False
            logger.exception("failed to schedule coalescer flush")

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
        gateway = kwargs.get("gateway")
        session_store = kwargs.get("session_store")
        if session_store is not None:
            self._session_store = session_store
        source = getattr(event, "source", None) if event is not None else None
        session_id = kwargs.get("session_id") or ""
        session_key = kwargs.get("session_key") or ""
        chat_id = getattr(source, "chat_id", None) or kwargs.get("chat_id") or ""
        chat_type = getattr(source, "chat_type", None) or kwargs.get("chat_type") or "dm"
        if not chat_id:
            return
        # 测试/旧路径：kwargs 自带 session_id 时直接 bind
        if session_id:
            self.book.bind_route(
                session_id=str(session_id),
                session_key=str(session_key),
                chat_id=str(chat_id),
                chat_type=str(chat_type),
            )
            return
        # 宿主路径：只有 event/gateway/session_store，先按 session_key 记 chat
        if gateway is not None and source is not None:
            try:
                session_key = gateway._session_key_for_source(source) or ""
            except Exception:
                logger.exception("gateway._session_key_for_source failed")
                session_key = ""
        if session_key:
            self._pending_chats[str(session_key)] = (str(chat_id), str(chat_type))

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
        """工具结束（含 status=error）只标记 tool_finished，不收口卡片。"""
        self._guard("post_tool_call", self._post_tool_call, **kwargs)

    def _post_tool_call(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        now = self._now()
        # 普通工具失败不 finish_error，卡片继续 running
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
        """不可重试时收口；retryable=True 时保持卡片 running。"""
        self._guard("api_request_error", self._api_request_error, **kwargs)

    def _api_request_error(self, **kwargs: Any) -> None:
        turn_id = kwargs.get("turn_id") or ""
        if not turn_id:
            return
        if kwargs.get("retryable") is True:
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
