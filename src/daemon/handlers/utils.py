import re
import json
import gzip
import base64
import time
import logging
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from ...protocol.ansi import strip_ansi
from ...session.manager import SessionManager
from ...output import safe_regex_search
from ...config.common import (
    MAX_SESSION_ID_LEN,
    MAX_COMMAND_LEN,
    MAX_PATTERN_LEN,
    MAX_INPUT_LEN,
    GZIP_COMPRESS_LEVEL,
)
from ...auth.base import Authenticator

_logger = logging.getLogger("pty-daemon")

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
    "Only unconsumed events are shown. "
    "Use -l <N> to view the full event history."
)

_SESSION_ENDED_HINT = "The session has ended. You are now viewing the data that was produced earlier."

_GIT_BASH_PATH_HINT = "非Git-Bash请不要使用Git-Bash风格路径(如 /c/Users/...)，请使用 Windows 风格路径(如 C:/Users/...)"

_GIT_BASH_PATH_RE = re.compile(r"(?:^|\s|[=\"'])/[a-zA-Z]/")


def compress_screen_buffer(buf: dict) -> str:
    raw = json.dumps(buf, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=GZIP_COMPRESS_LEVEL)
    return base64.b64encode(compressed).decode("ascii")


def has_git_bash_style_path(command) -> bool:
    if isinstance(command, list):
        command = " ".join(command)
    if not isinstance(command, str):
        return False
    return bool(_GIT_BASH_PATH_RE.search(command))


def map_reason(reason: str, exit_code=None) -> str:
    if reason == "ended" and exit_code is not None and exit_code != 0:
        return "program_crashed"
    return _REASON_MAP.get(reason, reason)


def filter_snapshot_lines(output: str, lines_param, column_param=None) -> str:
    if not output:
        return output
    if lines_param is not None:
        snap_lines = output.splitlines()
        if isinstance(lines_param, int):
            if 1 <= lines_param <= len(snap_lines):
                snap_lines = [snap_lines[lines_param - 1]]
            else:
                snap_lines = []
        elif isinstance(lines_param, str) and ":" in lines_param:
            parts = lines_param.split(":", 1)
            try:
                start = int(parts[0]) if parts[0] else 1
                end = int(parts[1]) if parts[1] else len(snap_lines)
                if start < 1:
                    start = 1
                snap_lines = snap_lines[start - 1:end]
            except (ValueError, IndexError):
                snap_lines = []
        else:
            try:
                n = int(lines_param)
                if 1 <= n <= len(snap_lines):
                    snap_lines = [snap_lines[n - 1]]
                else:
                    snap_lines = []
            except ValueError:
                snap_lines = []
        output = "\n".join(snap_lines)
    if column_param is not None and output:
        snap_lines = output.splitlines()
        col_idx = column_param - 1
        filtered = []
        for line in snap_lines:
            if 0 <= col_idx < len(line):
                filtered.append(line[col_idx])
            else:
                filtered.append("")
        output = "\n".join(filtered)
    return output


def build_hint(command_type: str, reason: str, session_running: bool,
               has_trigger: bool, exit_code=None) -> str:
    is_exec = command_type == "exec"
    is_send = command_type == "send"
    is_read = command_type == "read"

    if is_read:
        if not session_running:
            return _SESSION_ENDED_HINT
        return ""

    prefix = "The program started successfully" if is_exec else "Input sent successfully"

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

    return ""


def validate_field(value, name: str, max_len: int, conn) -> bool:
    if isinstance(value, str) and len(value) > max_len:
        Message.send(conn, Response.error(f"Parameter '{name}' too long (max {max_len} chars)"))
        return False
    return True


def format_iso_ms(timestamp: float) -> str:
    from datetime import datetime
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"


def attach_screen_buffer(result: dict, session, msg: dict):
    if not msg.get("include_screen_buffer"):
        return
    screen_buffer = session.export_screen_buffer()
    if not screen_buffer:
        return
    compressed = compress_screen_buffer(screen_buffer)
    result["screenBufferZ"] = compressed
    result["screenBufferMeta"] = {
        "cols": screen_buffer.get("cols", 0),
        "rows": screen_buffer.get("rows", 0),
        "sparse": True,
        "compressed": True,
    }


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
) -> dict:
    if session is None:
        session = manager.get_session(session_id)
    pty_type = session.pty_type if session else "none"
    running = session.running if session else False
    exit_code = session.exit_code if session else None
    error_message = session.error_message if session else None

    if exit_code == 0 and session:
        crash_events = [
            e for e in session.get_all_events()
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
        # 会话 UID（Session.uid），供客户端 AI 分析按 uid 续聊（aichat --session <uid>）
        "uid": session.uid if session else None,
        "outputStream": output,
        "outputOffset": session.output_offset if session else 0,
        "triggerReturnReason": mapped_reason,
    }

    program: dict = {
        "rawStartCommand": session.command if session else None,
        "startTime": format_iso_ms(session.start_time) if session and session.start_time else None,
        "nowTime": format_iso_ms(time.time()),
        "running": running,
        "ptyType": pty_type,
    }
    if exit_code is not None:
        program["exitCode"] = exit_code
    if error_message is not None:
        program["errorMessage"] = error_message
    result["program"] = program

    if consume_events:
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

        debug_info: dict = {}
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
        hint = (hint + " " + _GIT_BASH_PATH_HINT).strip() if hint else _GIT_BASH_PATH_HINT
    result["hint"] = hint

    if session and session.client_config:
        result["sessionDefaults"] = session.client_config

    return result


def strip_if_needed(output: str, msg: dict) -> str:
    if not msg.get("keep_ansi"):
        return strip_ansi(output)
    return output


def apply_lines_grep(output: str, lines_param, grep, conn) -> Optional[str]:
    if not lines_param and not grep:
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
                if start < 1:
                    start = 1
                lines = lines[start - 1:end]
            except (ValueError, IndexError):
                Message.send(conn, Response.error(f"Invalid line range: {lines_param}"))
                return None
        else:
            try:
                n = int(lines_param)
                lines = lines[-n:] if n > 0 else []
            except ValueError:
                Message.send(conn, Response.error(f"Invalid lines parameter: {lines_param}"))
                return None

    if grep:
        try:
            pat = re.compile(grep)
            lines = [l for l in lines if safe_regex_search(pat, l)]
        except re.error:
            Message.send(conn, Response.error(f"Invalid regex: {grep}"))
            return None

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


def validate_request(conn, msg: dict, fields: list) -> bool:
    for name, max_len in fields:
        if not validate_field(msg.get(name), name, max_len, conn):
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
