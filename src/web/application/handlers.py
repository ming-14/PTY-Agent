"""WebSocket 消息用例处理器。

每个处理器对应一种前端消息类型，负责执行业务逻辑并返回响应消息。
所有处理器只依赖应用端口和领域实体，不依赖具体框架或基础设施。
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Optional

from ...config.common import (
    DEFAULT_COLS,
    DEFAULT_ROWS,
    IS_WINDOWS,
    MAX_COMMAND_LEN,
    MAX_INPUT_LEN,
    MAX_SESSION_ID_LEN,
)
from ...protocol.response import Response
from ..domain.entities import SessionDetail
from .adaptive_lock import AdaptiveLockService
from .ports import (
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
from .services import MessageEncoderService, SubscriptionService
from ...logging import get_logger

if TYPE_CHECKING:
    from ...screenshare.ports import ScreenshareServicePort
    from ...vnc.ports import VncServicePort

_logger = get_logger("pty-web")


def _split_windows_command(cmd: str) -> list:
    """按 Windows CommandLineToArgvW 规则拆分字符串，正确处理引号与反斜杠转义。

    与 shlex.split(posix=False) 不同，本函数会去掉参数两端的引号，
    避免 PTY 模式下把引号当作路径的一部分传给子进程。
    反斜杠仅在其紧邻引号时参与转义（偶数个每对产生一个字面反斜杠，
    奇数个时剩余一个转义该引号），否则按字面保留。
    """
    args = []
    current = []
    in_quotes = False
    arg_started = False  # 当前参数是否已显式开始（含空引号），用于保留空参数
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\":
            # 统计连续反斜杠
            backslashes = 0
            while i < n and cmd[i] == "\\":
                backslashes += 1
                i += 1
            if i < n and cmd[i] == '"':
                # 反斜杠紧邻引号：每对反斜杠产生一个字面反斜杠
                current.append("\\" * (backslashes // 2))
                if backslashes % 2 == 1:
                    # 奇数个：剩余一个转义该引号，引号作为字面量写入
                    current.append('"')
                    i += 1
                else:
                    # 偶数个：引号作为普通分隔符
                    in_quotes = not in_quotes
                    i += 1
            else:
                current.append("\\" * backslashes)
            arg_started = True
        elif c == '"':
            # 引号内连续双引号表示一个字面引号；引号外连续双引号则成对开闭（空串）
            if in_quotes and i + 1 < n and cmd[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            in_quotes = not in_quotes
            arg_started = True
            i += 1
        elif c in (" ", "\t") and not in_quotes:
            if arg_started:
                args.append("".join(current))
                current = []
                arg_started = False
            i += 1
        else:
            current.append(c)
            arg_started = True
            i += 1
    if arg_started:
        args.append("".join(current))
    return args


def _command_to_string(command) -> str:
    """将命令转换为可显示的字符串。"""
    return command if isinstance(command, str) else " ".join(command)


class HandlerContext:
    """处理器共享的上下文对象。"""

    def __init__(
        self,
        session_repo: SessionRepository,
        history_repo: Optional[HistoryRepository],
        system_stats: SystemStatsProvider,
        shell_provider: ShellProvider,
        executor: ThreadExecutor,
        encoder: MessageEncoderService,
        subscription: SubscriptionService,
        publisher: EventPublisher,
        connection: ConnectionContext,
        channel: OutboundMessageChannel,
        enqueue: Callable[[dict], None],
        adaptive_lock: Optional[AdaptiveLockService] = None,
        vnc_service: Optional[VncServicePort] = None,
        screenshare_service: Optional[ScreenshareServicePort] = None,
        cursor_locator_service: Optional[CursorLocatorServicePort] = None,
    ):
        self.session_repo = session_repo
        self.history_repo = history_repo
        self.system_stats = system_stats
        self.shell_provider = shell_provider
        self.executor = executor
        self.encoder = encoder
        self.subscription = subscription
        self.publisher = publisher
        self.connection = connection
        self.channel = channel
        self.enqueue = enqueue
        self.adaptive_lock = adaptive_lock
        self.vnc_service = vnc_service
        self.screenshare_service = screenshare_service
        self.cursor_locator_service = cursor_locator_service


class MessageHandler(ABC):
    """消息处理器抽象基类。"""

    @abstractmethod
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        """处理消息并返回响应消息列表。"""


# --------------------------------------------------------------------------- #
# 具体处理器
# --------------------------------------------------------------------------- #


class PingHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        return [Response.pong()]


class ListSessionsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        sessions = ctx.session_repo.list_sessions()
        return [
            Response.ws_session_list(
                [
                    {
                        "id": s.id,
                        "uid": s.uid,
                        "command": s.command,
                        "running": s.running,
                        "startTime": s.start_time,
                    }
                    for s in sessions
                ]
            )
        ]


class ListShellsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        try:
            shells = ctx.shell_provider.list_shells()
            available = {name: path for name, path in shells.items() if path}
            return [Response.ws_shell_list(available)]
        except Exception as e:
            _logger.warning("list_shells failed: %s", e)
            return [Response.ws_shell_list({})]


class SystemStatsHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        stats = await ctx.system_stats.get_stats()
        return [Response.ws_system_stats(stats.cpu, stats.memory)]


class ListHistoryHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.history_repo:
            return [Response.ws_history_list([])]
        sessions = ctx.history_repo.list_sessions()
        return [
            Response.ws_history_list(
                [
                    {
                        "id": s.id,
                        "command": s.command,
                        "ptyType": s.pty_type,
                        "encoding": s.encoding,
                        "startTime": s.start_time,
                        "endTime": s.end_time,
                        "exitCode": s.exit_code,
                        "errorMessage": s.error_message,
                        "running": False,
                    }
                    for s in sessions
                ]
            )
        ]


class HistoryDetailHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.history_repo:
            return [Response.error("no history store")]
        sid = msg.get("session_id", "")
        detail = ctx.history_repo.get_session_detail(sid)
        if not detail:
            return [Response.error(f"session '{sid}' not found in history")]
        payload = {
            "type": "history_detail",
            "id": detail.id,
            "uid": detail.uid,
            "command": detail.command,
            "ptyType": detail.pty_type,
            "cols": detail.cols,
            "rows": detail.rows,
            "encoding": detail.encoding,
            "startTime": detail.start_time,
            "endTime": detail.end_time,
            "exitCode": detail.exit_code,
            "errorMessage": detail.error_message,
            "running": False,
            "replay": detail.replay,
            "snapshot": detail.snapshot,
        }
        if detail.screen_buffer_z:
            payload["screenBufferZ"] = detail.screen_buffer_z
            payload["screenBufferMeta"] = detail.screen_buffer_meta
        if detail.output_gz:
            payload["outputGz"] = detail.output_gz
            payload["outputGzOriginalLen"] = detail.output_gz_original_len
        if detail.events:
            payload["events"] = detail.events
        return [payload]


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

        # 创建成功后自动订阅
        subscribe_handler = SubscribeSessionHandler()
        return await subscribe_handler.handle(ctx, {"session_id": session_id})


class SubscribeSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        session = ctx.session_repo.get_session(session_id)
        if not session:
            return [Response.error(f"session '{session_id}' not found")]

        # 查询当前自适应锁状态，随 ws_subscribed 响应返回给前端。
        # 前端刷新后据此得知锁持有者，正确恢复 UI（持锁者高亮 / 非持锁者显示接管按钮）。
        adaptive_owner_active = False
        adaptive_owner_uid = None
        if ctx.adaptive_lock is not None:
            adaptive_owner_uid = ctx.adaptive_lock.get_owner(session_id)
            adaptive_owner_active = adaptive_owner_uid is not None

        # 多订阅模式：不再 unsubscribe 之前的订阅，支持多会话同时订阅
        # 切换标签时前端不再重新 subscribe（已订阅的会话直接切显示）
        # 只有首次打开该会话时才会 subscribe
        is_subprocess = getattr(session, "mode", "pty") == "subprocess"
        if session_id in ctx.connection.subscribed_session_ids:
            # 已订阅：返回已订阅状态，不重新注册回调
            _logger.info(
                "subscribed: sid=%s already subscribed (multi-sub)", session_id
            )
            return [
                Response.ws_subscribed(
                    session_id,
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

        # 添加到订阅集合（首次订阅才走到此处；已订阅分支提前返回）
        ctx.connection.add_subscription(session_id)

        # 订阅期间持有会话：会话可能在输出回调/replay 生成窗口内自然结束，
        # manager 会触发 release_components 释放大缓冲；持有确保结束只延迟
        # 释放，全部退订后才实际释放，避免回调/读取路径访问已置空的缓冲。
        # 与门户端创建期预持有衔接：自 create_session 预持有起计数保持非零。
        session.acquire_hold()
        ctx.connection.add_held_session(session_id, session)

        # 第一次订阅用终端模型 snapshot 作为 replay，而非原始输出缓冲区：
        # 原始输出缓冲区包含 ConPTY 增量光标序列（CSI row;col H），
        # 在 term.clear() 后重放会导致错位。终端模型 snapshot 是当前屏幕的"真相"，
        # 与 ConPTY repaint 同源，显示正确。
        # 后续实时输出通过 publisher 持续推送。
        # 同时返回 scrollback（wezterm 终端模型历史区），前端写入 xterm.js
        # 推入 scrollback 区，实现 F5 刷新/重开浏览器后历史不丢。
        sub_data = await ctx.subscription.prepare_subscription(session_id, session)
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

        # 子进程模式：附带 stderr 全文（前端以 ERR > 前缀单独展示）
        stderr_replay = ""
        if is_subprocess:
            try:
                stderr_replay = session.get_err_output(encoding=session.encoding or "utf-8")
            except Exception as e:
                _logger.warning(
                    "subscribed: get stderr replay failed sid=%s: %s", session_id, e
                )

        # 注册输出回调（每个会话独立回调）
        def _on_data(data: bytes, stream: str):
            try:
                text = ctx.encoder.decode_output(
                    session_id, session.encoding or "utf-8", data
                )
                ctx.enqueue(
                    Response.ws_output(
                        session_id, text, stream, session.encoding or "utf-8"
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
                        session_id, ended_session.exit_code, ended_session.error_message
                    )
                )
            except Exception:
                _logger.exception("end callback error sid=%s", session_id)

        # 注册事件回调
        def _on_event(ev: dict):
            try:
                # 会话结束时无论是否活动都要通知前端
                ctx.enqueue(Response.ws_session_event(session_id, ev))
            except Exception:
                _logger.exception("event callback error sid=%s", session_id)

        # 注册尺寸变更回调（程序/客户端发起 resize 后广播，web 端立即响应）
        def _on_resized(_session, cols, rows, snapshot=""):
            try:
                text = snapshot or _session.get_snapshot(keep_ansi=True)
                ctx.publisher.publish_session_resized(
                    session_id=session_id,
                    cols=cols,
                    rows=rows,
                    snapshot=text,
                    scrollback="",
                    exclude_conn_id=None,
                )
            except Exception:
                _logger.exception("resized callback error sid=%s", session_id)

        # 注册 OSC 52 剪贴板写回调（应用 → 前端写系统剪贴板）
        def _on_clipboard(selection, data):
            try:
                ctx.enqueue(Response.ws_clipboard(session_id, selection, data))
            except Exception:
                _logger.exception("clipboard callback error sid=%s", session_id)

        # 按 session_id 存储回调
        ctx.connection.set_callbacks(
            session_id,
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
            "subscribed: sid=%s pty=%s size=%dx%d running=%s (multi-sub, total=%d)",
            session_id,
            pty_type,
            session.cols,
            session.rows,
            session.running,
            len(ctx.connection.subscribed_session_ids),
        )

        messages = [
            Response.ws_subscribed(
                session_id,
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
                    session_id, session.exit_code, session.error_message
                )
            )
        return messages


class UnsubscribeSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        # 支持指定 sid 的 unsubscribe，不再清空所有订阅
        # msg 中可指定 session_id，未指定时清空所有（连接关闭场景）
        target_sid = msg.get("session_id")

        if target_sid:
            # 只移除指定会话的订阅
            sids_to_remove = [target_sid]
        else:
            # 兼容旧行为：未指定时清空所有
            sids_to_remove = list(ctx.connection.subscribed_session_ids)

        for sid in sids_to_remove:
            callbacks = ctx.connection.get_callbacks(sid)
            cb = callbacks.get("output")
            end_cb = callbacks.get("end")
            event_cb = callbacks.get("event")
            resized_cb = callbacks.get("resized")

            # 释放订阅期间持有的会话：使用连接上下文保存的引用
            # （会话结束被移出仓库后 get_session 已取不到，须凭持有凭证释放）
            held_session = ctx.connection.pop_held_session(sid)

            if sid:
                if held_session is not None:
                    held_session.release_hold()
                session = ctx.session_repo.get_session(sid)
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
                ctx.connection.remove_decoder(sid)

            ctx.connection.remove_subscription(sid)
            ctx.connection.clear_callbacks(sid)

        return [Response.ws_unsubscribed()]


class InputHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        data = msg.get("data", "")
        if not isinstance(data, str):
            return [Response.error("input data must be a string")]
        if len(data) > MAX_INPUT_LEN:
            return [Response.error(f"input too long (max {MAX_INPUT_LEN})")]

        session = ctx.session_repo.get_session(session_id)
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
            _logger.warning("input write failed: sid=%s err=%s", session_id, e)
            return [Response.error(f"write failed: {e}")]
        return []


class KeyInputHandler(MessageHandler):
    """模式感知键盘事件 → wezterm 服务端编码 → 写 pty

    前端发送原始按键（key + mods），daemon 用 wezterm-py Terminal
    按终端当前状态编码为对应 VT 序列并写入 pty。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        key = msg.get("key", "")
        if not isinstance(key, str) or not key:
            return [Response.error("key must be a non-empty string")]
        try:
            mods = int(msg.get("mods", 0))
        except (TypeError, ValueError):
            return [Response.error("mods must be an integer")]

        session = ctx.session_repo.get_session(session_id)
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
            _logger.warning("key input write failed: sid=%s err=%s", session_id, e)
            return [Response.error(f"write failed: {e}")]
        return []


class MouseInputHandler(MessageHandler):
    """模式感知鼠标事件 → wezterm 服务端编码 → 写 pty

    前端发送原始鼠标事件（x/y 0-based 单元格 + kind/button/mods）。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
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

        session = ctx.session_repo.get_session(session_id)
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
            _logger.warning("mouse input write failed: sid=%s err=%s", session_id, e)
            return [Response.error(f"write failed: {e}")]
        return []


class SignalHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        sig = msg.get("signal", "")
        _logger.info("SignalHandler: sid=%s sig=%s", session_id, sig)
        session = ctx.session_repo.get_session(session_id)
        if not session:
            _logger.warning("SignalHandler: session not found sid=%s", session_id)
            return []
        if not session.running:
            _logger.warning("SignalHandler: session not running sid=%s", session_id)
            return []
        if sig == "SIGINT":
            try:
                await ctx.executor.run(session.send_signal, sig)
                _logger.info("SignalHandler: SIGINT sent sid=%s", session_id)
            except Exception as e:
                _logger.warning(
                    "signal send failed: sid=%s sig=%s err=%s", session_id, sig, e
                )
                return [Response.error(f"signal failed: {e}")]
        else:
            return [Response.error(f"unsupported signal: {sig}")]
        return []


class ResizeHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        cols_raw = msg.get("cols")
        rows_raw = msg.get("rows")
        try:
            cols = int(cols_raw) if cols_raw is not None else DEFAULT_COLS
            rows = int(rows_raw) if rows_raw is not None else DEFAULT_ROWS
        except (TypeError, ValueError):
            return [Response.error("invalid cols or rows")]
        session = ctx.session_repo.get_session(session_id)
        if not session:
            _logger.warning("resize: session not found sid=%s", session_id)
            return []

        # 子进程模式无终端，禁止 resize
        if getattr(session, "mode", "pty") == "subprocess":
            return [Response.ws_error("subprocess mode does not support resize", code="subprocess_no_resize")]

        # 自适应排他锁：存在自适应持有者且当前连接不是持有者时拒绝 resize，
        # 避免非持有者的自适应调整抢占持有者的尺寸控制权；
        # 持有者以 client_uid 标识（localStorage 持久化），同一 client_uid 的
        # 多个连接（多标签页）都允许 resize
        if ctx.adaptive_lock is not None:
            initiator_uid = ctx.connection.client_uid
            owner = ctx.adaptive_lock.get_owner(session_id)
            if owner is not None and owner != initiator_uid:
                _logger.warning(
                    "resize rejected: sid=%s initiator_uid=%s not adaptive owner (owner=%s)",
                    session_id,
                    initiator_uid,
                    owner,
                )
                return [Response.ws_error("adaptive size control held by another client", code="adaptive_locked")]

        try:
            # session.resize() 按 ConPTY 语义重排并返回终端模型 snapshot
            #     （可见区内容 + 光标均与 ConPTY 坐标系一致，杜绝光标错位）
            snapshot = await ctx.executor.run(session.resize, cols, rows)
        except Exception as e:
            _logger.warning("resize failed: sid=%s err=%s", session_id, e)
            return []
        # resize 场景下 scrollback 始终为空：
        # ConPTY repaint 可能触发 index() 将可见区行推入 scrollback，
        # 导致 scrollback 与 snapshot（读终端模型可见区）内容重叠，
        # 前端 restoreScrollbackAndSnapshot 会将同一内容写两遍。
        # session.resize() 已在 snapshot 前清除 scrollback，但 resize 返回后
        # reader 线程可能继续 feed repaint 残余字节再次产生 scrollback，
        # 此处再次清除确保 resize_complete/session_resized 中 scrollback 为空。
        # 后续正常输出滚动会重新产生正确的 scrollback。
        try:
            await ctx.executor.run(session.clear_scrollback)
        except Exception:
            pass
        scrollback_ansi = ""
        snapshot_len = len(snapshot) if snapshot else 0
        scrollback_len = len(scrollback_ansi) if scrollback_ansi else 0
        _logger.info(
            "resize: sid=%s cols=%dx%d snapshot_len=%d scrollback_len=%d",
            session_id,
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
                session_id=session_id,
                cols=cols,
                rows=rows,
                snapshot=snapshot or "",
                scrollback=scrollback_ansi or "",
                exclude_conn_id=initiator_conn_id,
            )
            _logger.info(
                "resize: broadcast session_resized sid=%s %dx%d excluded initiator=0x%x",
                session_id,
                cols,
                rows,
                initiator_conn_id,
            )
        except Exception as e:
            _logger.exception(
                "resize: publish_session_resized failed sid=%s: %s", session_id, e
            )

        # snapshot 含 PTY 真实光标定位（\x1b[row;colH），前端 \x1b[3J + scrollback
        # + \x1b[2J + snapshot 重建 buffer 后与 ConPTY 坐标系完全一致
        return [
            {
                "type": "resize_complete",
                "sessionId": session_id,
                "cols": cols,
                "rows": rows,
                "snapshot": snapshot or "",
                "scrollback": scrollback_ansi or "",
            }
        ]


class KillSessionHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        session = ctx.session_repo.get_session(session_id)
        if not session:
            return [Response.error(f"session '{session_id}' not found")]

        # 先取消订阅指定会话，避免残留回调，不影响其他会话的订阅
        unsubscribe_handler = UnsubscribeSessionHandler()
        await unsubscribe_handler.handle(ctx, {"session_id": session_id})

        try:
            await ctx.executor.run(ctx.session_repo.remove_session, session_id)
        except Exception as e:
            _logger.exception("kill failed: sid=%s", session_id)
            return [Response.error(f"kill failed: {e}")]

        return [
            Response.ws_session_ended(
                session_id, session.exit_code, session.error_message
            )
        ]


class DeleteHistoryHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        if ctx.history_repo:
            ctx.history_repo.delete_session(session_id)
        return [Response.ws_history_deleted(session_id)]


class SessionDetailHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        session = ctx.session_repo.get_session(session_id)
        if not session:
            if ctx.history_repo:
                detail = ctx.history_repo.get_session_detail(session_id)
                if detail:
                    payload = {
                        "type": "session_detail",
                        "source": "history",
                        "id": detail.id,
                        "command": detail.command,
                        "ptyType": detail.pty_type,
                        "cols": detail.cols,
                        "rows": detail.rows,
                        "encoding": detail.encoding,
                        "startTime": detail.start_time,
                        "endTime": detail.end_time,
                        "exitCode": detail.exit_code,
                        "errorMessage": detail.error_message,
                        "running": False,
                    }
                    if detail.screen_buffer_z:
                        payload["screenBufferZ"] = detail.screen_buffer_z
                        payload["screenBufferMeta"] = detail.screen_buffer_meta
                    if detail.output_gz:
                        payload["outputGz"] = detail.output_gz
                        payload["outputGzOriginalLen"] = detail.output_gz_original_len
                    if detail.events:
                        payload["events"] = detail.events
                    return [payload]
            return [Response.error(f"session '{session_id}' not found")]

        detail = await self._build_active_detail(ctx, session)
        return [self._detail_to_message(detail, source="active")]

    async def _build_active_detail(
        self, ctx: HandlerContext, session: Any
    ) -> SessionDetail:
        from ...process.info import (
            _get_process_tree,
        )

        command = _command_to_string(session.command)
        pids = []
        try:
            pids = await ctx.executor.run(lambda: session.get_pty_process_list())
        except Exception:
            pass
        pids = [p for p in pids if p > 0]

        child_pid = None
        try:
            child_pid = await ctx.executor.run(lambda: session.get_pty_child_pid())
        except Exception:
            pass

        process_tree, process_details_raw = await ctx.executor.run(
            _get_process_tree, pids, root_pid=child_pid or 0
        )
        process_details = {str(pid): d for pid, d in process_details_raw.items()}
        events = await ctx.executor.run(session.get_all_events)
        gui_windows = session.gui_windows

        return SessionDetail(
            id=session.id,
            uid=session.uid,
            command=command,
            pty_type=session.pty_type,
            cols=session.cols,
            rows=session.rows,
            encoding=session.encoding or "utf-8",
            start_time=session.start_time,
            running=session.running,
            exit_code=session.exit_code,
            error_message=session.error_message,
            cwd=session.cwd or "",
            process_tree=process_tree,
            process_details=process_details,
            events=events,
            gui_windows=gui_windows,
            output_size=session.output_offset,
        )

    @staticmethod
    def _detail_to_message(detail: SessionDetail, source: str) -> dict:
        return {
            "type": "session_detail",
            "source": source,
            "id": detail.id,
            "uid": detail.uid,
            "command": detail.command,
            "ptyType": detail.pty_type,
            "cols": detail.cols,
            "rows": detail.rows,
            "encoding": detail.encoding,
            "startTime": detail.start_time,
            "running": detail.running,
            "exitCode": detail.exit_code,
            "errorMessage": detail.error_message,
            "cwd": detail.cwd,
            "processTree": detail.process_tree,
            "processDetails": detail.process_details,
            "events": detail.events,
            "guiWindows": detail.gui_windows,
            "outputSize": detail.output_size,
        }


class SessionDetailRefreshHandler(MessageHandler):
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        tab = msg.get("tab", "info")
        session = ctx.session_repo.get_session(session_id)
        if not session:
            return []

        result = {"type": "session_detail_refresh", "id": session_id, "tab": tab}
        if tab == "info":
            result.update(
                {
                    "running": session.running,
                    "exitCode": session.exit_code,
                    "errorMessage": session.error_message,
                    "outputSize": session.output_offset,
                }
            )
        elif tab == "process":
            from ...process.info import _get_process_detail

            pids = []
            try:
                pids = await ctx.executor.run(lambda: session.get_pty_process_list())
            except Exception:
                pass
            pids = [p for p in pids if p > 0]
            process_details = {}
            for pid in pids:
                d = await ctx.executor.run(_get_process_detail, pid)
                if d:
                    process_details[str(pid)] = {
                        "pid": d["pid"],
                        "memoryMb": d.get("memoryMb"),
                        "cpuSeconds": d.get("cpuSeconds"),
                    }
            result["processDetails"] = process_details
        return [result]


# --------------------------------------------------------------------------- #
# VNC 远程桌面处理器
# --------------------------------------------------------------------------- #


class VncStatusHandler(MessageHandler):
    """查询 VNC 服务状态。

    返回 {type: vnc_status, running, disabled, winvnc_available, ...}。
    VNC 未启用时返回 disabled=true，前端据此隐藏 UI 入口。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.vnc_service:
            return [
                Response.ws_vnc_status(
                    {
                        "running": False,
                        "disabled": True,
                        "winvnc_available": False,
                        "vnc_port": None,
                        "password": None,
                    }
                )
            ]
        status = await ctx.executor.run(ctx.vnc_service.get_status)
        return [Response.ws_vnc_status(status)]


class VncStartHandler(MessageHandler):
    """启动 VNC 服务（按需启动 winvnc.exe）。

    单例语义：若已在运行，直接返回当前连接信息。
    启动是同步阻塞操作（最多 30 秒），通过 ThreadExecutor 调度避免阻塞事件循环。
    WebSocket→VNC TCP 代理由守护进程 /vnc/websockify 端点实现，无需 websockify。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.vnc_service:
            return [Response.ws_vnc_error("VNC service not available", code="vnc.service_unavailable")]
        if not ctx.vnc_service.is_available():
            return [Response.ws_vnc_error("VNC unavailable", code="vnc.unavailable")]
        try:
            connection_info = await ctx.executor.run(ctx.vnc_service.start)
            _logger.info(
                "VNC started: vnc_port=%s",
                connection_info.get("vnc_port"),
            )
            return [Response.ws_vnc_started(connection_info)]
        except Exception as e:
            _logger.exception("VNC start failed")
            return [Response.ws_vnc_error("VNC start failed", code="vnc.start_failed", params={"error": str(e)})]


class VncStopHandler(MessageHandler):
    """停止 VNC 服务。"""

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.vnc_service:
            return [Response.ws_vnc_error("VNC service not available", code="vnc.service_unavailable")]
        try:
            await ctx.executor.run(ctx.vnc_service.stop)
            _logger.info("VNC stopped")
            return [Response.ws_vnc_stopped()]
        except Exception as e:
            _logger.exception("VNC stop failed")
            return [Response.ws_vnc_error("VNC stop failed", code="vnc.stop_failed", params={"error": str(e)})]


# --------------------------------------------------------------------------- #
# FastScreen 屏幕查看处理器
# --------------------------------------------------------------------------- #


class FsStatusHandler(MessageHandler):
    """查询 FastScreen 服务状态。

    返回 {type: fs_status, disabled, available, active_sessions}。
    FastScreen 未启用时返回 disabled=true，前端据此隐藏 UI 入口。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.screenshare_service:
            status = {
                "disabled": True,
                "available": False,
                "active_sessions": 0,
            }
        else:
            status = await ctx.executor.run(ctx.screenshare_service.get_status)
        if ctx.cursor_locator_service:
            cl_status = await ctx.executor.run(ctx.cursor_locator_service.get_status)
            status["cursor_locator_running"] = cl_status.get("running", False)
            status["cursor_locator_available"] = cl_status.get("available", False)
        else:
            status["cursor_locator_running"] = False
            status["cursor_locator_available"] = False
        return [Response.ws_fs_status(status)]


class FsListTargetsHandler(MessageHandler):
    """列出可查看目标（显示器 + 窗口）。

    前端打开 FastScreen tab 或点击"刷新"按钮时调用。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.screenshare_service:
            return [
                Response.ws_fs_targets(
                    {
                        "disabled": True,
                        "monitors": [],
                        "windows": [],
                    }
                )
            ]
        try:
            targets = await ctx.executor.run(ctx.screenshare_service.list_targets)
            return [Response.ws_fs_targets(targets)]
        except Exception as e:
            _logger.exception("FastScreen list_targets failed")
            return [Response.ws_fs_error("list targets failed", code="fs.list_targets_failed", params={"error": str(e)})]


class FsBringToFrontHandler(MessageHandler):
    """将指定窗口置于前台（恢复最小化 + 激活）。

    前端在窗口最小化提示中点击"置于前台"按钮时调用。
    仅 Windows 平台、窗口模式可用，使用 ShowWindowAsync(SW_RESTORE) + SetForegroundWindow。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not IS_WINDOWS:
            return [Response.ws_fs_error("bring to front supported on Windows only", code="fs.windows_only")]

        target_type = msg.get("target_type", "monitor")
        if target_type != "window":
            return [Response.ws_fs_error("bring to front supported in window mode only", code="fs.window_mode_only")]

        hwnd = msg.get("target_id", 0)
        try:
            hwnd = int(hwnd)
        except (TypeError, ValueError):
            return [Response.ws_fs_error("invalid window handle", code="fs.invalid_hwnd")]

        if hwnd == 0:
            return [Response.ws_fs_error("invalid window handle", code="fs.invalid_hwnd")]

        try:
            import ctypes

            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            # 先恢复（如果最小化了），再置于前台
            user32.ShowWindowAsync(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            _logger.info("FastScreen bring window to front: hwnd=%d", hwnd)
            return []
        except Exception as e:
            _logger.exception("FastScreen bring to front failed: hwnd=%d", hwnd)
            return [Response.ws_fs_error("bring to front failed", code="fs.bring_to_front_failed", params={"error": str(e)})]


# --------------------------------------------------------------------------- #
# 鼠标增强光标定位器处理器
# --------------------------------------------------------------------------- #


class CursorLocatorStartHandler(MessageHandler):
    """启动鼠标增强光标定位器。

    服务端单例：若已在运行，直接返回成功。
    启动操作通过 ThreadExecutor 调度避免阻塞事件循环。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.cursor_locator_service:
            return [Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")]
        if not ctx.cursor_locator_service.is_available():
            return [
                Response.ws_cursor_locator_error("cursor locator unavailable, Windows only", code="locator.windows_only")
            ]
        try:
            result = await ctx.executor.run(ctx.cursor_locator_service.start)
            if result.get("running"):
                _logger.info("CursorLocator started")
                return [Response.ws_cursor_locator_started()]
            return [Response.ws_cursor_locator_error("start failed", code="locator.start_failed", params={"error": result.get("error", "")})]
        except Exception as e:
            _logger.exception("CursorLocator start failed")
            return [Response.ws_cursor_locator_error("start failed", code="locator.start_failed", params={"error": str(e)})]


class CursorLocatorStopHandler(MessageHandler):
    """停止鼠标增强光标定位器。"""

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.cursor_locator_service:
            return [Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")]
        try:
            result = await ctx.executor.run(ctx.cursor_locator_service.stop)
            if not result.get("running"):
                _logger.info("CursorLocator stopped")
                return [Response.ws_cursor_locator_stopped()]
            return [Response.ws_cursor_locator_error("stop failed", code="locator.stop_failed", params={"error": result.get("error", "")})]
        except Exception as e:
            _logger.exception("CursorLocator stop failed")
            return [Response.ws_cursor_locator_error("stop failed", code="locator.stop_failed", params={"error": str(e)})]


class CursorLocatorUpdateConfigHandler(MessageHandler):
    """修改鼠标增强光标定位器配置参数。

    支持 outer_radius / inner_radius / alpha 三个参数，
    运行时实时生效（调用 cursorlocator.update_config），同时持久化到 JSON。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        if not ctx.cursor_locator_service:
            return [Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")]
        params = {}
        for key in ("outer_radius", "inner_radius", "alpha"):
            if key in msg:
                try:
                    params[key] = int(msg[key])
                except (TypeError, ValueError):
                    return [Response.ws_cursor_locator_error("invalid parameter", code="locator.invalid_param", params={"key": key})]
        if not params:
            return [Response.ws_cursor_locator_error("no parameters specified", code="locator.no_params")]
        try:
            result = await ctx.executor.run(
                ctx.cursor_locator_service.update_config, **params
            )
            if "error" in result:
                return [Response.ws_cursor_locator_error("update failed", code="locator.update_failed", params={"error": result["error"]})]
            _logger.info("CursorLocator config updated: %s", params)
            status = await ctx.executor.run(ctx.cursor_locator_service.get_status)
            return [Response.ws_cursor_locator_status(status)]
        except Exception as e:
            _logger.exception("CursorLocator update_config failed")
            return [Response.ws_cursor_locator_error("update failed", code="locator.update_failed", params={"error": str(e)})]


# --------------------------------------------------------------------------- #
# 自适应排他锁：接管与模式设定处理器
# --------------------------------------------------------------------------- #


class TakeoverSizeControlHandler(MessageHandler):
    """处理接管尺寸控制权请求。

    前端非自适应持有者点击"接管"按钮时发送 takeover_size_control。
    后端清空自适应锁（当前持有者降级），允许接管者随后设定新模式。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        if not session_id:
            return [Response.error("missing session_id")]
        if ctx.adaptive_lock is None:
            return [Response.error("adaptive lock service unavailable")]

        # 清空自适应锁：旧持有者将被降级（前端收到 size_mode_changed 后切 fixed）
        old_owner = ctx.adaptive_lock.clear(session_id)
        _logger.info(
            "takeover_size_control: sid=%s initiator_uid=%s old_owner=%s",
            session_id,
            ctx.connection.client_uid,
            old_owner if old_owner is not None else "None",
        )

        # 广播模式变更：adaptive_owner_active=False（无人持有锁）
        # 不排除发起方：发起方前端需据此解锁 UI
        try:
            ctx.publisher.publish_size_mode_changed(
                session_id=session_id,
                adaptive_owner_active=False,
            )
        except Exception as e:
            _logger.exception(
                "takeover: publish_size_mode_changed failed sid=%s: %s", session_id, e
            )

        return [{"type": "takeover_ack", "sessionId": session_id}]


class SetSizeModeHandler(MessageHandler):
    """处理设定尺寸模式请求。

    前端选择尺寸模式（adaptive/fixed/custom/default）时发送 set_size_mode。
    - adaptive：夺取自适应锁（当前持有者降级），持有者可自由自适应调整
    - fixed/custom/default：释放锁（若自己是持有者），按模式 resize 并广播

    锁持有者以 client_uid 标识（localStorage 持久化，刷新不变）。
    广播 size_mode_changed 时携带 adaptive_owner_uid，前端据此判断"自己是否持锁"。
    """

    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        session_id = msg.get("session_id", "")
        mode = msg.get("mode", "")
        if not session_id or not mode:
            return [Response.error("missing session_id or mode")]
        if mode not in ("adaptive", "fixed", "custom", "default"):
            return [Response.error(f"invalid mode: {mode}")]
        if ctx.adaptive_lock is None:
            return [Response.error("adaptive lock service unavailable")]

        session = ctx.session_repo.get_session(session_id)
        if not session:
            return [Response.error(f"session '{session_id}' not found")]

        # 用 client_uid 作为锁持有者标识（刷新不变，同 uid 多连接共享）
        initiator_uid = ctx.connection.client_uid
        initiator_conn_id = id(ctx.channel)  # 仍用于广播排除发起方连接

        if mode == "adaptive":
            # 夺取自适应锁：旧持有者降级，自己成为新持有者
            old_owner = ctx.adaptive_lock.acquire(session_id, initiator_uid)
            _logger.info(
                "set_size_mode adaptive: sid=%s new_owner=%s old_owner=%s",
                session_id,
                initiator_uid,
                old_owner if old_owner is not None else "None",
            )
            # 广播：adaptive_owner_active=True（有人持有锁）
            # 携带 adaptive_owner_uid，其他客户端据此判断"自己是否持锁"
            # 排除发起方（发起方前端已自行切换到 adaptive 模式）
            try:
                ctx.publisher.publish_size_mode_changed(
                    session_id=session_id,
                    adaptive_owner_active=True,
                    mode="adaptive",
                    adaptive_owner_uid=initiator_uid,
                    exclude_conn_id=initiator_conn_id,
                )
            except Exception as e:
                _logger.exception(
                    "set_size_mode adaptive: publish failed sid=%s: %s", session_id, e
                )
            return [
                {"type": "size_mode_ack", "sessionId": session_id, "mode": "adaptive"}
            ]

        # 非 adaptive 模式：释放锁（若自己是持有者）+ 按模式 resize
        ctx.adaptive_lock.release(session_id, initiator_uid)

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
            try:
                snapshot = await ctx.executor.run(session.resize, cols, rows)
            except Exception as e:
                _logger.exception(
                    "set_size_mode %s resize failed sid=%s: %s", mode, session_id, e
                )
                return [Response.error(f"resize failed: {e}")]
            actual_cols = cols
            actual_rows = rows
            # 广播尺寸变更（session_resized）给其他客户端，让它们跟随新尺寸
            # resize 场景下 scrollback 始终为空（同 ResizeHandler，见其注释）
            try:
                await ctx.executor.run(session.clear_scrollback)
            except Exception:
                pass
            scrollback_ansi = ""
            try:
                ctx.publisher.publish_session_resized(
                    session_id=session_id,
                    cols=cols,
                    rows=rows,
                    snapshot=snapshot or "",
                    scrollback=scrollback_ansi or "",
                    exclude_conn_id=initiator_conn_id,
                )
            except Exception as e:
                _logger.exception(
                    "set_size_mode: publish_session_resized failed sid=%s: %s",
                    session_id,
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
                session_id=session_id,
                adaptive_owner_active=ctx.adaptive_lock.has_owner(session_id),
                mode=mode,
                cols=actual_cols,
                rows=actual_rows,
                exclude_conn_id=initiator_conn_id,
            )
        except Exception as e:
            _logger.exception(
                "set_size_mode %s: publish failed sid=%s: %s", mode, session_id, e
            )

        _logger.info(
            "set_size_mode %s: sid=%s cols=%dx%d initiator_uid=%s",
            mode,
            session_id,
            actual_cols,
            actual_rows,
            initiator_uid,
        )
        return [
            {
                "type": "size_mode_ack",
                "sessionId": session_id,
                "mode": mode,
                "cols": actual_cols,
                "rows": actual_rows,
            }
        ]


# --------------------------------------------------------------------------- #
# 处理器注册表
# --------------------------------------------------------------------------- #


def build_handler_registry() -> dict[str, MessageHandler]:
    """构建消息类型到处理器的映射。"""
    return {
        "ping": PingHandler(),
        "list": ListSessionsHandler(),
        "shells": ListShellsHandler(),
        "system_stats": SystemStatsHandler(),
        "history": ListHistoryHandler(),
        "history_detail": HistoryDetailHandler(),
        "create": CreateSessionHandler(),
        "subscribe": SubscribeSessionHandler(),
        "unsubscribe": UnsubscribeSessionHandler(),
        "input": InputHandler(),
        "key": KeyInputHandler(),
        "mouse": MouseInputHandler(),
        "signal": SignalHandler(),
        "resize": ResizeHandler(),
        "kill": KillSessionHandler(),
        "delete_history": DeleteHistoryHandler(),
        "session_detail": SessionDetailHandler(),
        "session_detail_refresh": SessionDetailRefreshHandler(),
        "vnc_status": VncStatusHandler(),
        "vnc_start": VncStartHandler(),
        "vnc_stop": VncStopHandler(),
        "fs_status": FsStatusHandler(),
        "fs_list_targets": FsListTargetsHandler(),
        "fs_bring_to_front": FsBringToFrontHandler(),
        "cursor_locator_start": CursorLocatorStartHandler(),
        "cursor_locator_stop": CursorLocatorStopHandler(),
        "cursor_locator_update_config": CursorLocatorUpdateConfigHandler(),
        # 自适应排他锁
        "takeover_size_control": TakeoverSizeControlHandler(),
        "set_size_mode": SetSizeModeHandler(),
    }
