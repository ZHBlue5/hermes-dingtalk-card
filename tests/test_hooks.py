from hooks import HookBridge
from turnbook import TurnBook

def test_pre_tool_call_never_raises():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.pre_tool_call(turn_id="t", tool_call_id="1", tool_name="", args="nope")
    bridge.pre_tool_call(turn_id="t", tool_call_id="2", tool_name="terminal", args=None)

def test_pre_gateway_dispatch_does_not_open_a_card():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    class Source:
        chat_id = "c"
        chat_type = "dm"
    class Event:
        source = Source()
        text = "hi"
    bridge.pre_gateway_dispatch(event=Event(), session_id="s", session_key="sk")
    assert bridge.book.turns == {}

def test_stream_end_does_not_finish():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.on_stream_end(turn_id="t", finished=True, error=None)
    assert bridge.book.get("t").status == "running"

def test_session_end_finishes_once():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.on_session_end(turn_id="t", completed=True, failed=False, interrupted=False)
    bridge.on_session_end(turn_id="t", completed=False, failed=True, interrupted=False)
    assert bridge.book.get("t").status == "done"

def test_stop_uses_session_key():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.agent_loop_stopped(session_key="sk", platform="dingtalk", reason="stop")
    assert bridge.book.get("t").params["footer"] == "已停止"

def test_api_request_error_closes_without_session_end():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)
    bridge.book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    bridge.book.open_turn(turn_id="t", session_id="s", now=0.0)
    bridge.api_request_error(turn_id="t", error_message="timeout")
    assert bridge.book.get("t").status == "error"
    assert bridge.book.get("t").params["footer"] == "timeout"

def test_host_pre_gateway_binds_via_session_store():
    bridge = HookBridge(TurnBook(group_thinking=True), loop=None)

    class Source:
        chat_id = "c"
        chat_type = "dm"

    class Event:
        source = Source()
        text = "hi"

    class Gateway:
        def _session_key_for_source(self, source):
            return "sk"

    class Entry:
        session_key = "sk"
        session_id = "sid"

    class Store:
        def lookup_by_session_id(self, session_id):
            assert session_id == "sid"
            return Entry()

    bridge.pre_gateway_dispatch(
        event=Event(), gateway=Gateway(), session_store=Store()
    )
    assert bridge.book.turns == {}
    assert bridge.book.routes == {}
    bridge.on_stream_delta(turn_id="t", session_id="sid", kind="text", delta="x")
    assert "t" in bridge.book.turns
    assert bridge.book.routes["sid"] == ("sk", "c", "dm")
