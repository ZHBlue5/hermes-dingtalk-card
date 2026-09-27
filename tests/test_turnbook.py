from hermes_plugins.dingtalk_streaming_card.turnbook import TurnBook

def _book():
    return TurnBook(group_thinking=True)

def test_running_to_done_footer():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.push_content("t", "hello", segment_finalize=False, now=1.0)
    frames = book.finish_ok("t", now=12.44)
    params = next(f for f in frames if f.kind == "params").params
    assert params["status"] == "done"
    assert params["headerColor"] == "success"
    assert params["footer"] == "已完成 · 12.4s · —"
    assert params["elapsed"] == "12.4s"
    streams = [f for f in frames if f.kind == "stream" and f.is_finalize]
    assert {f.key for f in streams} == {"content"}

def test_interrupt_footer():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    frames = book.finish_error("t", "已停止", now=1.0)
    assert next(f for f in frames if f.kind == "params").params["footer"] == "已停止"
    assert not any(f.is_error for f in frames)

def test_is_error_only_on_streamed_keys():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.push_thinking("t", "hmm", now=0.1)
    frames = book.finish_error("t", "boom", now=1.0)
    erred = [f for f in frames if f.is_error]
    assert [f.key for f in erred] == ["thinking"]

def test_segment_finalize_does_not_complete():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    frames = book.push_content("t", "partial", segment_finalize=True, now=1.0)
    assert all(not f.is_finalize for f in frames)
    assert book.get("t").status == "running"

def test_tool_window_keeps_five_newest_first():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    for i in range(6):
        book.tool_started("t", f"id{i}", "terminal", {"cmd": str(i)}, now=float(i))
        book.tool_finished("t", f"id{i}", now=float(i) + 0.1)
    text = book.get("t").params["tool"]
    assert "cmd=0" not in text
    assert text.index("cmd=5") < text.index("cmd=1")

def test_tool_started_and_finished_emit_immediate_params():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    started = book.tool_started("t", "1", "terminal", {"cmd": "ls"}, now=1.0)
    assert len(started) == 1
    assert started[0].kind == "params"
    assert started[0].immediate is True
    assert "进行中" in started[0].params["tool"]
    assert started[0].params["toolCount"] == "1"
    finished = book.tool_finished("t", "1", now=2.0)
    assert len(finished) == 1
    assert finished[0].kind == "params"
    assert finished[0].immediate is True
    assert "进行中" not in finished[0].params["tool"]

def test_other_session_same_chat_untouched():
    book = _book()
    book.bind_route(session_id="s1", session_key="sk1", chat_id="group", chat_type="group")
    book.open_turn(turn_id="t1", session_id="s1", now=0.0)
    book.bind_route(session_id="s2", session_key="sk2", chat_id="group", chat_type="group")
    book.open_turn(turn_id="t2", session_id="s2", now=1.0)
    assert book.get("t1").status == "running"

def test_same_session_new_turn_closes_previous():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t1", session_id="s", now=0.0)
    frames = book.open_turn(turn_id="t2", session_id="s", now=1.0)
    assert book.get("t1").params["footer"] == "已结束"
    assert book.get("t1").status == "error"
    assert any(f.turn_id == "t1" and f.kind == "params" for f in frames)

def test_orphan_after_ten_minutes():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="old", session_id="s", now=0.0)
    book.drop_turn_index("old")
    book.open_turn(turn_id="new", session_id="s", now=601.0)
    assert book.card_by_track(book.get("old").out_track_id).params["footer"] == "未完成"

def test_group_thinking_off_skips_group_only():
    book = TurnBook(group_thinking=False)
    book.bind_route(session_id="s", session_key="sk", chat_id="g", chat_type="group")
    book.open_turn(turn_id="tg", session_id="s", now=0.0)
    assert book.push_thinking("tg", "secret", now=0.1) == []
    book.bind_route(session_id="s2", session_key="sk2", chat_id="d", chat_type="dm")
    book.open_turn(turn_id="td", session_id="s2", now=0.0)
    assert book.push_thinking("td", "secret", now=0.1)

def test_usage_ignores_failed_calls():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.add_usage("t", prompt=1000, completion=10, failed=True)
    book.add_usage("t", prompt=200, completion=5, failed=False)
    frames = book.finish_ok("t", now=1.0)
    footer = next(f for f in frames if f.kind == "params").params["footer"]
    assert "in 200 · out 5" in footer

def test_mismatched_turn_is_ignored():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    assert book.push_content("other", "nope", segment_finalize=False, now=1.0) == []

def test_thinking_overflow_no_footer_webhook_content_does():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    thinking_frames = book.push_thinking("t", "t" * 5000, now=0.1)
    card = book.get("t")
    assert not any(f.kind == "webhook" for f in thinking_frames)
    assert "回答过长已截断" not in card.params["footer"]
    assert card.params["thinking"].endswith("…")
    assert len(card.params["thinking"].encode("utf-8")) <= 1024
    capped = card.params["thinking"]
    book.push_thinking("t", "more", now=0.2)
    assert book.get("t").params["thinking"] == capped
    content_frames = book.push_content("t", "c" * 5000, segment_finalize=False, now=0.3)
    assert any(f.kind == "webhook" for f in content_frames)
    assert "回答过长已截断" in book.get("t").params["footer"]
    webhook = next(f for f in content_frames if f.kind == "webhook")
    assert webhook.content

def test_double_finish_is_noop():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.finish_ok("t", now=1.0)
    assert book.finish_error("t", "已停止", now=2.0) == []
