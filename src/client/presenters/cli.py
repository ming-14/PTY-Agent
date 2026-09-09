"""CLI 呈现层 — 自然语言响应格式化

把守护进程返回的 result/ok/error 响应格式化为人类可读文本输出。
与传输/业务解耦（client.api 返回 dict，本模块只负责"展示"）。

输出为**自然语言模式**：程序输出到 stdout，元数据/调试信息到 stdout。
"""

import logging
import sys
import time
from datetime import datetime

from ..input import safe_print

_logger = logging.getLogger("pty-client")


class CliPresenter:
    """CLI 呈现器（自然语言模式）

    Args:
        show_debug: True 显示 debug 段（进程树/GUI 窗口/事件）。
    """

    def __init__(self, show_debug: bool = True):
        self._show_debug = show_debug

    def present(self, resp: dict) -> None:
        """呈现一个响应 dict（副作用：打印）"""
        print_response(resp, show_debug=self._show_debug)


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
    elif isinstance(ev_time, str):
        try:
            # ISO 8601 字符串（如 "2026-06-22T14:32:15.12"）→ 仅显示时间
            ev_t = datetime.fromisoformat(ev_time).strftime("%H:%M:%S")
        except ValueError:
            ev_t = ev_time
    else:
        ev_t = str(ev_time)
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


def print_response(resp: dict, *, show_debug: bool = True):
    """打印守护进程响应（自然语言模式）

    Args:
        resp:       守护进程返回的响应字典。
        show_debug: 是否输出 debug 段（进程树/GUI 窗口/事件）。
    """
    resp_type = resp.get("type", "?") if resp else "None"
    _logger.debug("print_response: type=%s", resp_type)
    if resp is None:
        print("error: daemon not responding", file=sys.stderr)
        return

    resp_type = resp.get("type", "")

    if resp_type == "error":
        error_msg = resp.get("error", "unknown error")
        print(f"error: {error_msg}", file=sys.stderr)
        return

    if resp_type in ("result", "exec", "send", "read"):
        _print_result(resp, show_debug=show_debug)
        return

    if resp_type == "ok":
        _print_ok(resp, show_debug=show_debug)
        return

    safe_print(f"response: {resp}")


# ── 原因标签映射 ──
_REASON_LABELS = {
    "matched":      "matched",
    "timeout":      "timeout",
    "ended":        "ended",
    "gui_detected": "gui detected",
    "crashed":      "crashed",
    "ok":           "ok",
}


def _format_gui_window_lines(gui_windows) -> list:
    """GUI 窗口列表 → 呈现行列表（session 段正载荷与 debug 段共用）"""
    lines = []
    for w in gui_windows:
        hwnd = w.get("hwnd", 0)
        pid = w.get("pid", 0)
        title = w.get("title", "")
        cls = w.get("class_name", "")
        lines.append(
            f"# window: [0x{hwnd:08X}] PID={pid} \"{title}\" ({cls})",
        )
    return lines


def _print_result(resp: dict, *, show_debug: bool = True):
    """打印 result 类型响应

    格式:
    {
        "output": "...",
        "trigger_matched": bool,
        "reason": str,
        "program": {"mode": str, "running": bool, "pty_type": str, ...},
        "debug": {"processes": [{pid, path}, ...], "gui_windows": [...]}
    }
    """
    output = resp.get("output", "")
    program = resp.get("program", {})
    debug = resp.get("debug", {})
    session_id = resp.get("session_id")

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
        safe_print("\n# ── session ────────────────────────")
        warning = resp.get("warning")
        if warning:
            safe_print(f"# ⚠ {warning}")
        if first_line:
            safe_print(f"# {first_line}")
        if session_id:
            safe_print(f"# session id: {session_id}")
        mode = program.get("mode")
        if mode:
            safe_print(f"# mode: {mode}")
        pty_type = program.get("pty_type")
        if pty_type:
            safe_print(f"# pty type: {pty_type}")
        # 程序名
        command = program.get("command")
        if command:
            cmd_str = command if isinstance(command, str) else " ".join(command)
            safe_print(f"# program: {cmd_str}")
        # 启动时间
        start_time = program.get("start_time")
        if start_time:
            if isinstance(start_time, (int, float)):
                st = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time))
            else:
                st = str(start_time)
            safe_print(f"# started at: {st}")
        # 当前时间
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        safe_print(f"# current time: {now_str}")

    # ── debug ──
    processes = debug.get("processes") if show_debug else None
    all_windows = debug.get("gui_windows") or []

    # GUI 是与 `-t` 平级的返回条件：本轮返回原因就是它时，窗口信息属于
    # 正载荷而非调试附件 —— 即使 --no-debug 也必须给出（closewin 依赖 hwnd）。
    if all_windows and not show_debug and reason == "gui_detected":
        for line in _format_gui_window_lines(all_windows):
            safe_print(line)
        all_windows = []          # 已作为正载荷呈现，不在 debug 段重复
    gui_windows = all_windows if show_debug else None

    has_debug = processes or gui_windows
    if has_debug:
        safe_print("\n# ── debug ────────────────────────")

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
            safe_print(f"# process tree: {pid_str}")

        # GUI windows
        if gui_windows:
            for line in _format_gui_window_lines(gui_windows):
                safe_print(line)

    # ── pending events（exec/send 返回的 debug.pending_events）──
    pending_events = debug.get("pending_events") if show_debug else None
    if pending_events:
        has_crash = any(ev.get("type") == "process_crash" for ev in pending_events)
        if has_crash:
            safe_print("\n# ════════════ process crashes ════════════")
        else:
            safe_print(f"\n# ── events ({len(pending_events)}) ────────")
        for ev in pending_events:
            safe_print(_format_event(ev))

    # ── error message ──
    if error_message:
        safe_print("\n# ── error ────────────────────────")
        for line in error_message.split("\n"):
            safe_print(f"# {line}")


def _print_ok(resp: dict, *, show_debug: bool = True):
    """打印 ok 类型响应"""

    # ── session list (list command) ──
    sessions = resp.get("sessions")
    if sessions is not None:
        if not sessions:
            safe_print("# no active sessions")
        else:
            safe_print(f"\n# ── sessions ({len(sessions)}) ──────")
            for s in sessions:
                sid = s.get("id", "?")
                cmd = s.get("command", "?")
                running = s.get("running", False)
                state = "running" if running else "ended"
                ev_count = s.get("pending_events", 0)
                ev_str = f", pending {ev_count}" if ev_count else ""
                safe_print(f"#   [{sid}] {cmd}  [{state}{ev_str}]")
        return

    output = resp.get("output", "")
    if output:
        output = output.rstrip("\r\n")
        safe_print(output)

    note = resp.get("note", "")
    if note:
        safe_print(note)

    closed = resp.get("closed")
    hwnd = resp.get("hwnd")
    if closed is not None and hwnd is not None:
        safe_print(f"window 0x{hwnd:08X} closed" if closed else
                    f"window 0x{hwnd:08X} close failed")