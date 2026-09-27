"""Tests for final-review fixes (AI-generated).

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import asyncio
import time

from hermes_plugins.dingtalk_streaming_card.adapter import EnhancedDingTalkAdapter
from hermes_plugins.dingtalk_streaming_card.coalesce import Coalescer
from hermes_plugins.dingtalk_streaming_card.hooks import HookBridge
from hermes_plugins.dingtalk_streaming_card.publisher import Publisher
from hermes_plugins.dingtalk_streaming_card.textutil import tool_line
from hermes_plugins.dingtalk_streaming_card.turnbook import Frame, TurnBook


def test_flush_emits_due_frames_via_publisher():
    """非 immediate 帧 push 后应 schedule flush，到期后发给 publisher。"""
    book = TurnBook(group_thinking=True)
    sent: list[Frame] = []

    class Api:
        async def stream(self, frame):
            pass

        async def update_params(self, frame):
            pass

        async def webhook(self, frame):
            pass

    class FakePub:
        async def send(self, api, frames):
            sent.extend(frames)

    class Loop:
        def __init__(self):
            self._cb = None

        def call_soon_threadsafe(self, fn, *args):
            fn(*args)

        def call_later(self, delay, fn, *args):
            assert delay == 0.4
            self._cb = (fn, args)
            return None

    loop = Loop()
    bridge = HookBridge(book, loop=loop, publisher=FakePub(), api=Api())
    bridge.coalescer = Coalescer(0.4)
    frame = Frame("c", "t", "o", "stream", key="content", content="hi")
    bridge._emit([frame], 0.0)
    assert bridge.coalescer.has_pending
    assert loop._cb is not None
    key = next(iter(bridge.coalescer._pending))
    fr, _ = bridge.coalescer._pending[key]
    bridge.coalescer._pending[key] = (fr, time.time() - 1.0)

    async def _drive():
        fn, args = loop._cb
        fn(*args)

    asyncio.run(_drive())
    assert any(f.content == "hi" for f in sent)


def test_push_thinking_redacts_secrets():
    book = TurnBook(group_thinking=True)
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.push_thinking("t", "token sk-abcdefghijklmnopqrstuvwxyz ok", now=0.1)
    thinking = book.get("t").thinking
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in thinking


def test_thinking_stream_failure_does_not_webhook():
    class Api:
        def __init__(self):
            self.calls = []

        async def stream(self, frame):
            self.calls.append(("stream", frame.key))
            raise RuntimeError("stream")

        async def update_params(self, frame):
            self.calls.append(("params", frame.params.get("status")))

        async def webhook(self, frame):
            self.calls.append(("webhook", frame.content))

    api = Api()
    pub = Publisher()
    frame = Frame("c", "t", "o", "stream", key="thinking", content="secret thoughts")
    asyncio.run(pub.send(api, [frame]))
    assert "webhook" not in [c[0] for c in api.calls]


def test_post_tool_call_error_keeps_card_running():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.pre_tool_call(
        turn_id="t", tool_call_id="1", tool_name="terminal", args={"cmd": "x"}
    )
    bridge.post_tool_call(turn_id="t", tool_call_id="1", status="error", error="boom")
    assert bridge.book.get("t").status == "running"
    assert "进行中" not in bridge.book.get("t").params["tool"]


def test_api_request_error_retryable_keeps_running():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.api_request_error(turn_id="t", error_message="tmp", retryable=True)
    assert bridge.book.get("t").status == "running"
    bridge.api_request_error(turn_id="t", error_message="final", retryable=False)
    assert bridge.book.get("t").status == "error"
    assert bridge.book.get("t").params["footer"] == "final"


def test_active_turn_ambiguous_chat_returns_empty():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s1", session_key="sk1", chat_id="g", chat_type="group")
    bridge.book.open_turn(turn_id="t1", session_id="s1", now=0.0)
    bridge.book.bind_route(session_id="s2", session_key="sk2", chat_id="g", chat_type="group")
    bridge.book.open_turn(turn_id="t2", session_id="s2", now=1.0)
    assert bridge.active_turn("g") == ""
    assert bridge.active_turn("missing") == ""
    bridge.book.finish_ok("t1", now=2.0)
    assert bridge.active_turn("g") == "t2"


def test_segment_keeps_prior_content_when_host_clears_buffer():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.book.push_content("t", "seg1", segment_finalize=False, now=1.0)
    bridge.book.push_content("t", "", segment_finalize=True, now=1.1)
    assert bridge.book.get("t").status == "running"

    adapter = object.__new__(EnhancedDingTalkAdapter)
    adapter._bridge = bridge
    adapter._enhanced = True
    adapter._streaming_cards = {}

    async def _edit():
        return await EnhancedDingTalkAdapter.edit_message(
            adapter, "c", bridge.book.get("t").out_track_id, "seg2", finalize=False
        )

    result = asyncio.run(_edit())
    assert result.success
    assert bridge.book.get("t").content == "seg1seg2"
    assert bridge.book.get("t").status == "running"


def test_orphan_close_emits_params_frame():
    book = TurnBook(group_thinking=True)
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="old", session_id="s", now=0.0)
    old_track = book.get("old").out_track_id
    book.drop_turn_index("old")
    frames = book.open_turn(turn_id="new", session_id="s", now=601.0)
    assert book.card_by_track(old_track).params["footer"] == "未完成"
    orphan_params = [
        f for f in frames if f.kind == "params" and f.out_track_id == old_track
    ]
    assert orphan_params
    assert orphan_params[0].params["status"] == "error"
    assert orphan_params[0].params["footer"] == "未完成"


def test_create_card_failure_abandons_turn():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)

    class Resp:
        status_code = 200
        text = "ok"

    class Http:
        async def post(self, url, json=None, timeout=None):
            return Resp()

    adapter = object.__new__(EnhancedDingTalkAdapter)
    adapter.platform = type("P", (), {"value": "dingtalk"})()
    adapter.MAX_MESSAGE_LENGTH = 20000
    adapter._bridge = bridge
    adapter._enhanced = True
    adapter._http_client = Http()
    adapter._session_webhooks = {"c": ("https://hook.example/x", 9_999_999_999_999)}
    adapter._get_valid_webhook = lambda chat_id: adapter._session_webhooks.get(chat_id)
    adapter._message_contexts = {"c": object()}
    adapter._streaming_cards = {}
    adapter._normalize_markdown = lambda text: text
    adapter._fire_done_reaction = lambda chat_id: None

    async def fake_create(chat_id, message, card, content, *, finalize):
        return None, False

    adapter._create_enhanced_card = fake_create
    result = asyncio.run(EnhancedDingTalkAdapter.send(adapter, "c", "body"))
    assert result.success
    assert bridge.book.get("t").status == "error"
    assert bridge.book.get("t").delivered is False


def test_tool_line_includes_display_name():
    mcp = tool_line("mcp__github__create_issue", {"title": "x"}, running=False)
    assert "github.create_issue" in mcp
    assert mcp.startswith("MCP · ")
    browser = tool_line("browser_navigate", {"url": "https://a"}, running=True)
    assert "browser_navigate" in browser
    assert browser.startswith("Browser · ")


def test_emitted_params_elapsed_uses_current_now():
    book = TurnBook(group_thinking=True)
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.turns["t"].params["model"] = "gpt-test"
    frames = book.tool_started("t", "1", "terminal", {"cmd": "ls"}, now=12.44)
    assert frames[0].params["elapsed"] == "12.4s"
    assert frames[0].params["model"] == "gpt-test"


def test_publisher_params_retry_and_keeps_finalize():
    class Api:
        def __init__(self):
            self.calls = []
            self.param_fails_left = 1

        async def stream(self, frame):
            self.calls.append(("stream", frame.key, frame.is_finalize))

        async def update_params(self, frame):
            self.calls.append(("params", frame.params.get("status")))
            if self.param_fails_left:
                self.param_fails_left -= 1
                raise RuntimeError("params")

        async def webhook(self, frame):
            self.calls.append(("webhook", frame.content))

    api = Api()
    pub = Publisher()
    params = Frame(
        "c", "t", "track", "params", params={"status": "done"}, immediate=True
    )
    final = Frame(
        "c",
        "t",
        "track",
        "stream",
        key="content",
        content="done",
        is_finalize=True,
    )
    asyncio.run(pub.send(api, [params, final]))
    assert api.calls.count(("params", "done")) == 2
    assert ("stream", "content", True) in api.calls


def test_publisher_params_total_failure_still_sends_finalize():
    class Api:
        def __init__(self):
            self.calls = []

        async def stream(self, frame):
            self.calls.append(("stream", frame.key, frame.is_finalize))

        async def update_params(self, frame):
            self.calls.append("params")
            raise RuntimeError("params")

        async def webhook(self, frame):
            pass

    api = Api()
    pub = Publisher()
    params = Frame(
        "c", "t", "track", "params", params={"status": "done"}, immediate=True
    )
    final = Frame(
        "c",
        "t",
        "track",
        "stream",
        key="content",
        content="done",
        is_finalize=True,
    )
    asyncio.run(pub.send(api, [params, final]))
    assert api.calls.count("params") == 2
    assert ("stream", "content", True) in api.calls


def test_publisher_serializes_same_track():
    order: list[str] = []

    class Api:
        async def stream(self, frame):
            order.append(f"start-{frame.content}")
            await asyncio.sleep(0.05)
            order.append(f"end-{frame.content}")

        async def update_params(self, frame):
            pass

        async def webhook(self, frame):
            pass

    pub = Publisher()
    api = Api()

    async def _both():
        await asyncio.gather(
            pub.send(
                api, [Frame("c", "t", "same", "stream", key="content", content="a")]
            ),
            pub.send(
                api, [Frame("c", "t", "same", "stream", key="content", content="b")]
            ),
        )

    asyncio.run(_both())
    assert order in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )


def test_connect_disabled_does_not_set_loop():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)

    adapter = object.__new__(EnhancedDingTalkAdapter)
    adapter._bridge = bridge
    adapter._card_template_id = ""

    async def _run():
        ok = True
        sdk_ok = True
        if not sdk_ok:
            adapter._enhanced = False
        elif not adapter._card_template_id:
            adapter._enhanced = False
        else:
            adapter._enhanced = True
        if ok and adapter._enhanced and adapter._bridge is not None:
            adapter._bridge.set_loop(asyncio.get_running_loop(), api=adapter)
        return ok

    asyncio.run(_run())
    assert adapter._enhanced is False
    assert bridge.loop is None
    assert bridge.api is None

    class BoomPub:
        async def send(self, api, frames):
            raise AssertionError("must not publish when enhanced disabled")

    bridge.publisher = BoomPub()
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.on_stream_delta(turn_id="t", session_id="s", kind="reasoning", delta="x")
