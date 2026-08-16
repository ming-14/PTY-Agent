import time
import traceback
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from ..execution import (
    _run_snapshot_flow,
    _run_subprocess_no_trigger_flow,
    _run_subprocess_trigger_flow,
)
from .base import DaemonHandler, HandlerContext
from .utils import apply_client_defaults
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class ExecHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        from ...config.common import (
            MAX_COMMAND_LEN,
            MAX_PATTERN_LEN,
            MAX_SESSION_ID_LEN,
        )
        from .utils import (
            GIT_BASH_PATH_HINT,
            check_ended_session,
            has_git_bash_style_path,
            validate_request,
            validate_trigger_regex,
        )

        session_id = msg.get("id", "")
        command = msg.get("command")
        trigger = msg.get("trigger")
        if not validate_request(
            conn,
            msg,
            [
                ("id", MAX_SESSION_ID_LEN),
                ("command", MAX_COMMAND_LEN),
                ("trigger", MAX_PATTERN_LEN),
            ],
        ):
            return
        if not validate_trigger_regex(trigger, conn):
            return
        _logger.info(
            "_handle_exec: id=%r cmd=%r trigger=%r encoding=%r timeout=%r "
            "idle_timeout=%r idle_after_first=%r",
            session_id,
            command[:200] if isinstance(command, str) else command,
            trigger,
            msg.get("encoding"),
            msg.get("timeout"),
            msg.get("idle_timeout"),
            msg.get("idle_after_first_output"),
        )

        if not session_id:
            Message.send(conn, Response.error("Missing session id"))
            return
        if not command:
            Message.send(conn, Response.error("Missing command parameter"))
            return

        existing = ctx.manager.get_session(session_id)
        if existing:
            if not existing.running:
                Message.send(
                    conn,
                    Response.error(
                        f"Session '{session_id}' ended, kill and re-exec to restart"
                    ),
                )
                return
            session = existing
            _logger.info("会话 '%s' 已存在，直接附加", session_id)
        elif check_ended_session(ctx.manager, session_id):
            Message.send(
                conn,
                Response.error(
                    f"Session '{session_id}' ended, kill and re-exec to restart"
                ),
            )
            return
        else:
            # 插件解析：显式 --plugin 指定 + 自动加载（auto_load 条件命中）
            # 自动注入仅在会话创建时生效，已有会话附加不重复注入
            plugins = msg.get("plugins") or []          # daemon 形态：挂载到会话
            cli_plugins = msg.get("cliPlugins") or []   # CLI 形态：记录到会话，供客户端后续挂钩
            auto = ctx.manager.match_auto_load(command, msg.get("cwd"), msg.get("env"))
            if auto:
                _logger.info("自动注入插件命中 (sid=%r): %s", session_id, auto)
            merged = auto + [p for p in plugins if p not in auto]
            try:
                session = ctx.manager.create_session(
                    session_id,
                    command,
                    encoding=msg.get("encoding"),
                    cwd=msg.get("cwd"),
                    env=msg.get("env"),
                    cols=msg.get("cols"),
                    rows=msg.get("rows"),
                    plugins=merged or None,
                    cli_plugins=cli_plugins or None,
                    mode=msg.get("mode", "pty"),
                )
                log_cmd = command if isinstance(command, str) else " ".join(command)
                _logger.info("创建会话 '%s': %s", session_id, log_cmd)
            except KeyError:
                Message.send(
                    conn, Response.error(f"Session '{session_id}' already exists")
                )
                return
            except ValueError as e:
                Message.send(conn, Response.error(str(e)))
                return
            except Exception as e:
                tb = traceback.format_exc()
                _logger.error("会话 '%s' 启动失败: %s", session_id, e)
                _logger.error(tb)
                err_msg = f"Failed to start session: {e}"
                if has_git_bash_style_path(command):
                    err_msg += f". {GIT_BASH_PATH_HINT}"
                Message.send(conn, Response.error(err_msg))
                return

        # 处理期间持有会话：会话可能在本 handler 等待输出期间自然结束，
        # 管理器会触发 release_components 释放大缓冲；hold 确保缓冲在
        # 响应构造完成前不被提前释放（最后一个 hold 退出时才实际释放）
        with session.hold():
            return self._handle_exec_flow(ctx, conn, session, msg, existing, trigger)

    def _handle_exec_flow(self, ctx, conn, session, msg, existing, trigger):
        """exec 会话处理主体（已持有 session.hold）"""
        apply_client_defaults(session, msg)

        if getattr(session, "mode", "pty") == "subprocess":
            if msg.get("snapshot_diff"):
                Message.send(
                    conn,
                    Response.error(
                        "子进程模式不支持 --snapshot-diff（无终端快照），请用增量输出"
                    ),
                )
                return
            # 子进程模式：增量输出 + stderr 分离，支持 trigger/read offset
            if trigger:
                trigger_offset = (
                    0 if (msg.get("full") or not existing) else session.output_offset
                )
                start_offset = 0 if not existing else None
                _run_subprocess_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    trigger_offset,
                    trigger,
                    msg.get("newline", False),
                    msg.get("fresh", False),
                    msg.get("timeout", 120),
                    start_offset=start_offset,
                    result_type="exec",
                )
            else:
                _run_subprocess_no_trigger_flow(
                    ctx,
                    conn,
                    session,
                    msg,
                    result_type="exec",
                    from_offset=(
                        0 if (msg.get("full") or not existing) else session.output_offset
                    ),
                )
        else:
            # pty 模式恒为屏幕快照，trigger/idle-timeout 由 _run_snapshot_flow 内部处理
            _run_snapshot_flow(ctx, conn, session, msg, result_type="exec")


