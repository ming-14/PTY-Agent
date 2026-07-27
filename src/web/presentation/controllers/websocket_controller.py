"""WebSocket 连接控制器。"""

import asyncio
import json
import logging
from typing import Any, Optional

from ...application.dispatcher import MessageDispatcher
from ...application.handlers import HandlerContext
from ....vnc.ports import VncServicePort
from ....fastscreen.ports import FastScreenServicePort
from ...application.ports import (
    ConnectionContext,
    CursorLocatorServicePort,
    EventPublisher,
    HistoryRepository,
    OutboundMessageChannel,
    SessionRepository,
    ShellProvider,
    SystemStatsProvider,
    ThreadExecutor,
)
from ...application.adaptive_lock import AdaptiveLockService
from ...application.services import MessageEncoderService, SubscriptionService
from ...infrastructure.web.fastapi_transport import WSMsgType
from ....protocol.response import Response

_logger = logging.getLogger("pty-web")


class WebSocketController:
    """管理单个 WebSocket 连接的完整生命周期。

    负责：
    - 消息队列与 producer/consumer 任务；
    - 将原始 WebSocket 消息解析为 dict；
    - 构造 HandlerContext 并委托给 MessageDispatcher；
    - 清理订阅与回调。
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        history_repo: Optional[HistoryRepository],
        system_stats: SystemStatsProvider,
        shell_provider: ShellProvider,
        executor: ThreadExecutor,
        publisher: EventPublisher,
        adaptive_lock: Optional[AdaptiveLockService] = None,
        vnc_service: Optional[VncServicePort] = None,
        fastscreen_service: Optional[FastScreenServicePort] = None,
        cursor_locator_service: Optional[CursorLocatorServicePort] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        connections: Optional[dict] = None,
    ):
        self._session_repo = session_repo
        self._history_repo = history_repo
        self._system_stats = system_stats
        self._shell_provider = shell_provider
        self._executor = executor
        self._publisher = publisher
        self._adaptive_lock = adaptive_lock
        self._vnc_service = vnc_service
        self._fastscreen_service = fastscreen_service
        self._cursor_locator_service = cursor_locator_service
        self._dispatcher = dispatcher or MessageDispatcher()
        # v3: 引用 server.py 的 connections 字典，_cleanup 据此检查同 client_uid
        # 是否还有其他活跃连接订阅了该 sid（多标签页/刷新场景锁继承）
        self._connections = connections

    async def handle(
        self,
        transport: OutboundMessageChannel,
        context: ConnectionContext,
        queue: asyncio.Queue,
    ) -> None:
        """处理一个 WebSocket 连接直到断开。"""
        sid = id(transport)
        _logger.info("ws handle started conn_id=0x%x", sid)

        def _enqueue(message: dict) -> None:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                _logger.warning("ws queue full, dropping message type=%s", message.get("type"))

        encoder = MessageEncoderService(context)
        subscription = SubscriptionService(context, encoder, self._executor)
        handler_ctx = HandlerContext(
            session_repo=self._session_repo,
            history_repo=self._history_repo,
            system_stats=self._system_stats,
            shell_provider=self._shell_provider,
            executor=self._executor,
            encoder=encoder,
            subscription=subscription,
            publisher=self._publisher,
            connection=context,
            channel=transport,
            enqueue=_enqueue,
            adaptive_lock=self._adaptive_lock,
            vnc_service=self._vnc_service,
            fastscreen_service=self._fastscreen_service,
            cursor_locator_service=self._cursor_locator_service,
        )

        consumer = asyncio.ensure_future(self._consume(transport, handler_ctx, _enqueue))
        producer = asyncio.ensure_future(self._produce(transport, queue))
        try:
            done, pending = await asyncio.wait(
                [consumer, producer], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
        except asyncio.CancelledError:
            _logger.info("ws handle cancelled conn_id=0x%x", sid)
        except Exception:
            _logger.exception("ws handle error conn_id=0x%x", sid)
        finally:
            _logger.info("ws handle cleanup conn_id=0x%x subscribed=%s", sid, context.subscribed_session_id)
            await self._cleanup(context, handler_ctx)
            try:
                if not transport.closed:
                    await transport.close(code=1000)
            except Exception:
                pass
            _logger.info("ws handle finished conn_id=0x%x", sid)

    async def _consume(
        self,
        transport: OutboundMessageChannel,
        ctx: HandlerContext,
        enqueue: Any,
    ) -> None:
        sid = id(transport)
        msg_count = 0
        try:
            async for msg in transport:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        _logger.warning("ws recv invalid json from conn_id=0x%x: %r", sid, msg.data[:200])
                        enqueue(Response.error("invalid json"))
                        continue
                    msg_count += 1
                    t = data.get("type", "")
                    recv_sid = data.get("session_id", "")
                    if t == "input":
                        preview = repr(data.get("data", ""))[:80]
                        _logger.debug("ws recv #%d type=input sid=%r data=%s len=%d",
                                      msg_count, recv_sid, preview, len(data.get("data", "")))
                    elif t == "resize":
                        _logger.debug("ws recv #%d type=resize sid=%r cols=%s rows=%s",
                                      msg_count, recv_sid, data.get("cols"), data.get("rows"))
                    else:
                        _logger.debug("ws recv #%d type=%s sid=%r", msg_count, t, recv_sid)

                    responses = await self._dispatcher.dispatch(ctx, data)
                    for resp in responses:
                        enqueue(resp)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    _logger.info("ws consumer close/error msg type=%s conn_id=0x%x after %d msgs",
                                 msg.type, sid, msg_count)
                    break
        except asyncio.CancelledError:
            _logger.info("ws consumer cancelled conn_id=0x%x after %d msgs", sid, msg_count)
        except Exception:
            _logger.exception("ws consumer error conn_id=0x%x after %d msgs", sid, msg_count)
        finally:
            _logger.info("ws consumer finished conn_id=0x%x total_msgs=%d", sid, msg_count)

    async def _produce(
        self,
        transport: OutboundMessageChannel,
        queue: asyncio.Queue,
    ) -> None:
        sid = id(transport)
        sent_count = 0
        try:
            while True:
                msg = await queue.get()
                try:
                    await transport.send(msg)
                    sent_count += 1
                    msg_type = msg.get("type", "")
                    if msg_type == "output":
                        _logger.debug("ws send #%d type=output sid=%r data_len=%d",
                                      sent_count, msg.get("sessionId"), len(msg.get("data", "")))
                    elif sent_count % 50 == 0:
                        _logger.debug("ws producer sent %d messages", sent_count)
                except Exception as e:
                    _logger.info("ws producer send error: %s (sent=%d)", e, sent_count)
                    break
        except asyncio.CancelledError:
            _logger.info("ws producer cancelled conn_id=0x%x (sent=%d)", sid, sent_count)
        finally:
            _logger.info("ws producer finished conn_id=0x%x total_sent=%d", sid, sent_count)

    async def _cleanup(self, context: ConnectionContext, ctx: HandlerContext) -> None:
        """清理当前连接的订阅与回调。

        问题2补充：连接断开时若该连接是某会话的自适应锁持有者，
        必须释放锁并广播 size_mode_changed（adaptive_owner_active=False），
        通知其他订阅客户端解锁尺寸调整 UI。

        v3 改造：锁持有者从 conn_id 改为 client_uid。同一 client_uid 可能有多个
        连接（多标签页），仅当该 client_uid 没有其他活跃连接订阅该 sid 时才释放锁，
        否则锁由其他连接继承（不广播）。这保证：
        - A 端持锁 + A 端刷新：旧连接 _cleanup 时新连接可能已订阅（同 uid），锁保留
        - A 端持锁 + A 端关闭所有标签页：无其他连接，释放锁并广播
        - B 端刷新（不持锁）：_cleanup 时 is_owner=False，不释放 A 的锁
        """
        from ...application.handlers import UnsubscribeSessionHandler

        # 先快照订阅列表（unsubscribe 后会清空）
        client_uid = context.client_uid
        subscribed_sids = list(context.subscribed_session_ids)

        unsub = UnsubscribeSessionHandler()
        await unsub.handle(ctx, {})

        # 释放自适应锁：仅清理当前 client_uid 持有的锁
        if ctx.adaptive_lock is not None:
            for sid in subscribed_sids:
                if not ctx.adaptive_lock.is_owner(sid, client_uid):
                    continue
                # v3: 检查该 client_uid 是否还有其他活跃连接订阅了该 sid
                # 若有 → 锁由其他连接继承，不释放（同用户多标签页场景）
                # 若无 → 释放锁并广播（所有连接已关闭）
                if self._has_other_active_subscriber(sid, client_uid):
                    _logger.info(
                        "cleanup: keep adaptive lock sid=%s uid=%s (other conn active)",
                        sid, client_uid,
                    )
                    continue
                if ctx.adaptive_lock.release(sid, client_uid):
                    _logger.info(
                        "cleanup: release adaptive lock sid=%s uid=%s (no other conn)",
                        sid, client_uid,
                    )
                    try:
                        ctx.publisher.publish_size_mode_changed(
                            session_id=sid,
                            adaptive_owner_active=False,
                        )
                    except Exception as e:
                        _logger.exception(
                            "cleanup: publish_size_mode_changed failed sid=%s: %s", sid, e,
                        )

    def _has_other_active_subscriber(self, session_id: str, client_uid: Optional[str]) -> bool:
        """检查指定 client_uid 是否还有其他活跃连接订阅了该 session。

        v3 新增：_cleanup 时判断锁是否应由同 uid 的其他连接继承。
        遍历 connections 字典，找 uid 相同且订阅了该 sid 的其他连接。
        注意：调用时本连接的订阅已被 unsub.handle 清空，不会误匹配自己。
        """
        if not client_uid or not self._connections:
            return False
        for conn in self._connections.values():
            context = conn.get("context")
            if not context:
                continue
            try:
                if context.client_uid != client_uid:
                    continue
                if session_id in context.subscribed_session_ids:
                    return True
            except Exception as e:
                _logger.debug("has_other_active_subscriber: check error: %s", e)
        return False
