import base64
import gzip
import json
import re
import time
from typing import Optional

from ...config.common import (
    GZIP_COMPRESS_LEVEL,
)
from ...output import safe_regex_search
from ...protocol.ansi import strip_ansi
from ...protocol.message import Message
from ...protocol.response import Response
from ...session.manager import SessionManager
from ...logging import get_logger

_logger = get_logger("pty-daemon")

_REASON_MAP = {
    "matched": "trigger_matched",
    "timeout": "trigger_timeout",
    "idle_timeout": "idle_timeout",
    "ended": "program_ended",
    "gui_detected": "gui_detected",
    "crashed": "program_crashed",
    "ok": "ok",
}

_EVENTS_HINT = "Events are consumed when exec/send/mouse commands return. Below are pending events captured at that time."

_EVENTS_NO_ARGS_HINT = (
    "Only unconsumed events are shown. Use -l <N> to view the full event history."
)

_SESSION_ENDED_HINT = (
    "The session has ended. You are now viewing the data that was produced earlier."
)

# Git-Bash 风格路径提示（exec 响应路径提示用，属核心呈现；原 files 包已插件化）
GIT_BASH_PATH_HINT = "非Git-Bash请不要使用Git-Bash风格路径(如 /c/foo)，请使用 Windows 风格路径(如 C:/foo)"

_GIT_BASH_PATH_RE = re.compile(r"(?:^|\s|[=\"'])/[a-zA-Z]/")


def has_git_bash_style_path(command) -> bool:
    """检测文本中的 Git-Bash 风格路径（/c/...），供写路径参数时提示"""
    if isinstance(command, list):
        command = " ".join(command)
    if not isinstance(command, str):
        return False
    return bool(_GIT_BASH_PATH_RE.search(command))


def compress_screen_buffer(buf: dict) -> str:
    raw = json.dumps(buf, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=GZIP_COMPRESS_LEVEL)
    return base64.b64encode(compressed).decode("ascii")


def map_reason(reason: str, exit_code=None) -> str:
    if reason == "ended" and exit_code is not None and exit_code != 0:
        return "program_crashed"
    return _REASON_MAP.get(reason, reason)


def filter_snapshot_lines(
    output: str, lines_param, column_param=None, grep=None
) -> str:
    if not output:
        return output
    # 单次 splitlines：lines/grep/column 依次作用于同一行列表
    snap_lines = output.splitlines()

    if lines_param is not None:
        if isinstance(lines_param, int):
            snap_lines = snap_lines[-lines_param:] if lines_param > 0 else []
        elif isinstance(lines_param, str) and ":" in lines_param:
            parts = lines_param.split(":", 1)
            try:
                start = int(parts[0]) if parts[0] else 1
                end = int(parts[1]) if parts[1] else len(snap_lines)
                start = max(start, 1)
                snap_lines = snap_lines[start - 1 : end]
            except (ValueError, IndexError):
                snap_lines = []
        else:
            try:
                n = int(lines_param)
                snap_lines = snap_lines[-n:] if n > 0 else []
            except ValueError:
                snap_lines = []
    if grep:
        if not snap_lines:
            return ""
        try:
            pat = re.compile(grep)
            snap_lines = [l for l in snap_lines if safe_regex_search(pat, l)]
        except re.error:
            return ""
    if column_param is not None:
        col_idx = column_param - 1
        snap_lines = [
            line[col_idx] if 0 <= col_idx < len(line) else "" for line in snap_lines
        ]
    return "\n".join(snap_lines)


def build_hint(
    command_type: str,
    reason: str,
    session_running: bool,
    has_trigger: bool,
    exit_code=None,
) -> str:
    is_exec = command_type == "exec"
    is_send = command_type == "send"
    is_read = command_type == "read"

    if is_read:
        if not session_running:
            return _SESSION_ENDED_HINT
        return ""

    prefix = (
        "The program started successfully" if is_exec else "Input sent successfully"
    )

    if reason == "trigger_matched":
        return f"{prefix}. It is now returning due to trigger match."
    elif reason == "trigger_timeout":
        return f"{prefix}. Trigger wait timed out."
    elif reason == "idle_timeout":
        return f"{prefix}. Output has been idle."
    elif reason == "program_ended":
        return f"{prefix} but has now ended."
    elif reason == "gui_detected":
        return f"{prefix}. A GUI window was detected."
    elif reason == "program_crashed":
        ec = f" {exit_code}" if exit_code is not None else ""
        return f"Program crashed with exit code:{ec}."
    elif reason == "ok":
        if not session_running:
            return _SESSION_ENDED_HINT
        return f"{prefix}."

    # 内置原始 reason 不应落入默认分支（如 "matched"/"timeout" 等未映射值）
    if reason in _REASON_MAP:
        return ""

    # 插件自定义返回原因（request_return）：不在内置映射表中，原样透传
    return f"{prefix}. Returning due to plugin request ({reason})."


def validate_field(value, name: str, max_len: int, conn) -> bool:
    if isinstance(value, str) and len(value) > max_len:
        Message.send(
            conn, Response.error(f"Parameter '{name}' too long (max {max_len} chars)")
        )
        return False
    return True


def format_iso_ms(timestamp: float) -> str:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"


def attach_screen_buffer(result: dict, session, msg: dict):
    if not msg.get("include_screen_buffer"):
        return
    # 屏幕内容仅随 feed/resize 变化：按 (feed_count, cols, rows) 缓存压缩结果，
    # 避免高频命令重复 export + json.dumps + gzip + base64
    screen = getattr(session, "_screen", None)
    key = (screen.feed_count, screen.cols, screen.rows) if screen else None
    cached = getattr(session, "_screen_buffer_cache", None)
    if cached is not None and cached[0] == key:
        compressed, meta = cached[1], cached[2]
    else:
        screen_buffer = session.export_screen_buffer()
        if not screen_buffer:
            return
        compressed = compress_screen_buffer(screen_buffer)
        meta = {
            "cols": screen_buffer.get("cols", 0),
            "rows": screen_buffer.get("rows", 0),
            "sparse": True,
            "compressed": True,
        }
        session._screen_buffer_cache = (key, compressed, meta)
    result["screenBufferZ"] = compressed
    result["screenBufferMeta"] = meta


def build_result(
    manager: SessionManager,
    session_id: str,
    output: str,
    matched: bool,
    reason: str,
    consume_events: bool = False,
    has_trigger: bool = True,
    result_type: str = "exec",
    warning: Optional[str] = None,
    session=None,
    t_start: Optional[float] = None,
    output_offset: Optional[int] = None,
    include_debug: Optional[bool] = None,
) -> dict:
    if session is None:
        session = manager.get_session(session_id)
    pty_type = session.pty_type if session else "none"
    running = session.running if session else False
    exit_code = session.exit_code if session else None
    error_message = session.error_message if session else None

    if exit_code == 0 and session:
        crash_events = [
            e
            for e in session.get_all_events()
            if e.get("type") == "process_crash" and e.get("detail", {}).get("exitCode")
        ]
        if crash_events:
            crash_ec = crash_events[-1]["detail"]["exitCode"]
            if crash_ec != 0:
                exit_code = crash_ec
                if not error_message:
                    from ...process import _format_exit_code_message

                    error_message = _format_exit_code_message(crash_ec)

    mapped_reason = map_reason(reason, exit_code)

    result: dict = {
        "commandType": result_type,
        "sessionId": session_id,
        # 会话 UID（Session.uid），供 CLI 侧插件（如 ai）按会话续聊使用
        "uid": session.uid if session else None,
        "outputStream": output,
        "outputOffset": output_offset
        if output_offset is not None
        else (session.output_offset if session else 0),
        "triggerReturnReason": mapped_reason,
    }

    program: dict = {
        "rawStartCommand": session.command if session else None,
        "startTime": format_iso_ms(session.start_time)
        if session and session.start_time
        else None,
        "nowTime": format_iso_ms(time.time()),
        "running": running,
        "ptyType": pty_type,
    }
    if exit_code is not None:
        program["exitCode"] = exit_code
    if error_message is not None:
        program["errorMessage"] = error_message
    result["program"] = program

    # 会话当前挂载插件（name/version）—— 与 consume_events 解耦，任何响应都显示
    debug_info: dict = {}
    plugin_host = getattr(session, "plugin_host", None) if session else None
    plugin_info = plugin_host.snapshot_info() if plugin_host else None
    if plugin_info:
        debug_info["plugins"] = plugin_info

    # 返回时状态检查：插件 inspect_state 一次调用，附加 terminalState 字段
    terminal_state = plugin_host.inspect_state() if plugin_host else None
    if terminal_state:
        result["terminalState"] = terminal_state

    # 调试信息产出与事件消费解耦：默认沿用 consume_events（既有语义），
    # 调用方（如 read --debug-output）可显式 include_debug=True 在非等待
    # 路径也产出 debugInformation（事件消费仍由 consume_events 单独控制，
    # read 不应消费事件，只展示当前时刻快照）
    wants_debug = (
        include_debug if include_debug is not None else consume_events
    )
    if wants_debug:
        processes = session.processes if session else []
        process_tree = []
        if processes:
            for pid in processes:
                if pid == 0:
                    continue
                try:
                    from ...process import _get_process_path

                    path = _get_process_path(pid)
                except Exception:
                    path = f"PID {pid}"
                process_tree.append({"pid": pid, "path": path})
        events = session.consume_events() if session else None
        if events:
            events = [e for e in events if e.get("pid", 0) != 0]

        if process_tree:
            debug_info["processes"] = process_tree
        gui_windows = session.gui_windows if session else None
        if gui_windows:
            debug_info["guiWindows"] = gui_windows
        if events:
            debug_info["pendingEvents"] = events
            debug_info["hint"] = _EVENTS_HINT
        if t_start is not None:
            debug_info["elapsedMs"] = round((time.monotonic() - t_start) * 1000, 3)

    if debug_info:
        program["debugInformation"] = debug_info

    hint = build_hint(result_type, reason, running, has_trigger, exit_code)
    if warning:
        hint = (hint + " " + warning).strip() if hint else warning
    if not running and session and has_git_bash_style_path(session.command):
        hint = (hint + " " + GIT_BASH_PATH_HINT).strip() if hint else GIT_BASH_PATH_HINT
    result["hint"] = hint

    if session and session.client_config:
        result["sessionDefaults"] = session.client_config

    return result


def strip_if_needed(output: str, msg: dict) -> str:
    if not msg.get("keep_ansi"):
        return strip_ansi(output)
    return output


def apply_lines_grep(
    output: str, lines_param, grep, conn, column_param=None
) -> Optional[str]:
    if not lines_param and not grep and column_param is None:
        return output

    lines = output.splitlines()

    if lines_param is not None:
        if isinstance(lines_param, int):
            lines = lines[-lines_param:] if lines_param > 0 else []
        elif isinstance(lines_param, str) and ":" in lines_param:
            parts = lines_param.split(":", 1)
            try:
                start = int(parts[0]) if parts[0] else 1
                end = int(parts[1]) if parts[1] else len(lines)
                start = max(start, 1)
                lines = lines[start - 1 : end]
            except (ValueError, IndexError):
                Message.send(conn, Response.error(f"Invalid line range: {lines_param}"))
                return None
        else:
            try:
                n = int(lines_param)
                lines = lines[-n:] if n > 0 else []
            except ValueError:
                Message.send(
                    conn, Response.error(f"Invalid lines parameter: {lines_param}")
                )
                return None

    if grep:
        try:
            pat = re.compile(grep)
            lines = [l for l in lines if safe_regex_search(pat, l)]
        except re.error:
            Message.send(conn, Response.error(f"Invalid regex: {grep}"))
            return None

    # 与 filter_snapshot_lines 一致：取每行第 N 列（1-based 字符位）
    if column_param is not None:
        col_idx = column_param - 1
        lines = [
            line[col_idx] if 0 <= col_idx < len(line) else "" for line in lines
        ]

    return "\n".join(lines)


def apply_client_defaults(session, msg: dict):
    client_defaults = msg.get("client_defaults")
    if client_defaults and isinstance(client_defaults, dict):
        session.client_config.update(client_defaults)


def check_ended_session(manager: SessionManager, session_id: str) -> Optional[str]:
    hs = manager._history_store
    if not hs:
        return None
    tag = hs.get_session_tag(session_id)
    return tag if tag == "ended" else None


def get_session_cwd(ctx, conn, cwd_session: str) -> Optional[str]:
    """按 cwd_session 取会话 cwd（file 命令路径解析基准，不操作该会话）

    会话不存在或无 cwd 时向 conn 发送错误并返回 None，调用方直接 return。
    """
    if not cwd_session:
        Message.send(conn, Response.error("cwd_session is required"))
        return None
    elif ctx.manager is None:
        Message.send(conn, Response.error("session lookup unavailable"))
        return None
    session = ctx.manager.get_session(cwd_session)
    if session is None:
        Message.send(
            conn, Response.error("cwd_session: session not found: %s" % cwd_session)
        )
        return None
    cwd = session.cwd
    if not cwd:
        Message.send(
            conn, Response.error("cwd_session: session has no cwd: %s" % cwd_session)
        )
        return None
    _logger.info("session cwd: sid=%s cwd=%r", cwd_session, cwd)
    return cwd


def validate_request(conn, msg: dict, fields: list) -> bool:
    for name, max_len in fields:
        if not validate_field(msg.get(name), name, max_len, conn):
            return False
    return True


def validate_trigger_regex(trigger, conn) -> bool:
    """校验触发正则在提交前可编译；非法正则拒绝并返回 False

    仅拦截 re.compile 抛出的语法错误（用户输入错误），
    合法但存在 ReDoS 风险的正则由 TriggerMatcher 降级为子串匹配，不在此拒绝。
    """
    if trigger is None:
        return True
    try:
        re.compile(trigger)
    except re.error as e:
        Message.send(
            conn, Response.error(f"Invalid trigger regex: {trigger!r} ({e})")
        )
        return False
    return True


def get_detail(msg: dict) -> str:
    parts = []
    if msg.get("command"):
        cmd = str(msg["command"])
        parts.append(f"cmd={cmd[:60]!r}")
    if msg.get("trigger"):
        parts.append(f"trigger={msg['trigger']!r}")
    if msg.get("encoding"):
        parts.append(f"enc={msg['encoding']!r}")
    if msg.get("offset"):
        parts.append(f"offset={msg['offset']}")
    return ", ".join(parts) if parts else ""
