"""CLI 呈现层 Presenter — 把类型化 Result 渲染到 stdout/stderr

设计：
- 内容（程序输出/表格主体/配置/原始文本）→ stdout
- 元信息（状态行/原因/hint/调试/错误）→ stderr
- 错误 → stderr + 返回非 0（调用方决定进程退出码）
- ``--debug-output`` 控制元信息详略（含 debugInformation）
"""

import sys
from typing import Optional

from .input import safe_print
from .result import (
    CloseWinResult,
    ErrorResult,
    EventsResult,
    FileResult,
    KillResult,
    ListResult,
    MessageResult,
    PluginResult,
    Result,
    SessionResult,
    StatusResult,
    StopResult,
    WorkflowResult,
)

_SHOW_DEBUG = False
_render_hook = None
_error_seen = False

# 触发原因 → 中文短标签（stderr 状态行用）
_REASON_TAG = {
    "matched": "matched",
    "ok": "ok",
    "timeout": "timeout",
    "idle_timeout": "idle",
    "trigger_matched": "matched",
    "trigger_timeout": "timeout",
    "program_ended": "ended",
    "program_crashed": "crashed",
    "gui_detected": "gui",
    "ended": "ended",
    "crashed": "crashed",
    "cancelled": "cancelled",
}


def set_debug_mode(enabled: bool) -> None:
    global _SHOW_DEBUG
    _SHOW_DEBUG = enabled


def set_render_hook(fn) -> None:
    """注册 CLI 插件渲染钩子（render_response 链入口）

    插件返回非 None 文本则整体输出，否则走类型化渲染。
    """
    global _render_hook
    _render_hook = fn


def error_seen() -> bool:
    """本次 CLI 进程是否渲染过错误（供 main 提升为退出码 1）"""
    return _error_seen


def error_was_printed() -> bool:
    """兼容别名"""
    return _error_seen


def emit(text: str, msg_type: str = "info") -> None:
    """输出一条客户端本地消息（info→stderr，config/svg/raw→stdout）"""
    present(MessageResult(msg_type=msg_type, text=text))


def emit_error(message: str) -> None:
    """输出一条客户端本地错误（stderr + 记录 error 标志）"""
    present(ErrorResult(message=message))


def present(result: Result, out=None, err=None) -> bool:
    """渲染 Result；返回 result.ok（调用方据此设退出码）"""
    global _error_seen
    out = out or sys.stdout
    err = err or sys.stderr
    if not result.ok:
        _error_seen = True
    # CLI 插件渲染钩子：插件可整体覆盖输出
    if _render_hook is not None:
        try:
            text = _render_hook(result.raw)
        except Exception:
            from ..logging import get_logger

            get_logger("pty-client").exception("render hook 异常，回退类型化渲染")
            text = None
        if text is not None:
            safe_print(text, file=out, end="")
            return result.ok
    if _render_message(result, out, err):
        return result.ok
    return _render_typed(result, out, err)


def _render_message(result: Result, out, err) -> bool:
    """通用 Message 结果由本分支处理则返回 True"""
    if isinstance(result, MessageResult):
        if result.msg_type in ("info", "warning"):
            _write(err, result.text)
        else:  # config / svg / raw
            _write(out, result.text)
        return True
    return False


def _render_typed(result: Result, out, err) -> bool:
    if isinstance(result, ErrorResult):
        _write(err, f"error: {result.message}")
    elif isinstance(result, SessionResult):
        _render_session(result, out, err)
    elif isinstance(result, ListResult):
        _render_list(result, out, err)
    elif isinstance(result, StatusResult):
        _render_status(result, out)
    elif isinstance(result, EventsResult):
        _render_events(result, out, err)
    elif isinstance(result, (KillResult, StopResult, CloseWinResult)):
        _render_single(result, out)
    elif isinstance(result, PluginResult):
        _render_plugin(result, out, err)
    elif isinstance(result, FileResult):
        _render_file(result, out, err)
    elif isinstance(result, WorkflowResult):
        _render_workflow(result, out)
    else:
        # 兜底：不原样 dump，只走 fallback 文本
        _write(err, f"(unhandled result: {result.kind})")
    return True


def _render_session(r: SessionResult, out, err) -> None:
    # 内容 → stdout
    if r.output:
        _write(out, r.output, end="\n" if r.output.rstrip() else "")
    if r.stderr:
        _write(err, r.stderr)
    # 元信息 → stderr
    if _SHOW_DEBUG and r.meta.get("debugInformation"):
        _write(
            err,
            "[debug] " + _compact(r.meta["debugInformation"]),
        )
    tag = _REASON_TAG.get(r.reason, r.reason)
    state = "running" if r.running else "ended"
    pty = r.program.get("ptyType", "")
    _write(err, f"[{r.command_type} · {tag}]  {r.session_id or '-'}  {state}  {pty}".strip())
    if r.hint:
        _write(err, r.hint)
    if r.terminal_state:
        _write(err, f"state: {r.terminal_state.get('state', '')}")

    if _SHOW_DEBUG and r.output_offset:
        _write(err, f"offset: {r.output_offset}")


def _render_list(r: ListResult, out, err) -> None:
    if not r.sessions:
        _write(err, r.hint or "no active session")
        return
    _write(out, _table(
        ("ID", "COMMAND", "UID", "STATE"),
        [
            (
                s.get("id", ""),
                _trunc(s.get("rawStartCommand") or s.get("command") or "", 24),
                _trunc(s.get("uid", "") or "", 12),
                ("ramp" if s.get("running") else ("ended" if s.get("ended") else "ended")),
            )
            for s in r.sessions
        ],
    ))
    if r.hint:
        _write(err, r.hint)


def _render_status(r: StatusResult, out) -> None:
    rows = [
        ("running", "yes" if r.running else "no"),
    ]
    if r.pid is not None:
        rows.append(("pid", str(r.pid)))
    if r.port is not None:
        rows.append(("port", str(r.port)))
    if r.uptime is not None:
        rows.append(("uptime", f"{r.uptime}s"))
    rows.append(("sessions", f"{r.active_sessions} active / {r.ended_sessions} ended"))
    if r.web_url:
        rows.append(("web", r.web_url))
    _write(out, _table(("key", "value"), rows))


def _render_events(r: EventsResult, out, err) -> None:
    for e in r.events:
        t = e.get("time", "")
        etype = e.get("type", "")
        pid = e.get("pid", "")
        detail = e.get("detail", {}) or {}
        parts = [t, etype, f"pid={pid}"]
        if etype == "process_crash" and detail.get("exitCode") is not None:
            parts.append(f"code={detail['exitCode']}")
        _write(out, "  ".join(parts))
    if r.hint:
        _write(err, r.hint)


def _render_single(r: Result, out) -> None:
    if isinstance(r, KillResult):
        _write(out, r.msg or ("ok" if r.ok else "failed"))
    elif isinstance(r, StopResult):
        _write(out, r.msg or ("ok" if r.ok else "failed"))
    elif isinstance(r, CloseWinResult):
        _write(out, ("closed" if r.closed else ("failed" + (f": {r.message}" if r.message else ""))))


def _render_plugin(r: PluginResult, out, err) -> None:
    if r.plugins:
        _write(out, _table(("NAME", "VERSION"), [(p.get("name", ""), str(p.get("version", ""))) for p in r.plugins]))
    elif r.message:
        _write(err, r.message)
    else:
        _write(out, "ok")


def _render_file(r: FileResult, out, err) -> None:
    if r.body:
        _write(out, r.body)
    if r.summary:
        _write(err, r.summary)


def _render_workflow(r: WorkflowResult, out) -> None:
    _write(out, _compact(r.data))


def _write(stream, text: str, end: str = "\n") -> None:
    if not text:
        return
    safe_print(text, file=stream, end=end)


def _trunc(text: str, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 3] + "..."


def _compact(obj) -> str:
    if isinstance(obj, dict):
        return ", ".join(f"{k}={_compact(v)}" for k, v in obj.items() if v is not None)
    if isinstance(obj, (list, tuple)):
        return ", ".join(_compact(x) for x in obj)
    return str(obj)


def _table(headers, rows) -> str:
    """对齐列宽的 ASCII 表格（简单而稳）"""
    cols = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(min(cols, len(row))):
            widths[i] = max(widths[i], len(str(row[i])))
    lines = []
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(line.rstrip())
    lines.append("  ".join("-" * widths[i] for i in range(cols)))
    for row in rows:
        lines.append("  ".join(str(row[i]).ljust(widths[i]) for i in range(min(cols, len(row)))).rstrip())
    return "\n".join(lines)