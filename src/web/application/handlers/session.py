"""会话运行时处理器：创建 / 订阅 / 输入 / 信号 / resize / 终止。

所有处理器按会话 uid 路由（入站消息优先 sessionUid 字段，
否则经 session_id(sid) resolve_sid 转换）；响应同时携带
sessionId（sid，展示用）与 sessionUid（路由用）。
"""

from __future__ import annotations

import shlex

from ....config.common import (
    DEFAULT_COLS,
    DEFAULT_ROWS,
    IS_WINDOWS,
    MAX_COMMAND_LEN,
    MAX_INPUT_LEN,
    MAX_SESSION_ID_LEN,
)
from ....protocol.response import Response
from .base import (
    HandlerContext,
    MessageHandler,
    _logger,
    _resolve_session_uid,
    _split_windows_command,
)


class CreateSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        command = msg.get("command")
        if not session_id or not command:
            _logger.warning("create: missing session_id or command")
            return [Response.error("missing session_id or command")]
        if len(session_id) > MAX_SESSION_ID_LEN:
            return [Response.error(f"session_id too long (max {MAX_SESSION_ID_LEN})")]
        if isinstance(command, str) and len(command) > MAX_COMMAND_LEN:
            return [Response.error(f"command too long (max {MAX_COMMAND_LEN})")]

        existing = ctx.session_repo.get_session(session_id)
        if existing:
            return [Response.error(f"session '{session_id}' already exists")]

        cwd = msg.get("cwd")
        env = msg.get("env")
        mode = msg.get("mode", "pty")
        shell = msg.get("shell")

        parsed_command = command
        if isinstance(command, str):
            try:
                if IS_WINDOWS:
                    parsed_command = _split_windows_command(command)
                else:
                    parsed_command = shlex.split(command)
            except ValueError as e:
                return [Response.error(f"failed to parse command: {e}")]
            if not parsed_command:
                return [Response.error("empty command after parsing")]

        # shell 包装：选择 shell 时用该 shell 启动（与 daemon exec --shell 一致，
        # wrap_command 内部 which 解析路径，不支持/找不到时抛 ValueError）
        if shell and isinstance(parsed_command, list) and parsed_command:
            try:
                from ....common.shells import wrap_command

                parsed_command = wrap_command(parsed_command, shell)
            except ValueError as e:
                return [Response.error(str(e))]

        try:
            session = await ctx.executor.run(
                ctx.session_repo.create_session,
                session_id,
                parsed_command,
                cwd=cwd,
                env=env,
                cols=msg.get("cols"),
                rows=msg.get("rows"),
                mode=mode,
            )
        except Exception as e:
            _logger.exception("create failed: sid=%s", session_id)
            try:
                ctx.session_repo.remove_session(session_id)
            except Exception:
                pass
            return [Response.error(f"create failed: {e}")]

        # 来源标记：web 创建视为普通 exec
        session.add_common_mark("normal")

        # 创建成功后自动订阅（传 uid，避免 sid 解析竞态）
        return await SubscribeSessionHandler().handle(
            ctx, {"sessionUid": session.uid, "session_id": session_id}
        )


class SubscribeSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        if not session_uid:
            return [Response.error("session not found")]
        session = ctx.session_repo.get_by_uid(session_uid) or ctx.session_repo.get_session(session_uid)
        if not session:
            return [Response.error(f"session not found")]
        session_id = session.id

        # 查询当前自适应锁状态，随 ws_subscribed 响应返回给前端。
        # 前端刷新后据此得知锁持有者，正确恢复 UI（持锁者高亮 / 非持锁者显示接管按钮）。
        adaptive_owner_active = False
        adaptive_owner_uid = None
        if ctx.adaptive_lock is not None:
            adaptive_owner_uid = ctx.adaptive_lock.get_owner(session_uid)
            adaptive_owner_active = adaptive_owner_uid is not None

        # 多订阅模式：不再 unsubscribe 之前的订阅，支持多会话同时订阅
        # 切换标签时前端不再重新 subscribe（已订阅的会话直接切显示）
        # 只有首次打开该会话时才会 subscribe
        is_subprocess = getattr(session, "mode", "pty") == "subprocess"
        if session_uid in ctx.connection.subscribed_session_ids:
            # 已订阅：返回已订阅状态，不重新注册回调
            _logger.info(
                "subscribed: uid=%s already subscribed (multi-sub)", session_uid
            )
            return [
                Response.ws_subscribed(
                    session_id,
                    sessionUid=session_uid,
                    replay="",  # 已订阅不返回 replay，避免前端重放
                    snapshot=None,
                    scrollback="",  # 已订阅不返回 scrollback，前端 xterm.js 实例已保留
                    ptyType=session.pty_type,
                    mode=session.mode if hasattr(session, "mode") else "pty",
                    cols=session.cols,
                    rows=session.rows,
                    outputOffset=session.output_offset,
                    running=session.running,
                    exitCode=session.exit_code,
                    errorMessage=session.error_message,
                    encoding=session.encoding or "utf-8",
                    startTime=session.start_time,
                    appMouseMode=session.is_mouse_tracking() if not is_subprocess else False,
                    adaptiveOwnerActive=adaptive_owner_active,
                    adaptiveOwnerUid=adaptive_owner_uid,
                )
            ]

        # 添加到订阅集合（按 uid；首次订阅才走到此处；已订阅分支提前返回）
        ctx.connection.add_subscription(session_uid)

        # 订阅期间持有会话：会话可能在输出回调/replay 生成窗口内自然结束，
        # manager 会触发 release_components 释放大缓冲；持有确保结束只延迟
        # 释放，全部退订后才实际释放，避免回调/读取路径访问已置空的缓冲。
        # 与门户端创建期预持有衔接：自 create_session 预持有起计数保持非零。
        session.acquire_hold()
        ctx.connection.add_held_session(session_uid, session)

        # 第一次订阅用终端模型 snapshot 作为 replay，而非原始输出缓冲区：
        # 原始输出缓冲区包含 ConPTY 增量光标序列（CSI row;col H），
        # 在 term.clear() 后重放会导致错位。终端模型 snapshot 是当前屏幕的"真相"，
        # 与 ConPTY repaint 同源，显示正确。
        # 后续实时输出通过 publisher 持续推送。
        # 同时返回 scrollback（wezterm 终端模型历史区），前端写入 xterm.js
        # 推入 scrollback 区，实现 F5 刷新/重开浏览器后历史不丢。
        sub_data = await ctx.subscription.prepare_subscription(session_uid, session)
        replay_text = sub_data["replay"]
        scrollback_ansi = sub_data["scrollback"]

        # 子进程模式：无终端光标/模式语义，跳过 cursor_seq 与 mode_restore_seq
        if not is_subprocess:
            # 在 replay 末尾追加光标定位 VT 序列
            # 前端 replayPending 会 term.clear()+write(replay)，replay 为终端模型 snapshot，
            # 可能不以光标定位序列结尾（如最后输出是 prompt 文本而非 CUP 序列），
            # 导致前端写入后光标停在 replay 末尾而非 PTY 真实位置。
            # 追加终端模型当前光标位置序列，强制光标定位到正确位置。
            try:
                cursor_seq = session.get_cursor_seq()
                if cursor_seq:
                    replay_text = replay_text + cursor_seq
                    _logger.debug(
                        "subscribed: appended cursor seq sid=%s seq=%r",
                        session_id,
                        cursor_seq,
                    )
            except Exception as e:
                _logger.warning(
                    "subscribed: append cursor seq failed sid=%s: %s", session_id, e
                )

            # 在 replay 前拼接终端模式恢复序列（鼠标追踪/光标可见/备用屏幕等）
            # 网页刷新后 xterm 实例重建，replay 只含屏幕内容不含模式状态，
            # 会导致鼠标模式丢失、备用屏幕状态错乱。前端从 replay 写入路径
            # 自动检测 DECSET 序列并恢复鼠标模式（mouseMode.js detectAppMouseModeFromOutput）。
            try:
                mode_prefix = session.mode_restore_seq()
                if mode_prefix:
                    replay_text = mode_prefix + replay_text
                    _logger.debug(
                        "subscribed: prepend mode restore seq sid=%s seq=%r",
                        session_id,
                        mode_prefix,
                    )
            except Exception as e:
                _logger.warning(
                    "subscribed: mode restore seq failed sid=%s: %s", session_id, e
                )

        # 子进程模式：附带 stderr 全文（前端以红色逐行展示）
        stderr_replay = ""
        if is_subprocess:
            try:
                stderr_replay = session.get_err_output(encoding=session.encoding or "utf-8")
            except Exception as e:
                _logger.warning(
                    "subscribed: get stderr replay failed sid=%s: %s", session_id, e
                )

        # 注册输出回调（每个会话独立回调，按 uid 隔离）
        def _on_data(data: bytes, stream: str):
            try:
                text = ctx.encoder.decode_output(
                    session_uid, session.encoding or "utf-8", data
                )
                ctx.enqueue(
                    Response.ws_output(
                        session_id, session_uid, text, stream, session.encoding or "utf-8"
                    )
                )
            except Exception:
                _logger.exception("output callback error sid=%s", session_id)

        # 注册结束回调
        def _on_end(ended_session):
            try:
                # 会话结束时无论是否活动都要通知前端
                ctx.enqueue(
                    Response.ws_session_ended(
                        session_id, session_uid, ended_session.exit_code, ended_session.error_message
                    )
                )
            except Exception:
                _logger.exception("end callback error sid=%s", session_id)

        # 注册事件回调
        def _on_event(ev: dict):
            try:
                # 会话结束时无论是否活动都要通知前端
                ctx.enqueue(Response.ws_session_event(session_id, session_uid, ev))
            except Exception:
                _logger.exception("event callback error sid=%s", session_id)

        # 注册尺寸变更回调（程序/客户端发起 resize 后广播，web 端立即响应）
        def _on_resized(_session, cols, rows, snapshot="", scrollback=""):
            try:
                text = snapshot or _session.get_snapshot(keep_ansi=True)
                ctx.publisher.publish_session_resized(
                    session_uid=session_uid,
                    session_id=session_id,
                    cols=cols,
                    rows=rows,
                    snapshot=text,
                    scrollback=scrollback,
                    exclude_conn_id=None,
                )
            except Exception:
                _logger.exception("resized callback error sid=%s", session_id)

        # 注册 OSC 52 剪贴板写回调（应用 → 前端写系统剪贴板）
        def _on_clipboard(selection, data):
            try:
                ctx.enqueue(Response.ws_clipboard(session_id, session_uid, selection, data))
            except Exception:
                _logger.exception("clipboard callback error sid=%s", session_id)

        # 按 uid 存储回调
        ctx.connection.set_callbacks(
            session_uid,
            {
                "output": _on_data,
                "end": _on_end,
                "event": _on_event,
                "resized": _on_resized,
                "clipboard": _on_clipboard,
            },
        )

        session.publisher.subscribe(_on_data)
        session.publisher.add_on_end_callback(_on_end)
        session.publisher.add_on_resized_callback(_on_resized)
        session.event_history.add_event_listener(_on_event)
        # OSC 52 剪贴板回调（reader 线程入队，非阻塞）
        try:
            session.set_clipboard_callback(_on_clipboard)
        except Exception:
            _logger.exception("set clipboard callback error sid=%s", session_id)

        pty_type = session.pty_type
        output_offset = session.output_offset

        _logger.info(
            "subscribed: sid=%s uid=%s pty=%s size=%dx%d running=%s (multi-sub, total=%d)",
            session_id,
            session_uid,
            pty_type,
            session.cols,
            session.rows,
            session.running,
            len(ctx.connection.subscribed_session_ids),
        )

        messages = [
            Response.ws_subscribed(
                session_id,
                sessionUid=session_uid,
                replay=replay_text,
                snapshot=None,
                scrollback=scrollback_ansi,
                ptyType=pty_type,
                mode=session.mode if hasattr(session, "mode") else "pty",
                stderrReplay=stderr_replay,
                cols=session.cols,
                rows=session.rows,
                outputOffset=output_offset,
                running=session.running,
                exitCode=session.exit_code,
                errorMessage=session.error_message,
                encoding=session.encoding or "utf-8",
                startTime=session.start_time,
                appMouseMode=session.is_mouse_tracking() if not is_subprocess else False,
                # 携带自适应锁状态，前端刷新后据此恢复 UI
                adaptiveOwnerActive=adaptive_owner_active,
                adaptiveOwnerUid=adaptive_owner_uid,
            )
        ]
        if not session.running:
            messages.append(
                Response.ws_session_ended(
                    session_id, session_uid, session.exit_code, session.error_message
                )
            )
        return messages


class UnsubscribeSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        # 支持指定 uid 的 unsubscribe，不再清空所有订阅
        # msg 中可指定 sessionUid 或 session_id(sid)，未指定时清空所有（连接关闭场景）
        target_uid = msg.get("sessionUid") or ""
        if not target_uid:
            target_uid = _resolve_session_uid(ctx, msg)

        if target_uid:
            # 只移除指定会话的订阅
            uids_to_remove = [target_uid]
        else:
            # 兼容旧行为：未指定时清空所有
            uids_to_remove = list(ctx.connection.subscribed_session_ids)

        for uid in uids_to_remove:
            callbacks = ctx.connection.get_callbacks(uid)
            cb = callbacks.get("output")
            end_cb = callbacks.get("end")
            event_cb = callbacks.get("event")
            resized_cb = callbacks.get("resized")

            # 释放订阅期间持有的会话：使用连接上下文保存的引用
            # （会话结束被移出仓库后 get_session 已取不到，须凭持有凭证释放）
            held_session = ctx.connection.pop_held_session(uid)

            if uid:
                if held_session is not None:
                    held_session.release_hold()
                session = ctx.session_repo.get_by_uid(uid)
                if session:
                    if cb:
                        session.publisher.unsubscribe(cb)
                    if end_cb:
                        session.publisher.remove_on_end_callback(end_cb)
                    if resized_cb:
                        session.publisher.remove_on_resized_callback(resized_cb)
                    if event_cb:
                        try:
                            session.event_history.remove_event_listener(event_cb)
                        except Exception:
                            pass
                ctx.connection.remove_decoder(uid)

            ctx.connection.remove_subscription(uid)
            ctx.connection.clear_callbacks(uid)

        return [Response.ws_unsubscribed()]


class InputHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        data = msg.get("data", "")
        if not isinstance(data, str):
            return [Response.error("input data must be a string")]
        if len(data) > MAX_INPUT_LEN:
            return [Response.error(f"input too long (max {MAX_INPUT_LEN})")]

        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session or not session.running:
            return []

        def _write():
            # 编码已锁定（手动指定或探测成功）时无需每键重探测
            if not session._encoding_locked:
                tail = session.output_buffer.get_slice(
                    max(0, session.output_buffer.length - 4096)
                )
                session.detect_encoding(tail)
            session.write_input(data)

        try:
            await ctx.executor.run(_write)
        except Exception as e:
            _logger.warning("input write failed: sid=%s err=%s", session.id, e)
            return [Response.error(f"write failed: {e}")]
        return []


class KeyInputHandler(MessageHandler):
    """模式感知键盘事件 → wezterm 服务端编码 → 写 pty

    前端发送原始按键（key + mods），daemon 用 wezterm-py Terminal
    按终端当前状态编码为对应 VT 序列并写入 pty。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        key = msg.get("key", "")
        if not isinstance(key, str) or not key:
            return [Response.error("key must be a non-empty string")]
        try:
            mods = int(msg.get("mods", 0))
        except (TypeError, ValueError):
            return [Response.error("mods must be an integer")]

        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session or not session.running:
            return []
        if getattr(session, "mode", "pty") == "subprocess":
            return [Response.ws_error("subprocess mode has no terminal", code="subprocess_no_terminal_key")]
        if len(key) > 16:
            return [Response.error("key too long")]

        def _write():
            session.key_input(key, mods)

        try:
            await ctx.executor.run(_write)
        except Exception as e:
            _logger.warning("key input write failed: sid=%s err=%s", session.id, e)
            return [Response.error(f"write failed: {e}")]
        return []


class MouseInputHandler(MessageHandler):
    """模式感知鼠标事件 → wezterm 服务端编码 → 写 pty

    前端发送原始鼠标事件（x/y 0-based 单元格 + kind/button/mods）。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        try:
            x = int(msg.get("x"))
            y = int(msg.get("y"))
        except (TypeError, ValueError):
            return [Response.error("x/y must be integers")]
        kind = msg.get("kind", "press")
        button = msg.get("button", "left")
        try:
            mods = int(msg.get("mods", 0))
        except (TypeError, ValueError):
            return [Response.error("mods must be an integer")]

        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session or not session.running:
            return []
        if getattr(session, "mode", "pty") == "subprocess":
            return [Response.ws_error("subprocess mode has no terminal", code="subprocess_no_terminal_mouse")]
        if not isinstance(kind, str) or not isinstance(button, str):
            return [Response.error("kind/button must be strings")]

        def _write():
            session.mouse_input(x, y, kind, button, mods)

        try:
            await ctx.executor.run(_write)
        except Exception as e:
            _logger.warning("mouse input write failed: sid=%s err=%s", session.id, e)
            return [Response.error(f"write failed: {e}")]
        return []


class SignalHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        sig = msg.get("signal", "")
        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session:
            _logger.warning("SignalHandler: session not found uid=%s", session_uid)
            return []
        if not session.running:
            _logger.warning("SignalHandler: session not running uid=%s", session_uid)
            return []
        if sig == "SIGINT":
            try:
                await ctx.executor.run(session.send_signal, sig)
                _logger.info("SignalHandler: SIGINT sent sid=%s", session.id)
            except Exception as e:
                _logger.warning(
                    "signal send failed: sid=%s sig=%s err=%s", session.id, sig, e
                )
                return [Response.error(f"signal failed: {e}")]
        else:
            return [Response.error(f"unsupported signal: {sig}")]
        return []


class ResizeHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        cols_raw = msg.get("cols")
        rows_raw = msg.get("rows")
        try:
            cols = int(cols_raw) if cols_raw is not None else DEFAULT_COLS
            rows = int(rows_raw) if rows_raw is not None else DEFAULT_ROWS
        except (TypeError, ValueError):
            return [Response.error("invalid cols or rows")]
        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session:
            _logger.warning("resize: session not found uid=%s", session_uid)
            return []

        # 子进程模式无终端，禁止 resize
        if getattr(session, "mode", "pty") == "subprocess":
            return [Response.ws_error("subprocess mode does not support resize", code="subprocess_no_resize")]

        # 自适应排他锁：存在自适应持有者且当前连接不是持有者时拒绝 resize，
        # 避免非持有者的自适应调整抢占持有者的尺寸控制权；
        # 持有者以 client_uid 标识（localStorage 持久化），同一 client_uid 的
        # 多个连接（多标签页）都允许 resize。锁按会话 uid 索引。
        if ctx.adaptive_lock is not None:
            initiator_uid = ctx.connection.client_uid
            owner = ctx.adaptive_lock.get_owner(session_uid)
            if owner is not None and owner != initiator_uid:
                _logger.warning(
                    "resize rejected: uid=%s initiator_uid=%s not adaptive owner (owner=%s)",
                    session_uid,
                    initiator_uid,
                    owner,
                )
                return [Response.ws_error("adaptive size control held by another client", code="adaptive_locked")]

        try:
            # session.resize() 按 ConPTY 语义重排并返回 (snapshot, scrollback)：
            #   - snapshot：可见区内容 + 光标，与 ConPTY 坐标系一致（杜绝光标错位）
            #   - scrollback：resize 时保留的 reflow 历史（ANSI + \r\n），
            #     前端 restoreScrollbackAndSnapshot 据此重建 scrollback 区，
            #     不再因 resize 丢失用户滚动历史。
            snapshot, scrollback_ansi = await ctx.executor.run(
                session.resize, cols, rows
            )
        except Exception as e:
            _logger.warning("resize failed: sid=%s err=%s", session.id, e)
            return []
        snapshot_len = len(snapshot) if snapshot else 0
        scrollback_len = len(scrollback_ansi) if scrollback_ansi else 0
        _logger.info(
            "resize: sid=%s cols=%dx%d snapshot_len=%d scrollback_len=%d",
            session.id,
            cols,
            rows,
            snapshot_len,
            scrollback_len,
        )

        # 尺寸变更通知：立刻定向广播给其他订阅客户端；
        # 发起方（当前连接）通过 resize_complete 完成本地调整，需排除避免重复处理。
        # conn_id = id(transport)，与 server.py 中 self._connections[conn_id] 的 key 一致。
        try:
            initiator_conn_id = id(ctx.channel)
            ctx.publisher.publish_session_resized(
                session_uid=session_uid,
                session_id=session.id,
                cols=cols,
                rows=rows,
                snapshot=snapshot or "",
                scrollback=scrollback_ansi or "",
                exclude_conn_id=initiator_conn_id,
            )
            _logger.info(
                "resize: broadcast session_resized sid=%s %dx%d excluded initiator=0x%x",
                session.id,
                cols,
                rows,
                initiator_conn_id,
            )
        except Exception as e:
            _logger.exception(
                "resize: publish_session_resized failed sid=%s: %s", session.id, e
            )

        # snapshot 含 PTY 真实光标定位（\x1b[row;colH），前端 \x1b[3J + scrollback
        # + \x1b[2J + snapshot 重建 buffer 后与 ConPTY 坐标系完全一致
        return [
            {
                "type": "resize_complete",
                "sessionId": session.id,
                "sessionUid": session_uid,
                "cols": cols,
                "rows": rows,
                "snapshot": snapshot or "",
                "scrollback": scrollback_ansi or "",
            }
        ]


class KillSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_uid = _resolve_session_uid(ctx, msg)
        session = ctx.session_repo.get_by_uid(session_uid) if session_uid else None
        if not session:
            return [Response.error(f"session not found")]

        # 先取消订阅指定会话，避免残留回调，不影响其他会话的订阅
        unsubscribe_handler = UnsubscribeSessionHandler()
        await unsubscribe_handler.handle(ctx, {"sessionUid": session_uid})

        try:
            await ctx.executor.run(ctx.session_repo.remove_session, session_uid)
        except Exception as e:
            _logger.exception("kill failed: sid=%s", session.id)
            return [Response.error(f"kill failed: {e}")]

        return [
            Response.ws_session_ended(
                session.id, session_uid, session.exit_code, session.error_message
            )
        ]
