"""响应格式化输出

提供守护进程响应的格式化与打印功能。
适配 v2 规范化 result 格式（trigger/program/debug 三层）。

支持双模式输出：
  - **JSON 模式**（默认）：输出单行 JSON 到 stdout，便于 AI agent 解析。
  - **自然语言模式**：对 stdout（输出内容）和 stderr（元数据/调试信息）做人类可读格式化。
"""

import json
import logging
import sys
import time

from .input import safe_print

_logger = logging.getLogger("pty-client")

# ── 全局标志 ──

# 颜色模式：True=元数据输出到 stderr（终端可着色），False=输出到 stdout（纯文本）
_USE_COLOR = False
# JSON 模式：True=输出 JSON 到 stdout，False=自然语言格式化（默认 True）
_USE_JSON = True
# Debug 输出：True=输出 debug 段（进程树/GUI 窗口/事件），False=隐藏
_SHOW_DEBUG = True


def set_color_mode(enabled: bool):
    """设置颜色模式

    Args:
        enabled: True 启用颜色（stderr），False 禁用（stdout）。
    """
    global _USE_COLOR
    _USE_COLOR = enabled


def set_output_mode(json_mode: bool):
    """设置输出模式

    Args:
        json_mode: True=JSON 模式，False=自然语言模式。
    """
    global _USE_JSON
    _USE_JSON = json_mode


def is_json_mode() -> bool:
    """查询当前是否为 JSON 输出模式"""
    return _USE_JSON


def set_debug_mode(enabled: bool):
    """设置 debug 输出模式

    Args:
        enabled: True=输出 debug 段，False=隐藏。
    """
    global _SHOW_DEBUG
    _SHOW_DEBUG = enabled


def _meta_file():
    """返回元数据输出流

    仅在自然语言模式下区分 stderr/stdout。
    JSON 模式下统一用 stdout（JSON 本身不区分）。

    Returns:
        stderr（颜色模式）或 stdout（普通模式）。
    """
    return sys.stderr if _USE_COLOR else sys.stdout


def _format_event(ev: dict) -> str:
    """格式化单个事件为显示行

    Args:
        ev: 事件字典（time/type/pid/info/hwnd）。

    Returns:
        格式化后的字符串（含标记）。
    """
    ev_time = ev.get("time", "")
    if isinstance(ev_time, (int, float)):
        ev_t = time.strftime("%H:%M:%S", time.localtime(ev_time))
    else:
        ev_t = str(ev_time)[-12:] if len(str(ev_time)) > 12 else str(ev_time)
    ev_type = ev.get("type", "?")
    ev_info = ev.get("info", "")
    if ev_type == "process_crash":
        return f"# [!!] [{ev_t}] process crashed!\n#    {ev_info}"
    elif ev_type == "gui_window":
        return f"# [W]  [{ev_t}] GUI window: {ev_info}"
    elif ev_type == "process_spawn":
        return f"# [+]  [{ev_t}] {ev_info}"
    elif ev_type == "process_exit":
        info = ev_info or f"PID {ev.get('pid', 0)} exited"
        return f"# [-]  [{ev_t}] {info}"
    return f"# [?]  [{ev_t}] {ev_type}: {ev_info}"


def print_response(resp: dict):
    """打印守护进程响应

    根据全局 _USE_JSON 标志选择输出模式：
      - JSON 模式：直接 json.dump 到 stdout。
      - 自然语言模式：根据响应类型格式化输出到 stdout/stderr。

    Args:
        resp: 守护进程返回的响应字典。
    """
    resp_type = resp.get("type", "?") if resp else "None"
    _logger.debug("print_response: type=%s", resp_type)
    if resp is None:
        if _USE_JSON:
            safe_print(json.dumps({"type": "error", "error": "daemon not responding"},
                                  ensure_ascii=False))
        else:
            print("error: daemon not responding", file=sys.stderr)
        return

    if _USE_JSON:
        if not _SHOW_DEBUG:
            resp = {k: v for k, v in resp.items() if k != "debug"}
        safe_print(json.dumps(resp, ensure_ascii=False))
        return

    resp_type = resp.get("type", "")

    if resp_type == "error":
        error_msg = resp.get("error", "unknown error")
        print(f"error: {error_msg}", file=sys.stderr)
        return

    if resp_type in ("result", "exec", "send", "read"):
        _print_result(resp)
        return

    if resp_type == "ok":
        _print_ok(resp)
        return

    if resp_type == "triggered":
        output = resp.get("output", "")
        matched = resp.get("matched", False)
        if output:
            output = output.rstrip("\r\n")
            safe_print(output)
        if not matched:
            safe_print("\n[trigger not matched]", file=_meta_file())
        return

    safe_print(f"response: {json.dumps(resp, ensure_ascii=False)}", file=_meta_file())


# ── 原因标签映射 ──
_REASON_LABELS = {
    "matched":      "matched",
    "timeout":      "timeout",
    "ended":        "ended",
    "gui_detected": "gui detected",
    "crashed":      "crashed",
    "ok":           "ok",
}


def _print_result(resp: dict):
    """打印 result 类型响应 (v4)

    格式:
    {
        "output": "...",
        "trigger_matched": bool,
        "reason": str,
        "program": {"running": bool, "pty_type": str, ...},
        "debug": {"processes": [{pid, path}, ...], "gui_windows": [...]}
    }
    """
    output = resp.get("output", "")
    trigger = resp.get("trigger", {})
    program = resp.get("program", {})
    debug = resp.get("debug", {})
    session_id = resp.get("session_id")
    mf = _meta_file  # 短引用

    # ── 终端输出 ──
    if output:
        output = output.rstrip("\r\n")
        safe_print(output)

    # ── trigger info ──
    trigger_parts = []
    matched = resp.get("trigger_matched", False)
    reason = resp.get("reason", "ok")
    if matched:
        trigger_parts.append("matched")
    else:
        label = _REASON_LABELS.get(reason, reason)
        if label:
            trigger_parts.append(label)

    # program status
    running = program.get("running", False)
    exit_code = program.get("exit_code")
    error_message = program.get("error_message")
    status_parts = []
    if not running:
        status = "ended"
        if exit_code is not None:
            status += f" (exit={exit_code})"
        status_parts.append(status)
    else:
        status_parts.append("running")

    # first line: trigger + status
    first_line = " | ".join(trigger_parts + status_parts)
    if first_line or session_id:
        safe_print(f"\n# ── session ────────────────────────", file=mf())
        warning = resp.get("warning")
        if warning:
            safe_print(f"# ⚠ {warning}", file=mf())
        if first_line:
            safe_print(f"# {first_line}", file=mf())
        if session_id:
            safe_print(f"# session id: {session_id}", file=mf())
        pty_type = program.get("pty_type")
        if pty_type:
            safe_print(f"# pty type: {pty_type}", file=mf())
        # 程序名
        command = program.get("command")
        if command:
            cmd_str = command if isinstance(command, str) else " ".join(command)
            safe_print(f"# program: {cmd_str}", file=mf())
        # 启动时间
        start_time = program.get("start_time")
        if start_time:
            if isinstance(start_time, (int, float)):
                st = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time))
            else:
                st = str(start_time)
            safe_print(f"# started at: {st}", file=mf())
        # 当前时间
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        safe_print(f"# current time: {now_str}", file=mf())

    # ── debug ──
    processes = debug.get("processes") if _SHOW_DEBUG else None
    gui_windows = debug.get("gui_windows") if _SHOW_DEBUG else None

    has_debug = processes or gui_windows
    if has_debug:
        safe_print(f"\n# ── debug ────────────────────────", file=mf())

        # process tree（含名称）
        if processes:
            proc_strs = []
            for p in processes:
                if isinstance(p, dict):
                    pid = p.get("pid", 0)
                    path = p.get("path", "") or ""
                    if path and f"PID {pid}" not in path:
                        proc_strs.append(f"PID {pid} ({path})")
                    else:
                        proc_strs.append(f"PID {pid}")
                else:
                    pid = int(p) if not isinstance(p, int) else p
                    proc_strs.append(f"PID {pid}")
            pid_str = ", ".join(proc_strs)
            safe_print(f"# process tree: {pid_str}", file=mf())

        # GUI windows
        if gui_windows:
            for w in gui_windows:
                hwnd = w.get("hwnd", 0)
                pid = w.get("pid", 0)
                title = w.get("title", "")
                cls = w.get("class_name", "")
                safe_print(
                    f"# window: [0x{hwnd:08X}] PID={pid} \"{title}\" ({cls})",
                    file=mf(),
                )

    # ── pending events ──
    pending_events = debug.get("pending_events") if _SHOW_DEBUG else None
    if pending_events:
        has_crash = any(ev.get("type") == "process_crash" for ev in pending_events)
        if has_crash:
            safe_print(f"\n# ════════════ process crashes ════════════", file=mf())
        else:
            safe_print(f"\n# ── events ({len(pending_events)}) ────────", file=mf())
        for ev in pending_events:
            safe_print(_format_event(ev), file=mf())

    # ── error message ──
    if error_message:
        safe_print(f"\n# ── error ────────────────────────", file=mf())
        for line in error_message.split("\n"):
            safe_print(f"# {line}", file=mf())

    # ── offset ──
    output_offset = resp.get("output_offset")
    if output_offset is not None and output_offset > 0:
        safe_print(f"\n# ── offset ────────────────────────", file=mf())
        safe_print(f"# {output_offset}", file=mf())


def _print_ok(resp: dict):
    """print ok response"""
    mf = _meta_file

    # ── session list (list command) ──
    sessions = resp.get("sessions")
    if sessions is not None:
        if not sessions:
            safe_print("# no active sessions", file=mf())
        else:
            safe_print(f"\n# ── sessions ({len(sessions)}) ──────", file=mf())
            for s in sessions:
                sid = s.get("id", "?")
                cmd = s.get("command", "?")
                running = s.get("running", False)
                state = "running" if running else "ended"
                ev_count = s.get("pending_events", 0)
                ev_str = f", pending {ev_count}" if ev_count else ""
                safe_print(f"#   [{sid}] {cmd}  [{state}{ev_str}]", file=mf())
        return

    # pending events
    pending_events = resp.get("pending_events")
    if pending_events:
        warning = resp.get("warning")
        if warning:
            safe_print(f"# ⚠ {warning}", file=mf())
        has_crash = any(ev.get("type") == "process_crash" for ev in pending_events)
        if has_crash:
            safe_print(f"# ════════════ process crashes ════════════", file=mf())
        else:
            safe_print(f"# ── events ({len(pending_events)}) ────────", file=mf())
        for ev in pending_events:
            safe_print(_format_event(ev), file=mf())

    output = resp.get("output", "")
    if output:
        output = output.rstrip("\r\n")
        safe_print(output)

    note = resp.get("note", "")
    if note:
        safe_print(note, file=mf())

    closed = resp.get("closed")
    hwnd = resp.get("hwnd")
    if closed is not None and hwnd is not None:
        safe_print(f"window 0x{hwnd:08X} closed" if closed else
                    f"window 0x{hwnd:08X} close failed", file=mf())
