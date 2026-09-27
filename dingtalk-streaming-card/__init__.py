"""DingTalk enhanced streaming card plugin entry.

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import logging
from typing import Any

from .adapter import EnhancedDingTalkAdapter
from .hooks import HookBridge
from .turnbook import TurnBook

logger = logging.getLogger(__name__)

_HOOK_NAMES = (
    "pre_gateway_dispatch",
    "on_stream_delta",
    "on_stream_end",
    "pre_tool_call",
    "post_tool_call",
    "post_api_request",
    "on_session_end",
    "agent_loop_stopped",
    "api_request_error",
)


class _RecordingCtx:
    """只记录 register_platform kwargs 的 stub。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def register_platform(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


def register(ctx) -> None:
    """调用官方 register 打桩，换上 EnhancedDingTalkAdapter 并注册 hooks。"""
    group_thinking = True
    try:
        group_thinking = bool(ctx.get_config("group_thinking", True))
    except Exception:
        group_thinking = True

    book = TurnBook(group_thinking=group_thinking)
    bridge = HookBridge(book, loop=None)

    stub = _RecordingCtx()
    try:
        from plugins.platforms.dingtalk.adapter import register as official_register

        official_register(stub)
    except Exception:
        logger.exception("official dingtalk register stub failed")
        return

    if not stub.calls:
        logger.error("official dingtalk register produced no platform kwargs")
        return

    kwargs = dict(stub.calls[-1])
    kwargs["adapter_factory"] = lambda config, _bridge=bridge: EnhancedDingTalkAdapter(
        config, bridge=_bridge
    )

    handle = ctx.register_platform(**kwargs)
    if handle is None:
        logger.error("register_platform returned None; enhanced dingtalk card not active")

    for name in _HOOK_NAMES:
        ctx.register_hook(name, getattr(bridge, name))
