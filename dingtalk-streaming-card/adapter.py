"""Enhanced DingTalk adapter: one streaming card per turn.

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from typing import Any, Dict, Optional, Tuple

from gateway.platforms.base import SendResult
from plugins.platforms.dingtalk.adapter import DingTalkAdapter
from routing import route_edit, route_send
from turnbook import Frame

logger = logging.getLogger(__name__)


class EnhancedDingTalkAdapter(DingTalkAdapter):
    """子类官方适配器：复用/新建 out_track_id，并补 UpdateCard REST。"""

    def __init__(self, config, bridge=None):
        """注入共享 HookBridge。"""
        super().__init__(config)
        self._bridge = bridge
        self._enhanced = True

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """探测卡片 SDK / 模板 ID；缺失时降级 webhook。"""
        ok = await super().connect(is_reconnect=is_reconnect)
        sdk_ok = False
        try:
            import alibabacloud_dingtalk  # noqa: F401

            sdk_ok = True
        except Exception:
            sdk_ok = False
        if not sdk_ok:
            logger.error(
                "[%s] Enhanced streaming card disabled: install with "
                "`pip install alibabacloud-dingtalk`",
                self.name,
            )
            self._enhanced = False
        elif not self._card_template_id:
            logger.error(
                "[%s] Enhanced streaming card disabled: configure card_template_id",
                self.name,
            )
            self._enhanced = False
        else:
            self._enhanced = True
            logger.info(
                "[%s] Enhanced DingTalk streaming card enabled (template=%s)",
                self.name,
                self._card_template_id,
            )
        if ok and self._bridge is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            self._bridge.set_loop(loop, api=self)
        return ok

    async def update_params(
        self,
        token: str | Frame | None = None,
        out_track_id: str | None = None,
        params: dict | None = None,
    ) -> None:
        """普通变量：PUT /v1.0/card/instances。

        也满足 CardApi：``update_params(frame)``。
        """
        if isinstance(token, Frame):
            frame = token
            access = await self._get_access_token()
            if not access:
                raise RuntimeError("No access token for card params")
            await self._put_card_params(access, frame.out_track_id, frame.params or {})
            return
        if not token or out_track_id is None or params is None:
            raise TypeError("update_params(token, out_track_id, params) required")
        await self._put_card_params(token, out_track_id, params)

    async def _put_card_params(self, token: str, out_track_id: str, params: dict) -> None:
        payload = {"outTrackId": out_track_id, "cardData": {"cardParamMap": params}}
        resp = await self._http_client.put(
            "https://api.dingtalk.com/v1.0/card/instances",
            headers={"x-acs-dingtalk-access-token": token},
            json=payload,
            timeout=15.0,
        )
        resp.raise_for_status()

    async def stream(self, frame: Frame) -> None:
        """CardApi：流式更新，key 由帧指定，is_full=True。"""
        token = await self._get_access_token()
        if not token:
            raise RuntimeError("No access token for card stream")
        await self._stream_card_key(
            frame.out_track_id,
            token,
            frame.key or "content",
            frame.content,
            finalize=frame.is_finalize,
            is_error=frame.is_error,
        )

    async def webhook(self, frame: Frame) -> None:
        """CardApi：超长正文 webhook 补发（不走官方 send，避免再建官方卡）。"""
        result = await self._send_markdown_webhook(frame.chat_id, frame.content)
        if not result.success:
            raise RuntimeError(result.error or "webhook markdown failed")

    async def _send_markdown_webhook(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """直接 POST session webhook markdown，不调用 DingTalkAdapter.send。"""
        metadata = metadata or {}
        session_webhook = metadata.get("session_webhook") or (
            self._get_valid_webhook(chat_id) or ("",)
        )[0]
        if not session_webhook:
            return SendResult(
                success=False,
                error="No valid session_webhook available. Reply must follow an incoming message.",
            )
        if not self._http_client:
            return SendResult(success=False, error="HTTP client not initialized")
        is_final_reply = reply_to is not None
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": "Hermes",
                "text": self._normalize_markdown(content[: self.MAX_MESSAGE_LENGTH]),
            },
        }
        try:
            import httpx
        except ImportError:
            httpx = None  # type: ignore[assignment]
        try:
            resp = await self._http_client.post(session_webhook, json=payload, timeout=15.0)
            if resp.status_code < 300:
                if is_final_reply:
                    self._fire_done_reaction(chat_id)
                return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            logger.warning(
                "[%s] Webhook send failed HTTP %d: %s",
                self.name,
                resp.status_code,
                resp.text[:200],
            )
            return SendResult(
                success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        except Exception as e:
            if httpx is not None and isinstance(e, httpx.TimeoutException):
                return SendResult(success=False, error="Timeout sending message to DingTalk")
            logger.error("[%s] Webhook send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def _stream_card_key(
        self,
        out_track_id: str,
        token: str,
        key: str,
        content: str,
        *,
        finalize: bool = False,
        is_error: bool = False,
    ) -> None:
        """沿用父类流式请求体，但 key 由调用方指定。"""
        from plugins.platforms.dingtalk import adapter as official

        if not official.CARD_SDK_AVAILABLE or self._card_sdk is None:
            raise RuntimeError("Card SDK unavailable")
        models = official.dingtalk_card_models
        stream_request = models.StreamingUpdateRequest(
            out_track_id=out_track_id,
            guid=str(uuid.uuid4()),
            key=key,
            content=(content or "")[: self.MAX_MESSAGE_LENGTH],
            is_full=True,
            is_finalize=finalize,
            is_error=is_error,
        )
        await self._sdk_call(
            self._card_sdk.streaming_update_with_options_async,
            stream_request,
            models.StreamingUpdateHeaders,
            token,
        )

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """按 route_send 复用、新建或 webhook。"""
        if not self._enhanced or self._bridge is None:
            return await super().send(chat_id, content, reply_to=reply_to, metadata=metadata)

        bridge = self._bridge
        turn_id = bridge.active_turn(chat_id)
        action = route_send(bridge.book, chat_id=chat_id, turn_id=turn_id)
        now = time.time()
        metadata = metadata or {}

        if action == "webhook":
            return await self._send_markdown_webhook(
                chat_id, content, reply_to=reply_to, metadata=metadata
            )

        if action == "reuse":
            frames = bridge.book.push_content(
                turn_id, content, segment_finalize=False, now=now
            )
            bridge.emit_frames(frames, now=now)
            card = bridge.book.get(turn_id)
            return SendResult(success=True, message_id=card.out_track_id)

        # create：不调用 _close_streaming_siblings，用 TurnBook 全量 params 建卡
        frames = bridge.book.push_content(turn_id, content, segment_finalize=False, now=now)
        card = bridge.book.get(turn_id)
        current_message = self._message_contexts.get(chat_id)
        session_webhook = metadata.get("session_webhook") or (
            self._get_valid_webhook(chat_id) or ("",)
        )[0]
        if not session_webhook:
            return SendResult(
                success=False,
                error="No valid session_webhook available. Reply must follow an incoming message.",
            )
        if not self._http_client or not current_message:
            return await self._send_markdown_webhook(
                chat_id, content, reply_to=reply_to, metadata=metadata
            )

        result, orphaned = await self._create_enhanced_card(
            chat_id, current_message, card, content, finalize=reply_to is not None
        )
        if result and result.success:
            bridge.book.mark_delivered(turn_id)
            bridge.emit_frames(frames, now=now)
            if reply_to is not None:
                self._fire_done_reaction(chat_id)
            else:
                self._streaming_cards.setdefault(chat_id, {})[result.message_id] = content
            return result
        if orphaned:
            bridge.book.abandon_undelivered(turn_id)
        logger.warning("[%s] Enhanced AI Card send failed, falling back to webhook", self.name)
        return await self._send_markdown_webhook(
            chat_id, content, reply_to=reply_to, metadata=metadata
        )

    async def _create_enhanced_card(
        self,
        chat_id: str,
        message: Any,
        card: Any,
        content: str,
        *,
        finalize: bool,
    ) -> Tuple[Optional[SendResult], bool]:
        """建卡；若 create 成功但 deliver/首帧失败，返回 orphaned=True。"""
        created = False
        out_track_id = card.out_track_id
        try:
            from plugins.platforms.dingtalk import adapter as official

            token = await self._get_access_token()
            if not token or not official.CARD_SDK_AVAILABLE:
                return None, False
            models = official.dingtalk_card_models
            is_group = str(getattr(message, "conversation_type", "1")) == "2"
            sender_staff_id = getattr(message, "sender_staff_id", "") or ""
            param_map = dict(card.params)
            create_request = models.CreateCardRequest(
                card_template_id=self._card_template_id,
                out_track_id=out_track_id,
                callback_type="STREAM",
                card_data=models.CreateCardRequestCardData(card_param_map=param_map),
                im_group_open_space_model=models.CreateCardRequestImGroupOpenSpaceModel(
                    support_forward=True
                ),
                im_robot_open_space_model=models.CreateCardRequestImRobotOpenSpaceModel(
                    support_forward=True
                ),
            )
            await self._sdk_call(
                self._card_sdk.create_card_with_options_async,
                create_request,
                models.CreateCardHeaders,
                token,
            )
            created = True
            if is_group:
                open_space_id = (
                    f"dtv1.card//IM_GROUP.{getattr(message, 'conversation_id', '') or ''}"
                )
                deliver_model = {
                    "im_group_open_deliver_model": models.DeliverCardRequestImGroupOpenDeliverModel(
                        robot_code=self._robot_code
                    )
                }
            elif sender_staff_id:
                open_space_id = f"dtv1.card//IM_ROBOT.{sender_staff_id}"
                deliver_model = {
                    "im_robot_open_deliver_model": models.DeliverCardRequestImRobotOpenDeliverModel(
                        space_type="IM_ROBOT"
                    )
                }
            else:
                logger.error(
                    "[%s] AI Card deliver skipped after create (missing sender_staff_id); "
                    "outTrackId=%s",
                    self.name,
                    out_track_id,
                )
                return None, True
            deliver_request = models.DeliverCardRequest(
                out_track_id=out_track_id,
                user_id_type=1,
                open_space_id=open_space_id,
                **deliver_model,
            )
            await self._sdk_call(
                self._card_sdk.deliver_card_with_options_async,
                deliver_request,
                models.DeliverCardHeaders,
                token,
            )
            shown = card.params.get("content") or content
            await self._stream_card_key(
                out_track_id, token, "content", shown, finalize=finalize, is_error=False
            )
            logger.info(
                "[%s] Enhanced AI Card %s: %s",
                self.name,
                "created+finalized" if finalize else "created (streaming)",
                out_track_id,
            )
            return SendResult(success=True, message_id=out_track_id), False
        except Exception as e:
            if created:
                logger.error(
                    "[%s] AI Card deliver/stream failed after create; outTrackId=%s: %s\n%s",
                    self.name,
                    out_track_id,
                    e,
                    traceback.format_exc(),
                )
                return None, True
            logger.warning(
                "[%s] Enhanced AI Card create failed: %s\n%s",
                self.name,
                e,
                traceback.format_exc(),
            )
            return None, False

    async def edit_message(
        self, chat_id: str, message_id: str, content: str, *, finalize: bool = False
    ) -> SendResult:
        """段边界 finalize 只落盘，不写完成态。"""
        if not self._enhanced or self._bridge is None:
            return await super().edit_message(chat_id, message_id, content, finalize=finalize)

        bridge = self._bridge
        turn_id = bridge.book.tracks.get(message_id) or bridge.active_turn(chat_id)
        action = route_edit(bridge.book, turn_id=turn_id, finalize=finalize)
        if action != "store":
            return SendResult(success=False, error="no running enhanced card")

        now = time.time()
        card = bridge.book.get(turn_id)
        # edit 带全文：先对齐再 push，避免重复追加
        if content.startswith(card.content):
            delta = content[len(card.content) :]
        else:
            card.content = ""
            delta = content
        frames = bridge.book.push_content(
            turn_id, delta, segment_finalize=finalize, now=now
        )
        bridge.emit_frames(frames, now=now)
        if finalize:
            self._streaming_cards.get(chat_id, {}).pop(message_id, None)
            if not self._streaming_cards.get(chat_id):
                self._streaming_cards.pop(chat_id, None)
        else:
            self._streaming_cards.setdefault(chat_id, {})[message_id] = content
        return SendResult(success=True, message_id=message_id)
