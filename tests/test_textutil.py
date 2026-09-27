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
