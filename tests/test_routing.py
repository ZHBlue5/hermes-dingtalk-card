from turnbook import TurnBook
from routing import route_edit, route_send

def test_segment_reuses_track():
    book = TurnBook(group_thinking=True)
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    assert route_send(book, chat_id="c", turn_id="t") == "create"
    book.mark_delivered("t")
    assert route_send(book, chat_id="c", turn_id="t") == "reuse"
    assert route_edit(book, turn_id="t", finalize=True) == "store"
    assert book.get("t").status == "running"

def test_unknown_turn_webhooks():
    book = TurnBook(group_thinking=True)
    assert route_send(book, chat_id="c", turn_id="") == "webhook"

def test_closed_turn_does_not_reopen():
    book = TurnBook(group_thinking=True)
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.finish_ok("t", now=1.0)
    assert route_edit(book, turn_id="t", finalize=True) == "ignore"
