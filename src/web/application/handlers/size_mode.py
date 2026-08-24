"""自适应排他锁处理器：尺寸控制权接管与模式设定。

所有处理器按会话 uid 路由（入站消息优先 sessionUid 字段，
否则经 session_id(sid) resolve_sid 转换），锁状态按 uid 索引。
"""

from __future__ import annotations

from ....protocol.response import Response
from .base import HandlerContext, MessageHandler, _logger, _resolve_session_uid


class TakeoverSizeControlHandler(MessageHandler):
    """处理接管尺寸控制权请求。

    前端非自适应持有者点击"接管"按钮时发送 takeover_size_control。
    后端清空自适应锁（当前持有者降级），允许接管者随后设定新模式。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        if not session_uid:
            return [Response.error("session not found")]
        if ctx.adaptive_lock is None:
            return [Response.error("adaptive lock service unavailable")]

        # 清空自适应锁：旧持有者将被降级（前端收到 size_mode_changed 后切 fixed）
        old_owner = ctx.adaptive_lock.clear(session_uid)
        _logger.info(
            "takeover_size_control: uid=%s initiator_uid=%s old_owner=%s",
            session_uid,
            ctx.connection.client_uid,
            old_owner if old_owner is not None else "None",
        )

        # 广播模式变更：adaptive_owner_active=False（无人持有锁）
        # 不排除发起方：发起方前端需据此解锁 UI
        try:
            ctx.publisher.publish_size_mode_changed(
                session_uid=session_uid,
                session_id=msg.get("session_id", ""),
                adaptive_owner_active=False,
            )
        except Exception as e:
            _logger.exception(
                "takeover: publish_size_mode_changed failed uid=%s: %s", session_uid, e
            )

        return [{"type": "takeover_ack", "sessionId": msg.get("session_id", ""), "sessionUid": session_uid}]


class SetSizeModeHandler(MessageHandler):
    """处理设定尺寸模式请求。

    前端选择尺寸模式（adaptive/fixed/custom/default）时发送 set_size_mode。
    - adaptive：夺取自适应锁（当前持有者降级），持有者可自由自适应调整
    - fixed/custom/default：释放锁（若自己是持有者），按模式 resize 并广播

    锁持有者以 client_uid 标识（localStorage 持久化，刷新不变）。
    广播 size_mode_changed 时携带 adaptive_owner_uid，前端据此判断"自己是否持锁"。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        mode = msg.get("mode", "")
        if not session_uid or not mode:
            return [Response.error("missing session or mode")]
        if mode not in ("adaptive", "fixed", "custom", "default"):
            return [Response.error(f"invalid mode: {mode}")]
        if ctx.adaptive_lock is None:
            return [Response.error("adaptive lock service unavailable")]

        session = ctx.session_repo.get_by_uid(session_uid)
        if not session:
            return [Response.error(f"session not found")]

        # 用 client_uid 作为锁持有者标识（刷新不变，同 uid 多连接共享）
        initiator_uid = ctx.connection.client_uid
        initiator_conn_id = id(ctx.channel)  # 仍用于广播排除发起方连接

        if mode == "adaptive":
            # 夺取自适应锁：旧持有者降级，自己成为新持有者
            old_owner = ctx.adaptive_lock.acquire(session_uid, initiator_uid)
            _logger.info(
                "set_size_mode adaptive: uid=%s new_owner=%s old_owner=%s",
                session_uid,
                initiator_uid,
                old_owner if old_owner is not None else "None",
            )
            # 广播：adaptive_owner_active=True（有人持有锁）
            # 携带 adaptive_owner_uid，其他客户端据此判断"自己是否持锁"
            # 排除发起方（发起方前端已自行切换到 adaptive 模式）
            try:
                ctx.publisher.publish_size_mode_changed(
                    session_uid=session_uid,
                    session_id=session.id,
                    adaptive_owner_active=True,
                    mode="adaptive",
                    adaptive_owner_uid=initiator_uid,
                    exclude_conn_id=initiator_conn_id,
                )
            except Exception as e:
                _logger.exception(
                    "set_size_mode adaptive: publish failed uid=%s: %s", session_uid, e
                )
            return [
                {"type": "size_mode_ack", "sessionId": session.id, "sessionUid": session_uid, "mode": "adaptive"}
            ]

        # 非 adaptive 模式：释放锁（若自己是持有者）+ 按模式 resize
        # 排他校验：其他 client_uid 持有自适应锁时拒绝固定/自定义 resize
        # （与 ResizeHandler 一致，防止非持有者抢占尺寸控制权；
        # 前端 UI 已灰显，此处为后端防线）
        if ctx.adaptive_lock.has_owner(session_uid):
            owner = ctx.adaptive_lock.get_owner(session_uid)
            if owner != initiator_uid:
                _logger.warning(
                    "set_size_mode %s rejected: uid=%s initiator_uid=%s not owner (owner=%s)",
                    mode,
                    session_uid,
                    initiator_uid,
                    owner,
                )
                return [
                    Response.ws_error(
                        "adaptive size control held by another client",
                        code="adaptive_locked",
                    )
                ]
        ctx.adaptive_lock.release(session_uid, initiator_uid)

        cols = msg.get("cols")
        rows = msg.get("rows")
        actual_cols = session.cols
        actual_rows = session.rows

        # fixed/custom 模式需要指定尺寸；default 用守护进程当前尺寸
        if mode in ("fixed", "custom"):
            if not cols or not rows:
                return [Response.error(f"mode {mode} requires cols and rows")]
            cols = int(cols)
            rows = int(rows)
            # 执行 resize（复用 ResizeHandler 逻辑）
            # session.resize() 返回 (snapshot, scrollback)，scrollback 为
            # resize 保留的 reflow 历史，随 session_resized 广播给其他客户端
            try:
                snapshot, scrollback_ansi = await ctx.executor.run(
                    session.resize, cols, rows
                )
            except Exception as e:
                _logger.exception(
                    "set_size_mode %s resize failed uid=%s: %s", mode, session_uid, e
                )
                return [Response.error(f"resize failed: {e}")]
            actual_cols = cols
            actual_rows = rows
            try:
                ctx.publisher.publish_session_resized(
                    session_uid=session_uid,
                    session_id=session.id,
                    cols=cols,
                    rows=rows,
                    snapshot=snapshot or "",
                    scrollback=scrollback_ansi or "",
                    exclude_conn_id=initiator_conn_id,
                )
            except Exception as e:
                _logger.exception(
                    "set_size_mode: publish_session_resized failed uid=%s: %s",
                    session_uid,
                    e,
                )
        elif mode == "default":
            # default 模式：不主动 resize，由前端根据缓存的 daemonCols/daemonRows 自行处理
            # （守护进程当前尺寸可能已是 default，无需变更）
            actual_cols = session.cols
            actual_rows = session.rows

        # 广播模式变更：adaptive_owner_active=False（非 adaptive 模式不持有锁）
        try:
            ctx.publisher.publish_size_mode_changed(
                session_uid=session_uid,
                session_id=session.id,
                adaptive_owner_active=ctx.adaptive_lock.has_owner(session_uid),
                mode=mode,
                cols=actual_cols,
                rows=actual_rows,
                exclude_conn_id=initiator_conn_id,
            )
        except Exception as e:
            _logger.exception(
                "set_size_mode %s: publish failed uid=%s: %s", mode, session_uid, e
            )

        _logger.info(
            "set_size_mode %s: uid=%s sid=%s cols=%dx%d initiator_uid=%s",
            mode,
            session_uid,
            session.id,
            actual_cols,
            actual_rows,
            initiator_uid,
        )
        return [
            {
                "type": "size_mode_ack",
                "sessionId": session.id,
                "sessionUid": session_uid,
                "mode": mode,
                "cols": actual_cols,
                "rows": actual_rows,
            }
        ]
