"""In-memory turn state and streaming card frames for DingTalk."""

from __future__ import annotations

import secrets
from collections import defaultdict
from dataclasses import dataclass, field

from textutil import fit_param, fit_stream, format_elapsed, format_tokens, tool_line

_TRUNC_FOOTER = " 回答过长已截断"
_ORPHAN_SECONDS = 600.0
_THINKING_LIMIT = 1024
_CONTENT_LIMIT = 2048
_MAX_TOOLS = 5


@dataclass(frozen=True)
class Frame:
    """One outbound card update or stream chunk."""

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


@dataclass
class _ToolRow:
    tool_id: str
    name: str
    args: object
    running: bool


@dataclass
class Card:
    """Mutable card state for one turn."""

    turn_id: str
    out_track_id: str
    session_key: str
    chat_id: str
    chat_type: str
    status: str
    started: float
    params: dict
    thinking: str = ""
    content: str = ""
    streamed_keys: set[str] = field(default_factory=set)
    tools: list[_ToolRow] = field(default_factory=list)
    delivered: bool = False
    usage_prompt: int = 0
    usage_completion: int = 0
    truncated_footer: bool = False
    tool_total: int = 0


class TurnBook:
    """Tracks one DingTalk streaming card per turn."""

    def __init__(self, *, group_thinking: bool = True) -> None:
        self.group_thinking = group_thinking
        self.routes: dict[str, tuple[str, str, str]] = {}
        self.turns: dict[str, Card] = {}
        self._cards: dict[str, Card] = {}
        self.tracks: dict[str, str] = {}
        self._chat_tracks: dict[str, set[str]] = defaultdict(set)
        self._session_running: dict[str, str] = {}

    def bind_route(
        self,
        *,
        session_id: str,
        session_key: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        self.routes[session_id] = (session_key, chat_id, chat_type)

    def get(self, turn_id: str) -> Card:
        card = self.turns.get(turn_id)
        if card is not None:
            return card
        card = self._cards.get(turn_id)
        if card is None:
            raise KeyError(turn_id)
        return card

    def card_by_track(self, out_track_id: str) -> Card:
        turn_id = self.tracks[out_track_id]
        return self._cards[turn_id]

    def mark_delivered(self, turn_id: str) -> None:
        self.get(turn_id).delivered = True

    def drop_turn_index(self, turn_id: str) -> None:
        self.turns.pop(turn_id, None)
        card = self._cards.get(turn_id)
        if card is not None:
            self._session_running.pop(card.session_key, None)

    def open_turn(self, *, turn_id: str, session_id: str, now: float) -> list[Frame]:
        session_key, chat_id, chat_type = self.routes[session_id]
        frames: list[Frame] = []

        prev_id = self._session_running.get(session_key)
        if prev_id is not None and prev_id != turn_id:
            prev = self.turns.get(prev_id)
            if prev is not None and prev.status == "running":
                frames.extend(self._finish_card(prev, "error", "danger", "已结束", now, error_streams=False))

        for track_id in list(self._chat_tracks.get(chat_id, ())):
            orphan_tid = self.tracks.get(track_id)
            if orphan_tid is None or orphan_tid in self.turns:
                continue
            orphan = self._cards.get(orphan_tid)
            if orphan is None or orphan.status != "running":
                continue
            if now - orphan.started < _ORPHAN_SECONDS:
                continue
            orphan.status = "error"
            orphan.params["status"] = "error"
            orphan.params["headerColor"] = "danger"
            orphan.params["footer"] = "未完成"
            orphan.params["elapsed"] = format_elapsed(now - orphan.started)

        out_track_id = f"hermes_{secrets.token_hex(6)}"
        params = {
            "status": "running",
            "headerColor": "info",
            "footer": "生成中",
            "title": "Hermes",
            "model": "—",
            "tokens": "—",
            "elapsed": format_elapsed(now - now),
            "tool": "",
            "toolCount": "0",
            "thinking": "",
            "content": "",
        }
        card = Card(
            turn_id=turn_id,
            out_track_id=out_track_id,
            session_key=session_key,
            chat_id=chat_id,
            chat_type=chat_type,
            status="running",
            started=now,
            params=params,
        )
        self.turns[turn_id] = card
        self._cards[turn_id] = card
        self.tracks[out_track_id] = turn_id
        self._chat_tracks[chat_id].add(out_track_id)
        self._session_running[session_key] = turn_id
        return frames

    def push_thinking(self, turn_id: str, text: str, *, now: float) -> list[Frame]:
        card = self.turns.get(turn_id)
        if card is None or card.status != "running":
            return []
        if card.chat_type == "group" and not self.group_thinking:
            return []
        card.thinking += text
        return self._push_body(card, "thinking", _THINKING_LIMIT)

    def push_content(
        self,
        turn_id: str,
        text: str,
        *,
        segment_finalize: bool,
        now: float,
    ) -> list[Frame]:
        card = self.turns.get(turn_id)
        if card is None or card.status != "running":
            return []
        card.content += text
        return self._push_body(card, "content", _CONTENT_LIMIT)

    def tool_started(
        self,
        turn_id: str,
        tool_id: str,
        name: str,
        args: object,
        *,
        now: float,
    ) -> list[Frame]:
        card = self.turns.get(turn_id)
        if card is None or card.status != "running":
            return []
        row = _ToolRow(tool_id=tool_id, name=name, args=args, running=True)
        card.tools.insert(0, row)
        card.tool_total += 1
        if len(card.tools) > _MAX_TOOLS:
            card.tools = card.tools[:_MAX_TOOLS]
        self._sync_tools(card)
        return []

    def tool_finished(self, turn_id: str, tool_id: str, *, now: float) -> list[Frame]:
        card = self.turns.get(turn_id)
        if card is None or card.status != "running":
            return []
        for row in card.tools:
            if row.tool_id == tool_id:
                row.running = False
                break
        self._sync_tools(card)
        return []

    def add_usage(
        self,
        turn_id: str,
        *,
        prompt: int,
        completion: int,
        failed: bool,
    ) -> None:
        card = self.turns.get(turn_id)
        if card is None or failed:
            return
        card.usage_prompt += prompt
        card.usage_completion += completion

    def finish_ok(self, turn_id: str, *, now: float) -> list[Frame]:
        card = self.turns.get(turn_id)
        if card is None or card.status != "running":
            return []
        if card.usage_prompt or card.usage_completion:
            tokens = format_tokens(card.usage_prompt, card.usage_completion)
        else:
            tokens = "—"
        elapsed = format_elapsed(now - card.started)
        footer = f"已完成 · {elapsed} · {tokens}"
        return self._finish_card(
            card,
            "done",
            "success",
            footer,
            now,
            error_streams=False,
            tokens=tokens,
        )

    def finish_error(self, turn_id: str, footer: str, *, now: float) -> list[Frame]:
        card = self.turns.get(turn_id)
        if card is None or card.status != "running":
            return []
        return self._finish_card(
            card,
            "error",
            "danger",
            footer,
            now,
            error_streams=True,
        )

    def _sync_tools(self, card: Card) -> None:
        lines = [
            tool_line(row.name, row.args, running=row.running) for row in card.tools
        ]
        card.params["tool"] = fit_param("\n".join(lines))
        card.params["toolCount"] = str(card.tool_total)

    def _push_body(
        self,
        card: Card,
        key: str,
        limit: int,
    ) -> list[Frame]:
        raw = card.thinking if key == "thinking" else card.content
        shown, rest = fit_stream(raw, limit)
        card.params[key] = shown
        frames: list[Frame] = []
        if rest:
            frames.extend(self._maybe_trunc_footer(card, rest))
        card.streamed_keys.add(key)
        frames.append(
            Frame(
                card.chat_id,
                card.turn_id,
                card.out_track_id,
                "stream",
                key=key,
                content=shown,
                is_finalize=False,
            )
        )
        return frames

    def _maybe_trunc_footer(self, card: Card, rest: str) -> list[Frame]:
        if not card.truncated_footer:
            card.truncated_footer = True
            card.params["footer"] = card.params["footer"] + _TRUNC_FOOTER
        return [
            Frame(
                card.chat_id,
                card.turn_id,
                card.out_track_id,
                "webhook",
                content=rest,
            )
        ]

    def _finish_card(
        self,
        card: Card,
        status: str,
        header_color: str,
        footer: str,
        now: float,
        *,
        error_streams: bool,
        tokens: str | None = None,
    ) -> list[Frame]:
        card.status = status
        card.params["status"] = status
        card.params["headerColor"] = header_color
        card.params["footer"] = footer
        card.params["elapsed"] = format_elapsed(now - card.started)
        if tokens is not None:
            card.params["tokens"] = tokens
        self._session_running.pop(card.session_key, None)
        frames: list[Frame] = [
            Frame(
                card.chat_id,
                card.turn_id,
                card.out_track_id,
                "params",
                params=dict(card.params),
                immediate=True,
            )
        ]
        for key in sorted(card.streamed_keys):
            content = card.params.get(key, "")
            frames.append(
                Frame(
                    card.chat_id,
                    card.turn_id,
                    card.out_track_id,
                    "stream",
                    key=key,
                    content=content,
                    is_finalize=True,
                    is_error=error_streams,
                )
            )
        return frames
