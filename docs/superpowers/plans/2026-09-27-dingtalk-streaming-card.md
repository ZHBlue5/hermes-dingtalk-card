# 钉钉增强版 AI Streaming Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做一个可选的 Hermes 插件，用一张钉钉 AI 卡展示一次回复的状态、工具、思考和回答，并在结束时切换 Header 与 Footer。

**Architecture:** 纯函数负责分类、截断和回合状态；`Coalescer` 负责 400ms 合并；`Publisher` 负责重试、`UpdateCard` 与流式终态的顺序；`EnhancedDingTalkAdapter` 只决定复用还是新建 `out_track_id`。Hook 只改内存并入队，网络调用回到网关的事件循环。

**Tech Stack:** Python 3.11+、pytest、宿主 Hermes Agent 0.21.3、钉钉卡片 REST（流式 `PUT /v1.0/card/streaming`，普通变量 `PUT /v1.0/card/instances`）

**Spec:** `docs/superpowers/specs/2026-09-27-dingtalk-streaming-card-design.md`

## Global Constraints

- 插件目录名 `dingtalk-streaming-card`，`kind: platform`，安装到 `~/.hermes/plugins/dingtalk-streaming-card/` 后执行 `hermes plugins enable dingtalk-streaming-card`
- 子类官方 `DingTalkAdapter`，`register_platform("dingtalk")` 整条替换；官方 `register()` 先打到记录用 ctx，再换上子类工厂
- 插件启用且 `card_template_id` 有值时只发增强卡；缺失卡片 SDK 或模板 ID 时记 ERROR，只走 webhook markdown
- 建卡惰性：首个 `on_stream_delta` 或首个 `pre_tool_call`；不在 `pre_gateway_dispatch` 建卡
- 一张卡对应一个 `turn_id`；`chat_id` 只决定投递会话
- `group_thinking` 默认 true；false 时群聊不写 `thinking`，单聊照常
- 工具列表最近 5 条，新的在上；参数先脱敏再截成一行 80 字符
- 分类顺序：`mcp__` → MCP，`terminal` → Shell，`browser_` → Browser，名字含 `github` → GitHub，其余原名
- 终态只听 `on_session_end`、`agent_loop_stopped`、`api_request_error` 或 `post_tool_call(status=error)`；不听 `on_stream_end`
- 完成顺序：先 `UpdateCard`，再对已流过的 key 发 `isFinalize=true`；内容和上一帧相同也要发
- 失败或中断：`status=error`，`headerColor=danger`；中断 footer 为 `已停止`；`isError` 只打在已经流过的 key 上
- 段边界 `edit_message(finalize=True)` 不写完成态；下一段复用同一 `out_track_id`，不调用 `_close_streaming_siblings`
- 同一 session 新轮：上一张卡 footer `已结束`。不同 session 共用 `chat_id` 时不动对方的卡
- 孤儿卡：没有 `turn_id` 索引、超过 600 秒、下次同一 `chat_id` 建卡时收成 `error`，footer `未完成`
- 长度：流式单帧 1024 字节；`thinking` 累计 1024；`content` 累计 2048；普通变量每个值 1024。按 UTF-8 字符边界截断并补齐未闭合围栏。超出的回答用 webhook 补发
- 节流 400ms；完成帧和失败帧立即发出
- `elapsed` 从本轮首个带 `turn_id` 的 hook 算起，格式如 `12.4s`
- `tokens` 只累加成功的 `post_api_request` usage；没有数字时为 `—`
- `thinking` 与工具参数过 `redact_sensitive_text(..., force=True)`；`browser_type` 的 `text` 再过 `redact_tool_args_for_display`
- 回答正文原样写入，不剥 `---`
- `pre_tool_call` 不做网络、不抛异常来挡工具
- `on_stream_delta` 只入队；队列满 1024 丢最旧；思考区允许缺字
- 流式失败：同一变量重试 1 次，再失败转 `error`；错误帧失败只记日志并留下 `outTrackId`；最终答案再走一次 webhook
- 色条模板固定 `#1677FF`；`headerColor` 仍写入 `info` / `success` / `danger`
- 不改 Hermes 核心，不在运行时创建模板，不做后台定时清扫
- 宿主配置必须是 `display.platforms.dingtalk.streaming: true`、`show_reasoning: false`、`tool_progress: off`，以及 `plugins.stream_reasoning_deltas: true`
- `group_thinking` 写在 `plugins.entries.dingtalk-streaming-card.settings.group_thinking`

## Review Focus

这五条最容易在真实会话里写错卡，每条都有对应任务里的测试：

1. 群里另一个人的卡还在生成，同 `chat_id` 的新回合不得把它收成 `error`（Task 2，`test_other_session_same_chat_untouched`）
2. 段边界的 `finalize=True` 不得把卡标成完成，下一次 `send` 不得新建 `out_track_id`（Task 4，`test_segment_reuses_track`）
3. `pre_tool_call` 收到非 dict 参数或空名字时必须返回，不能抛（Task 5，`test_pre_tool_call_never_raises`）
4. 流式更新第二次仍失败后，用户仍能从 webhook 看到完整回答（Task 3，`test_stream_failure_still_webhooks_answer`）
5. 群聊 `group_thinking: false` 时不写 `thinking`，同一配置下单聊仍写（Task 2，`test_group_thinking_off_skips_group_only`）

## 文件

| 文件 | 职责 |
|---|---|
| `dingtalk-streaming-card/textutil.py` | 分类、脱敏、截断、耗时与 token 文案 |
| `dingtalk-streaming-card/turnbook.py` | 按 `turn_id` 的卡状态与要发出的帧 |
| `dingtalk-streaming-card/coalesce.py` | 400ms 合并，终态立即发出 |
| `dingtalk-streaming-card/publisher.py` | 重试、终态顺序、webhook 补发 |
| `dingtalk-streaming-card/routing.py` | `send` / `edit_message` 复用还是新建 |
| `dingtalk-streaming-card/adapter.py` | 官方适配器子类 |
| `dingtalk-streaming-card/hooks.py` | hook 入队，不发网络 |
| `dingtalk-streaming-card/__init__.py` | `register()` |
| `dingtalk-streaming-card/plugin.yaml` | 清单与 `provides_hooks` |
| `templates/hermes-streaming-card.json` | 模板变量与版面 |
| `templates/README.md` | 手工搭建与宿主配置 |
| `tests/conftest.py` | 把插件目录和 Hermes 源码放进 `sys.path` |
| `tests/test_textutil.py` | Task 1 |
| `tests/test_turnbook.py` | Task 2 |
| `tests/test_publisher.py` | Task 3 |
| `tests/test_routing.py` | Task 4 |
| `tests/test_hooks.py` | Task 5 |

帧类型全任务共用，定义在 `turnbook.py`：

```python
@dataclass(frozen=True)
class Frame:
    chat_id: str
    turn_id: str
    out_track_id: str
    kind: str  # "params" | "stream" | "webhook"
    key: str = ""
    content: str = ""
    params: dict | None = None
    is_finalize: bool = False
    is_error: bool = False
    immediate: bool = False
```

---

### Task 1: 文案与截断

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_textutil.py`
- Create: `dingtalk-streaming-card/textutil.py`
- Test: `tests/test_textutil.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `classify_tool(name: str) -> tuple[str, str]`，返回 `(标签, 显示名)`
  - `tool_line(name: str, args: object, *, running: bool) -> str`
  - `fit_stream(text: str, limit: int) -> tuple[str, str]`，返回 `(卡片内文本, 被截掉的尾部)`
  - `fit_param(text: str) -> str`，最多 1024 字节
  - `format_elapsed(seconds: float) -> str`
  - `format_tokens(prompt: int, completion: int) -> str`
  - `redact_text(text: str) -> str`

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`：

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dingtalk-streaming-card"))
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
```

`tests/test_textutil.py`：

```python
from textutil import classify_tool, fit_stream, format_elapsed, format_tokens, tool_line

def test_mcp_github_is_mcp_not_github():
    label, shown = classify_tool("mcp__github__create_issue")
    assert (label, shown) == ("MCP", "github.create_issue")

def test_classify_order():
    assert classify_tool("terminal")[0] == "Shell"
    assert classify_tool("browser_navigate")[0] == "Browser"
    assert classify_tool("github_search")[0] == "GitHub"
    assert classify_tool("read_file") == ("read_file", "read_file")

def test_tool_line_redacts_then_clips_to_one_line():
    line = tool_line("terminal", {"cmd": "echo sk-abcdefghijklmnopqrstuvwxyz\nnext"}, running=True)
    assert "\n" not in line
    assert line.startswith("Shell · ")
    assert "进行中" in line
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in line
    assert len(line.split("·", 1)[1].strip()) <= 80 or "…" in line

def test_five_is_not_this_modules_job():
    assert callable(tool_line)

def test_fit_stream_char_boundary_and_fence():
    text = "你好" + "x" * 3000 + "\n```python\nprint(1)\n"
    shown, rest = fit_stream(text, 1024)
    assert len(shown.encode()) <= 1024
    assert shown.encode().decode() == shown
    assert shown.rstrip().endswith("```")
    assert rest
    assert "…" in shown

def test_content_and_thinking_budgets_are_independent():
    thinking, _ = fit_stream("t" * 5000, 1024)
    content, _ = fit_stream("c" * 5000, 2048)
    assert len(thinking.encode()) <= 1024
    assert len(content.encode()) <= 2048

def test_elapsed_and_tokens():
    assert format_elapsed(12.44) == "12.4s"
    assert format_tokens(1200, 340) == "in 1.2k · out 340"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_textutil.py -v`

Expected: FAIL，`ModuleNotFoundError: textutil`

- [ ] **Step 3: Write minimal implementation**

`dingtalk-streaming-card/textutil.py` 实现上列函数。分类按 Global Constraints 的顺序。`tool_line` 把 args 收成一行：dict 用 `key=value` 以空格连接，其他类型 `str(args)`；每个值先 `redact_text`；名字是 `browser_type` 且键为 `text` 时再调用 `agent.display.redact_tool_args_for_display`。整行去掉换行后，参数部分最多 80 个字符，超出加 `…`。`running=True` 时行尾加 ` 进行中`。

`redact_text` 调用 `agent.redact.redact_sensitive_text(text, force=True)`。

`fit_stream`：从末尾按 UTF-8 字符删到 `limit` 以内，若发生截断则先留出 `…` 的字节再追加 `…`。若截断后有未闭合的 ` ``` `，在预算内补上闭合围栏；预算不够时先删正文再补围栏。返回的尾部是被截掉的原文后缀，没截断时尾部为 `""`。

`format_tokens`：`>= 1000` 的数写成一位小数加 `k`（1200 → `1.2k`），否则十进制。`format_elapsed` 固定一位小数加 `s`。`fit_param` 用 `fit_stream(text, 1024)[0]`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_textutil.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_textutil.py dingtalk-streaming-card/textutil.py
git commit -m "feat: add streaming card text helpers

AI-generated: yes"
```

---

### Task 2: 回合状态

**Files:**
- Create: `dingtalk-streaming-card/turnbook.py`
- Create: `tests/test_turnbook.py`
- Test: `tests/test_turnbook.py`

**Interfaces:**
- Consumes: `textutil.classify_tool`、`tool_line`、`fit_stream`、`fit_param`、`format_elapsed`、`format_tokens`、`Frame`
- Produces: `TurnBook`，方法见本任务实现；`Frame` 如文件表

- [ ] **Step 1: Write the failing test**

```python
from turnbook import TurnBook

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

def test_double_finish_is_noop():
    book = _book()
    book.bind_route(session_id="s", session_key="sk", chat_id="c", chat_type="dm")
    book.open_turn(turn_id="t", session_id="s", now=0.0)
    book.finish_ok("t", now=1.0)
    assert book.finish_error("t", "已停止", now=2.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_turnbook.py -v`

Expected: FAIL，`ModuleNotFoundError: turnbook`

- [ ] **Step 3: Write minimal implementation**

`TurnBook` 在内存里保存：

- `routes[session_id] = (session_key, chat_id, chat_type)`
- `turns[turn_id] = Card`，`Card` 含 `out_track_id`、`session_key`、`status`、`started`、`params`、`thinking`、`content`、`streamed_keys`、`tools`（最多 5 条，插入在列表头）
- `tracks[out_track_id] = turn_id`，`drop_turn_index` 只从 `turns` 删除，留下 `tracks` 以便孤儿卡仍可被找到
- `delivered` 默认为 False，只有 `mark_delivered` 把它设为 True

`open_turn`：若同一 `session_key` 已有 `running` 卡，先把那张收成 `error` / `已结束` 并产出 params 帧。然后若此 `chat_id` 上有 `tracks` 里不再属于 `turns`、且 `now - started >= 600` 的卡，收成 `未完成`。新卡 `status=running`，`headerColor=info`，`footer=生成中`，`title=Hermes`，`model=—`，`tokens=—`，`out_track_id` 用 `hermes_` 加 12 位十六进制。`elapsed` 用 `now - started`。

`push_thinking` / `push_content` 把文本交给 `fit_stream`（1024 / 2048）。有尾部时在 footer 末尾加上 ` 回答过长已截断`（只加一次）并额外产出一条 `kind=webhook`、`content` 为尾部的帧。`segment_finalize=True` 只更新内容，不置 `is_finalize`。群聊且 `group_thinking` 为 false 时 `push_thinking` 返回 `[]`。

`finish_ok` / `finish_error` 在 `status != running` 时返回 `[]`。params 帧 `immediate=True`。只给 `streamed_keys` 里的 key 发 stream 帧；`finish_error` 且 footer 不是调用方传入的短错误时仍使用传入 footer。`is_error` 仅 `finish_error` 且该 key 已流过。

`add_usage` 在 `failed=False` 时累加。`finish_ok` 的 tokens 用累加值；从未成功则 `—`。

未知 `turn_id` 的 push / finish 返回 `[]`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_turnbook.py tests/test_textutil.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dingtalk-streaming-card/turnbook.py tests/test_turnbook.py
git commit -m "feat: track one DingTalk card per turn

AI-generated: yes"
```

---

### Task 3: 合并、重试与 webhook

**Files:**
- Create: `dingtalk-streaming-card/coalesce.py`
- Create: `dingtalk-streaming-card/publisher.py`
- Create: `tests/test_publisher.py`
- Test: `tests/test_publisher.py`

**Interfaces:**
- Consumes: `turnbook.Frame`
- Produces:
  - `Coalescer.push(frame: Frame, now: float) -> list[Frame]`
  - `Coalescer.flush(now: float, *, force: bool = False) -> list[Frame]`
  - `class CardApi`：`async stream(...)`、`async update_params(...)`、`async webhook(...)`
  - `Publisher.send(api, frames: list[Frame]) -> None`
  - 模块常量 `TERMINAL_ORDER = ("params", "stream")`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_publisher.py -v`

Expected: FAIL，`ModuleNotFoundError: coalesce`

- [ ] **Step 3: Write minimal implementation**

`Coalescer` 按 `(out_track_id, kind, key)` 保存最后一帧。`immediate=True` 或 `is_finalize` 或 `is_error` 的帧先 `flush(force=True)` 再原样返回。普通帧在 `now - first_queued < interval` 时不发出。

`Publisher.send` 按 `TERMINAL_ORDER` 分组排序后逐帧发送。`stream` 失败再试 1 次；仍失败则构造一帧 params：`status=error`、`headerColor=danger`、`footer` 为异常信息的第一行（最多 80 字符），再 webhook 该帧的 `content`。若这次 params 或 `is_error` 帧本身失败，打日志包含 `out_track_id` 并停止后续帧。成功的 stream 不额外 webhook。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_publisher.py tests/test_turnbook.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dingtalk-streaming-card/coalesce.py dingtalk-streaming-card/publisher.py tests/test_publisher.py
git commit -m "feat: coalesce card frames and retry streams once

AI-generated: yes"
```

---

### Task 4: 复用卡片还是新建

**Files:**
- Create: `dingtalk-streaming-card/routing.py`
- Create: `tests/test_routing.py`
- Test: `tests/test_routing.py`

**Interfaces:**
- Consumes: `TurnBook.get`、`TurnBook.card_by_track`
- Produces:
  - `route_send(book, *, chat_id: str, turn_id: str) -> str`，返回 `"reuse"`、`"create"` 或 `"webhook"`
  - `route_edit(book, *, turn_id: str, finalize: bool) -> str`，返回 `"store"` 或 `"ignore"`
  - `TurnBook.mark_delivered(turn_id: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routing.py::test_segment_reuses_track -v`

Expected: FAIL，`ModuleNotFoundError: routing`

- [ ] **Step 3: Write minimal implementation**

`route_send`：没有 `turn_id`，或卡的 `chat_id` 不同 → `"webhook"`。卡存在、`status=="running"` 且 `delivered` → `"reuse"`。卡存在、`status=="running"` 且尚未 `mark_delivered` → `"create"`。`mark_delivered` 只把该卡的 `delivered` 设为 True，不改 `status`。

`route_edit`：卡不存在或 `status!="running"` → `"ignore"`。否则 `"store"`。这个函数不改 `status`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routing.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dingtalk-streaming-card/routing.py tests/test_routing.py
git commit -m "feat: reuse the open card across segment boundaries

AI-generated: yes"
```

---

### Task 5: Hook 入队与插件注册

**Files:**
- Create: `dingtalk-streaming-card/hooks.py`
- Create: `dingtalk-streaming-card/adapter.py`
- Create: `dingtalk-streaming-card/__init__.py`
- Create: `dingtalk-streaming-card/plugin.yaml`
- Create: `tests/test_hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `TurnBook`、`route_send`、`route_edit`、`Coalescer`、`Publisher`
- Produces:
  - `HookBridge.on_stream_delta(**kwargs) -> None`
  - `HookBridge.pre_tool_call(**kwargs) -> None`
  - `HookBridge.post_tool_call(**kwargs) -> None`
  - `HookBridge.post_api_request(**kwargs) -> None`
  - `HookBridge.on_session_end(**kwargs) -> None`
  - `HookBridge.agent_loop_stopped(**kwargs) -> None`
  - `HookBridge.api_request_error(**kwargs) -> None`
  - `HookBridge.pre_gateway_dispatch(**kwargs) -> None`
  - `HookBridge.on_stream_end(**kwargs) -> None` 不收口
  - `register(ctx) -> None`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hooks.py -v`

Expected: FAIL，`ModuleNotFoundError: hooks`

- [ ] **Step 3: Write minimal implementation**

`HookBridge` 持有 `TurnBook`。所有 hook 用 `try/except` 包住并打日志，异常不向外抛。`push_thinking`、`tool_started` 之前若该 `turn_id` 还没有卡，先 `open_turn`。`pre_tool_call` 把非 dict 的 args 当成 `{}`，缺 `turn_id` 时直接返回。`pre_gateway_dispatch` 只 `bind_route`。`on_stream_delta` 在 `kind=="reasoning"` 时 `push_thinking`，`kind=="text"` 时不写卡（回答以适配器的 `send`/`edit_message` 为准）。`on_stream_end` 空操作。`on_session_end`：`interrupted` → `finish_error(..., "已停止")`；`failed` → `finish_error`，footer 为 `error` 或 `reason` 的第一行，缺省 `失败`；`completed` → `finish_ok`。`agent_loop_stopped` 找该 `session_key` 上所有 `running` 卡并 `finish_error(..., "已停止")`。`api_request_error` 与 `post_tool_call(status="error")` 同样 `finish_error`，footer 用错误信息第一行。`post_api_request` 从 `usage` 或 `response` 摘要里取 `prompt_tokens` 与 `completion_tokens`（没有 completion 时用 `total_tokens - prompt_tokens`），`failed=finish_reason in {"error", "length"}` 时传 `failed=True`。

`loop is None` 时只更新 `TurnBook`，不调度协程。`loop` 不为 None 时用 `asyncio.run_coroutine_threadsafe` 把 `Coalescer` 产出的帧交给 `Publisher`。

`adapter.py`：`EnhancedDingTalkAdapter(DingTalkAdapter)`。`send` 用当前线程上 `HookBridge` 的活跃 `turn_id`（bridge 提供 `active_turn(chat_id)`，取该 chat 上最新的 running turn）。`route_send=="reuse"` 时不调用 `_close_streaming_siblings` 和 `_create_and_stream_card`，把正文 `push_content`。`route_send=="create"` 时调用父类建卡，但 `card_param_map` 用 `TurnBook` 的全量 params，流式 key 先写 `content`。`route_send=="webhook"` 走父类 webhook 分支。`edit_message(..., finalize=True)` 在 `route_edit=="store"` 时只 `push_content(segment_finalize=True)`。`connect` 里探测 `import alibabacloud_dingtalk`，失败或 `card_template_id` 为空时 `logger.error` 提示 `pip install alibabacloud-dingtalk` 或配置模板 ID，并令 `self._enhanced = False`。

`__init__.py` 的 `register`：定义只实现 `register_platform` 并把 kwargs 存进列表的 stub，调用 `plugins.platforms.dingtalk.adapter.register(stub)`，把 `adapter_factory` 换成 `EnhancedDingTalkAdapter`，再 `ctx.register_platform(**kwargs)`。返回 None 时 `logger.error`。同时 `ctx.register_hook` 注册上面每个 hook 名。`plugin.yaml` 的 `provides_hooks` 列出：`pre_gateway_dispatch`、`on_stream_delta`、`on_stream_end`、`pre_tool_call`、`post_tool_call`、`post_api_request`、`on_session_end`、`agent_loop_stopped`、`api_request_error`。`config_schema` 含 `group_thinking: {type: bool, default: true}`。

普通变量更新用：

```python
async def update_params(self, token: str, out_track_id: str, params: dict) -> None:
    payload = {"outTrackId": out_track_id, "cardData": {"cardParamMap": params}}
    resp = await self._http_client.put(
        "https://api.dingtalk.com/v1.0/card/instances",
        headers={"x-acs-dingtalk-access-token": token},
        json=payload,
        timeout=15.0,
    )
    resp.raise_for_status()
```

流式沿用父类 `_stream_card_content` 的请求体，但 `key` 由帧指定，`is_full=True`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hooks.py tests/test_routing.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dingtalk-streaming-card tests/test_hooks.py
git commit -m "feat: register enhanced DingTalk card plugin hooks

AI-generated: yes"
```

---

### Task 6: 模板与安装说明

**Files:**
- Create: `templates/hermes-streaming-card.json`
- Create: `templates/README.md`
- Create: `tests/test_template.py`
- Test: `tests/test_template.py`

**Interfaces:**
- Consumes: 规格里的变量表与色值 `#1677FF`、`#00B578`、`#F53F3F`
- Produces: 模板描述文件与安装步骤

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

def test_template_lists_every_variable():
    data = json.loads(Path("templates/hermes-streaming-card.json").read_text())
    keys = set(data["variables"])
    assert keys == {
        "status", "headerColor", "title", "model", "elapsed", "tool",
        "toolCount", "tokens", "thinking", "content", "footer",
    }
    assert data["streaming"] == ["thinking", "content"]
    assert data["header"]["fallbackColor"] == "#1677FF"
    assert "streaming: true" in Path("templates/README.md").read_text()
    assert "tool_progress: off" in Path("templates/README.md").read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_template.py -v`

Expected: FAIL，`FileNotFoundError`

- [ ] **Step 3: Write minimal implementation**

JSON 含 `variables`、`streaming`、`layout`（色条、标题/模型/耗时、工具、思考、回答、Footer）、`header.fallbackColor` 以及 `header.bindings`：`success=#00B578`、`danger=#F53F3F`，并写明 v1 色条用 fallback，bindings 仅在控制台支持样式绑定时启用。

README 写：软链到 `~/.hermes/plugins/dingtalk-streaming-card`、`hermes plugins enable`、规格里的两段 yaml、`pip install alibabacloud-dingtalk`、控制台手工建模板时变量名必须一致、换模板必须换 `card_template_id`。另附 spike 步骤：用双变量模板依次打一帧 1024 字节流式、一次 `UpdateCard`、一次 `isFinalize`，记录上限以及 `UpdateCard` 是否清缓冲。若清缓冲，把 `publisher.TERMINAL_ORDER` 改成 `("stream", "params")` 并改规格状态机第 5 步。spike 不在 pytest 里跑。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates tests/test_template.py
git commit -m "docs: add DingTalk streaming card template and setup

AI-generated: yes"
```
