"""Send/edit routing for DingTalk streaming cards."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .turnbook import TurnBook


def route_send(book: TurnBook, *, chat_id: str, turn_id: str) -> str:
    if not turn_id:
        return "webhook"
    try:
        card = book.get(turn_id)
    except KeyError:
        return "webhook"
    if card.chat_id != chat_id:
        return "webhook"
    if card.status == "running" and card.delivered:
        return "reuse"
    if card.status == "running":
        return "create"
    return "webhook"


def route_edit(book: TurnBook, *, turn_id: str, finalize: bool) -> str:
    try:
        card = book.get(turn_id)
    except KeyError:
        return "ignore"
    if card.status != "running":
        return "ignore"
    return "store"
