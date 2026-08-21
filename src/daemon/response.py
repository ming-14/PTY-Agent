import base64
import gzip
import json
import re
import time
from typing import Optional

from ..config.common import GZIP_COMPRESS_LEVEL
from ..protocol.reasons import OUTWARD_REASON, Reason
from ..session.manager import SessionManager
from ..logging import get_logger

_logger = get_logger("pty-daemon")

_EVENTS_NO_ARGS_HINT = (
    "Only unconsumed events are shown. Use -l <N> to view the full event history."
)

_SESSION_ENDED_HINT = (
    "The session has ended. You are now viewing the data that was produced earlier."
)

# Git-Bash 风格路径提示（exec 响应路径提示用，属核心呈现）
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


def map_reason(reason, exit_code=None, error_message=None) -> str:
    """原始 reason → 对外 triggerReturnReason

    崩溃判定以退出码/错误消息为权威依据：crash_event 信号会被 stop()
    无条件置位，等待循环可能据其误报 Reason.CRASHED；此处兜底保证
    exit_code==0 且无 error_message 的正常完成绝不映射为 program_crashed。
    插件自定义 reason（不在 OUTWARD_REASON）原样透传。
    """
    is_crash = (exit_code is not None and exit_code != 0) or bool(error_message)
    if reason in (Reason.CRASHED, Reason.ENDED):
        return Reason.PROGRAM_CRASHED if is_crash else Reason.PROGRAM_ENDED
    return OUTWARD_REASON.get(reason, reason)


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
                    from ..process import _format_exit_code_message

                    error_message = _format_exit_code_message(crash_ec)

    mapped_reason = map_reason(reason, exit_code, error_message)

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
        "mode": getattr(session, "mode", "pty") if session else "pty",
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
                    from ..process import _get_process_path

                    path = _get_process_path(pid)
                except Exception:
                    path = f"PID {pid}"
                process_tree.append({"pid": pid, "path": path})
        if include_debug is not None:
            # CLI 主动申请 debug：事件"不消费展示"（get_all_events 含历史，无副作用），
            # 让 debug 信息不受既有 consume 语义消耗而空缺
            events = session.get_all_events() if session else None
        else:
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

    # elapsedMs 是基础信息（命令处理耗时），不依赖 debug 开关
    if t_start is not None:
        debug_info["elapsedMs"] = round((time.monotonic() - t_start) * 1000, 3)

    if debug_info:
        program["debugInformation"] = debug_info

    # 返回原因文案（如 matched/timeout/crash）由呈现层 presenter 按 reason 数据重建；
    # daemon 只透传自身才有的补充警告（warning / Git-Bash 路径提示）
    hint = ""
    if warning:
        hint = warning
    if not running and session and has_git_bash_style_path(session.command):
        hint = (hint + " " + GIT_BASH_PATH_HINT).strip() if hint else GIT_BASH_PATH_HINT
    result["hint"] = hint

    if session and session.client_config:
        result["sessionDefaults"] = session.client_config

    return result


def describe_output_format(msg, is_subprocess: bool = False) -> str:
    """请求的输出/过滤格式标签（供 presenter 分隔线显示）

    按本请求实际采用的取源/过滤方式返回 ASCII 标签：
    snapshot(默认) / diff(增量) / full(全量) / tail:N(最后N行) / lines:A:B(行区间) /
    col:N(第N列) / match:<pattern>(grep 命中)。
    ``-l N``/``--lines N`` 表示"最后 N 行"，用 tail:N 避免误解成"第 N 行"。
    子进程模式默认增量输出（无快照），无其他过滤标注时标注 diff 而非 snapshot。
    """
    if msg.get("snapshot_diff"):
        return "diff"
    if msg.get("full"):
        return "full"
    lines = msg.get("lines")
    if lines is not None and lines != "":
        s = str(lines)
        return f"lines:{s}" if ":" in s else f"tail:{s}"
    col = msg.get("column")
    if col is not None:
        return f"col:{col}"
    if msg.get("grep"):
        return f"match:{msg['grep']}"
    if is_subprocess:
        return "diff"
    return "snapshot"