"""Streaming card text helpers: classification, redaction, truncation, formatting."""

from __future__ import annotations

from agent.display import redact_tool_args_for_display
from agent.redact import redact_sensitive_text

_ELLIPSIS = "…"
_CLOSE_FENCE = "\n```"


def classify_tool(name: str) -> tuple[str, str]:
    """Return (label, display name) for a tool."""
    if name.startswith("mcp__"):
        rest = name[5:]
        if "__" in rest:
            server, tool = rest.split("__", 1)
            return ("MCP", f"{server}.{tool}")
        return ("MCP", rest)
    if name == "terminal":
        return ("Shell", name)
    if name.startswith("browser_"):
        return ("Browser", name)
    if "github" in name.lower():
        return ("GitHub", name)
    return (name, name)


def redact_text(text: str) -> str:
    """Redact sensitive substrings for display."""
    return redact_sensitive_text(text, force=True)


def _utf8_prefix(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _fences_open(text: str) -> bool:
    return text.count("```") % 2 == 1


def fit_stream(text: str, limit: int) -> tuple[str, str]:
    """Return (card text, truncated suffix). Truncates on UTF-8 boundaries."""
    raw = text if isinstance(text, str) else str(text)
    if len(raw.encode("utf-8")) <= limit:
        return (raw, "")

    ell_b = len(_ELLIPSIS.encode("utf-8"))
    close_b = len(_CLOSE_FENCE.encode("utf-8"))
    need_close = _fences_open(raw)

    prefix_budget = limit - ell_b - (close_b if need_close else 0)
    if prefix_budget < 0:
        prefix_budget = 0
    prefix = _utf8_prefix(raw, prefix_budget)
    shown = prefix + _ELLIPSIS
    if need_close:
        shown = prefix + _ELLIPSIS + _CLOSE_FENCE

    while len(shown.encode("utf-8")) > limit and prefix_budget > 0:
        prefix_budget -= 1
        prefix = _utf8_prefix(raw, prefix_budget)
        shown = prefix + _ELLIPSIS
        if need_close:
            shown = prefix + _ELLIPSIS + _CLOSE_FENCE

    if _fences_open(prefix) and not need_close:
        prefix_budget = limit - ell_b - close_b
        if prefix_budget < 0:
            prefix_budget = 0
        prefix = _utf8_prefix(raw, prefix_budget)
        shown = prefix + _ELLIPSIS + _CLOSE_FENCE
        while len(shown.encode("utf-8")) > limit and prefix_budget > 0:
            prefix_budget -= 1
            prefix = _utf8_prefix(raw, prefix_budget)
            shown = prefix + _ELLIPSIS + _CLOSE_FENCE

    rest = raw[len(prefix) :]
    return (shown, rest)


def fit_param(text: str) -> str:
    """Fit a card param value to 1024 bytes."""
    return fit_stream(text, 1024)[0]


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds for the card header."""
    return f"{seconds:.1f}s"


def _format_token_count(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def format_tokens(prompt: int, completion: int) -> str:
    """Format token usage for the card footer."""
    return f"in {_format_token_count(prompt)} · out {_format_token_count(completion)}"


def _args_one_line(name: str, args: object) -> str:
    if isinstance(args, dict):
        parts: list[str] = []
        for key, value in args.items():
            piece = redact_text(str(value))
            if name == "browser_type" and key == "text":
                redacted = redact_tool_args_for_display(name, {"text": piece})
                if isinstance(redacted, dict) and isinstance(redacted.get("text"), str):
                    piece = redacted["text"]
            parts.append(f"{key}={piece}")
        line = " ".join(parts)
    else:
        line = redact_text(str(args))
    return line.replace("\n", " ").replace("\r", " ")


def tool_line(name: str, args: object, *, running: bool) -> str:
    """Format one tool row for the card tool list."""
    label, shown = classify_tool(name)
    params = _args_one_line(name, args)
    if len(params) > 80:
        params = params[:80] + "…"
    # 展示 classify_tool 的 display name（如 github.create_issue / browser_navigate）
    line = f"{label} · {shown} · {params}" if params else f"{label} · {shown}"
    if running:
        line += " 进行中"
    return line
