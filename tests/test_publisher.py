import asyncio
from turnbook import Frame
from coalesce import Coalescer
from publisher import Publisher, TERMINAL_ORDER

class Api:
    def __init__(self, fail_stream=0, fail_error=False):
        self.calls = []
        self.fail_stream = fail_stream
        self.fail_error = fail_error
    async def stream(self, frame):
        self.calls.append(("stream", frame.key, frame.is_finalize, frame.is_error, frame.content))
        if frame.is_error and self.fail_error:
            raise RuntimeError("error frame")
        if self.fail_stream:
            self.fail_stream -= 1
            raise RuntimeError("stream")
    async def update_params(self, frame):
        self.calls.append(("params", frame.params["status"]))
    async def webhook(self, frame):
        self.calls.append(("webhook", frame.content))

def test_coalesce_merges_same_key_inside_window():
    c = Coalescer(interval=0.4)
    a = Frame("c", "t", "o", "stream", key="content", content="a")
    b = Frame("c", "t", "o", "stream", key="content", content="ab")
    assert c.push(a, 0.0) == []
    assert c.push(b, 0.2) == []
    out = c.flush(0.4, force=False)
    assert [f.content for f in out] == ["ab"]

def test_terminal_frame_is_not_delayed():
    c = Coalescer(interval=0.4)
    done = Frame("c", "t", "o", "params", params={"status": "done"}, immediate=True)
    assert c.push(done, 0.1) == [done]

def test_terminal_order_constant():
    assert TERMINAL_ORDER == ("params", "stream")

def test_stream_retries_once_then_errors():
    api = Api(fail_stream=2)
    pub = Publisher()
    frame = Frame("c", "t", "o", "stream", key="content", content="answer")
    asyncio.run(pub.send(api, [frame]))
    kinds = [call[0] for call in api.calls]
    assert kinds.count("stream") == 2
    assert "params" in kinds
    assert api.calls[-1][0] == "webhook"
    assert api.calls[-1][1] == "answer"

def test_stream_failure_still_webhooks_answer():
    api = Api(fail_stream=2)
    pub = Publisher()
    frame = Frame("c", "t", "track1", "stream", key="content", content="全文回答")
    asyncio.run(pub.send(api, [frame]))
    assert ("webhook", "全文回答") in api.calls

def test_error_frame_failure_logs_and_stops(caplog):
    api = Api(fail_error=True)
    pub = Publisher()
    bad = Frame("c", "t", "track9", "stream", key="content", content="x", is_error=True, immediate=True)
    asyncio.run(pub.send(api, [bad, Frame("c", "t", "track9", "stream", key="thinking", content="y")]))
    assert any("track9" in rec.message for rec in caplog.records)
    assert [call[1] for call in api.calls if call[0] == "stream"] == ["content"]
