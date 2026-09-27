"""Adapter webhook / orphan-create behavior (no live DingTalk)."""

import asyncio

from hermes_plugins.dingtalk_streaming_card.adapter import EnhancedDingTalkAdapter
from hermes_plugins.dingtalk_streaming_card.hooks import HookBridge
from hermes_plugins.dingtalk_streaming_card.routing import route_send
from hermes_plugins.dingtalk_streaming_card.turnbook import Frame, TurnBook


def _bare_adapter(*, bridge=None):
    class _Plat:
        value = "dingtalk"

    adapter = object.__new__(EnhancedDingTalkAdapter)
    adapter.platform = _Plat()
    adapter.MAX_MESSAGE_LENGTH = 20000
    adapter._bridge = bridge
    adapter._enhanced = True
    adapter._http_client = None
    adapter._session_webhooks = {}
    adapter._message_contexts = {}
    adapter._streaming_cards = {}
    adapter._normalize_markdown = lambda text: text
    adapter._fire_done_reaction = lambda chat_id: None
    return adapter


def test_markdown_webhook_posts_payload_not_super_send():
    posts = []

    class Resp:
        status_code = 200
        text = "ok"

    class Http:
        async def post(self, url, json=None, timeout=None):
            posts.append((url, json, timeout))
            return Resp()

    adapter = _bare_adapter()
    adapter._http_client = Http()
    adapter._session_webhooks = {"c": ("https://hook.example/x", 9_999_999_999_999)}
    adapter._get_valid_webhook = lambda chat_id: adapter._session_webhooks.get(chat_id)

    result = asyncio.run(adapter._send_markdown_webhook("c", "hello **world**"))
    assert result.success
    assert posts[0][0] == "https://hook.example/x"
    assert posts[0][1] == {
        "msgtype": "markdown",
        "markdown": {"title": "Hermes", "text": "hello **world**"},
    }


def test_cardapi_webhook_uses_markdown_post():
    posts = []

    class Resp:
        status_code = 200
        text = "ok"

    class Http:
        async def post(self, url, json=None, timeout=None):
            posts.append(json)
            return Resp()

    adapter = _bare_adapter()
    adapter._http_client = Http()
    adapter._session_webhooks = {"c": ("https://hook.example/x", 9_999_999_999_999)}
    adapter._get_valid_webhook = lambda chat_id: adapter._session_webhooks.get(chat_id)

    frame = Frame("c", "t", "track", "webhook", content="tail")
    asyncio.run(adapter.webhook(frame))
    assert posts[0]["msgtype"] == "markdown"
    assert posts[0]["markdown"]["text"] == "tail"


def test_orphan_after_create_abandons_and_webhooks_once():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    track = bridge.book.get("t").out_track_id

    posts = []

    class Resp:
        status_code = 200
        text = "ok"

    class Http:
        async def post(self, url, json=None, timeout=None):
            posts.append(json)
            return Resp()

    adapter = _bare_adapter(bridge=bridge)
    adapter._http_client = Http()
    adapter._session_webhooks = {"c": ("https://hook.example/x", 9_999_999_999_999)}
    adapter._get_valid_webhook = lambda chat_id: adapter._session_webhooks.get(chat_id)
    adapter._message_contexts = {"c": object()}

    async def fake_create(chat_id, message, card, content, *, finalize):
        assert card.out_track_id == track
        return None, True

    adapter._create_enhanced_card = fake_create

    result = asyncio.run(adapter.send("c", "answer body"))
    assert result.success
    assert len(posts) == 1
    assert posts[0]["markdown"]["text"] == "answer body"
    assert bridge.book.get("t").status == "error"
    assert bridge.book.get("t").delivered is False
    assert route_send(bridge.book, chat_id="c", turn_id="t") == "webhook"
