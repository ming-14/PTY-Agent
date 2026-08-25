"""CLI 呈现层 Presenter — 把类型化 Result 渲染到 stdout/stderr

设计：
- 内容（程序输出/表格主体/配置/原始文本）→ stdout
- 元信息（状态行/原因/hint/调试/错误）→ stderr
- 错误 → stderr + 返回非 0（调用方决定进程退出码）
- ``--debug-output`` 控制元信息详略（含 debugInformation）
"""

import shutil
import sys
import unicodedata
from typing import Optional

from .input import safe_print
from .msg import fmt_message
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
    WaitResult,
    WorkflowResult,
    from_response,
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
            # 信息/警告 → stderr，统一消息格式
            _write(err, fmt_message(result.text))
        else:  # config / svg / raw（内容类）→ stdout 原样
            _write(out, result.text)
        return True
    return False


def _render_typed(result: Result, out, err) -> bool:
    if isinstance(result, ErrorResult):
        _write(err, fmt_message(result.message))
    elif isinstance(result, SessionResult):
        _render_session(result, out, err)
    elif isinstance(result, ListResult):
        _render_list(result, out, err)
    elif isinstance(result, StatusResult):
        _render_status(result, out)
    elif isinstance(result, WaitResult):
        _render_wait(result, out)
    elif isinstance(result, EventsResult):
        _render_events(result, out, err)
    elif isinstance(result, (KillResult, StopResult, CloseWinResult)):
        _render_single(result, out, err)
    elif isinstance(result, PluginResult):
        _render_plugin(result, out, err)
    elif isinstance(result, FileResult):
        _render_file(result, out, err)
    elif isinstance(result, WorkflowResult):
        _render_workflow(result, out, err)
    else:
        # 兜底：不原样 dump，只走 fallback 文本
        _write(err, f"(unhandled result: {result.kind})")
    return True


def _render_session(r: SessionResult, out, err) -> None:
    # mouse 光标定位：结果即正文（无快照框、不套 hit 前缀），如 [cursor] col=5 row=6 line=...
    cursor = (r.raw or {}).get("cursor")
    if cursor:
        line = cursor.get("line")
        if line:
            _write(
                out,
                f"[cursor] col={cursor.get('col')} row={cursor.get('row')} "
                f"line={line!r}",
            )
        else:
            _write(out, f"[cursor] col={cursor.get('col')} row={cursor.get('row')}")
        _write(out, _status_line(r))
        return

    # GUI 检测返回：窗口信息是本次返回的实质内容（即使无终端输出也应展示）
    gui_windows = (r.program.get("debugInformation") or {}).get("guiWindows") or []

    # mouse 动作/查询：命中列表是结果正文（非 hit 前缀），未执行是消息（非 hit）
    is_mouse = r.command_type == "mouse"
    mouse_fail = _mouse_fail_message(r)

    # 会话级消息（崩溃等）→ stderr，位于内容/No output 之前
    if not is_mouse:
        session_msg = _session_message(r)
        if session_msg:
            _write(err, fmt_message(session_msg))

    # mouse grep 命中列表 → stdout（放分隔线前），格式 [grep "<pattern>"] row=.. col=..
    if is_mouse and r.matches:
        pattern = (r.raw or {}).get("grep") or ""
        for m in r.matches:
            line = _format_match(pattern, m)
            if line:
                _write(out, line)

    # mouse 动作未执行 → 消息（(PTY-Agent message: Operation not performed. ...) → stderr）
    if mouse_fail:
        # 先冲刷 stdout 缓冲，保证消息紧跟命中列表、在分隔线之前（管道捕获也不乱序）
        try:
            out.flush()
        except Exception:
            pass
        _write(err, fmt_message(mouse_fail))

    # 有实际内容（输出/GUI窗口）才画上下列分隔框，避免空快照框
    # （命中列表已先行输出，不计入框内容）
    has_content = bool(r.output or gui_windows)
    if has_content:
        _write(out, _separator(r.meta.get("format", "")))
    elif not (is_mouse and (r.matches or mouse_fail)):
        _write(out, fmt_message("No output."))
    # 内容 → stdout
    if r.output:
        _write(out, r.output, end="\n" if r.output.rstrip() else "")
    for w in gui_windows:
        _write(out, "  " + _fmt_gui_window(w))
    # 程序 stderr → stderr（不影响 stdout 内容顺序）
    if r.stderr:
        _write(err, r.stderr)
    if has_content:
        _write(out, _separator())
    # 状态行 → stdout 底部（与内容同流，顺序确定，不受 stderr 合并干扰）
    _write(out, _status_line(r))
    if r.terminal_state:
        _write(out, f"state: {r.terminal_state.get('state', '')}")
    # hint → stdout 末尾：daemon 仅透传补充警告（如 Git-Bash 路径、光标不可用），
    # 返回原因文案由本层按 reason 数据重建（见 _session_reason_hint）
    hint = r.hint
    reason_hint = _session_reason_hint(r) if not is_mouse else ""
    if reason_hint:
        hint = (" ".join(x for x in (hint, reason_hint) if x)).strip() if hint else reason_hint
    _append_hit(out, hint)
    # debug 信息 → stdout 末尾（--debug-output 时，workflow 风格分块）
    if _SHOW_DEBUG:
        _render_debug(r, out)


def _mouse_fail_message(r: SessionResult) -> str:
    """按 mouse 返回数据（performed/matches/action/message）重建"动作未执行"消息

    呈现收敛：daemon 不再产 "Mouse action performed successfully" 等装饰文案。
    grep 是查询动作，命中列表即结果，不视为操作失败（无匹配时给 "No match found."）。
    其余动作未执行 → ``Operation not performed. <message>``（走 (PTY-Agent message: ...)）。
    """
    if r.command_type != "mouse":
        return ""
    raw = r.raw or {}
    if raw.get("performed"):
        return ""
    if (raw.get("action") or "") == "grep":
        return "No match found." if not r.matches else ""
    message = raw.get("message") or raw.get("error")
    if message:
        return _ensure_sentence_end("Operation not performed. " + str(message))
    return "Operation not performed."


def _ensure_sentence_end(text: str) -> str:
    """消息文本统一以句号/感叹号/问号等句末标点收尾（用户可读文案规范）"""
    if text and text[-1] not in ".!?。！？":
        return text + "."
    return text


def _session_message(r: SessionResult) -> str:
    """会话级消息（崩溃等）：(PTY-Agent message: ...) → stderr，位于内容之前

    与 hint 区分：面向用户的实义信息（程序崩溃）走消息，附加说明走 hit。
    """
    reason = r.reason
    if reason in ("crashed", "program_crashed"):
        ec = r.program.get("exitCode")
        if ec is not None:
            return f"Program crashed with exit code: {ec}."
        return "Program crashed."
    return ""


def _session_reason_hint(r: SessionResult) -> str:
    """按会话返回数据（reason/running/exitCode）重建"为何返回"的提示文案

    呈现收敛：daemon 不再产此类装饰文案，统一由呈现层重建。
    崩溃消息走 _session_message（(PTY-Agent message:)），此处只留附加说明类 hint。
    """
    if r.command_type == "read" and not r.running:
        return "The session has ended. You are now viewing the data that was produced earlier."
    return ""


def _fmt_gui_window(w: dict) -> str:
    """格式化单个 GUI 窗口：``GUI pid=.. hwnd=0x.. title (class)``"""
    parts = ["GUI"]
    pid = w.get("pid")
    if pid:
        parts.append(f"pid={pid}")
    hwnd = w.get("hwnd")
    if hwnd is not None:
        parts.append(f"hwnd=0x{int(hwnd):X}" if isinstance(hwnd, int) else f"hwnd={hwnd}")
    title = w.get("title")
    if title:
        parts.append(title)
    cls = w.get("class_name")
    if cls:
        parts.append(f"({cls})")
    return "  ".join(parts)


def _status_line(r: SessionResult) -> str:
    """会话状态行：``[cmd · reason[(exit_code: N)] · 耗时]  session  state  mode``

    崩溃时在原因后附带退出码（如 ``crashed(exit_code: 7)``），便于区分非零退出的具体值。
    """
    tag = _REASON_TAG.get(r.reason, r.reason)
    if tag == "crashed":
        ec = r.program.get("exitCode")
        if ec is not None:
            tag = f"crashed(exit_code: {ec})"
    state = "running" if r.running else "ended"
    elapsed = _elapsed(r)
    core = f"[{r.command_type} · {tag}" + (f" · {elapsed}" if elapsed else "") + "]"
    line = f"{core}  {r.session_id or '-'}  {state}"
    mode = r.program.get("mode")
    if mode:
        line += f"  {mode}"
    return line.strip()


def _render_debug(r: SessionResult, out) -> None:
    """渲染 debugInformation：``[debug]`` 标题 + ``key: value`` 逐行 + 事件缩进行

    仅在 --debug-output 时调用；输出到 stdout 末尾（状态行之后），与内容同流保证顺序。
    字段：elapsedMs / processes / guiWindows / offset（逐行），pendingEvents（事件缩进行）。
    """
    di = r.program.get("debugInformation") or {}
    lines: list = []
    if di.get("elapsedMs") is not None:
        lines.append(f"elapsed: {di['elapsedMs']:.0f} ms")
    for p in di.get("processes") or []:
        if isinstance(p, dict):
            pid, path = p.get("pid"), p.get("path")
            proc = f"[{pid}]" if pid is not None else ""
            lines.append(f"process: {proc}".rstrip() if path else f"process: {p}")
            if path:
                lines[-1] = f"process: {proc} {path}".strip()
        else:
            lines.append(f"process: {p}")
    for g in di.get("guiWindows") or []:
        title = g.get("title") if isinstance(g, dict) else g
        lines.append(f"gui: {title}")
    if r.output_offset:
        lines.append(f"offset: {r.output_offset}")

    events = di.get("pendingEvents") or []
    hint = di.get("hint")
    if not (lines or events or hint):
        return  # 无内容则不输出

    out.write("\n")  # 状态行之后空一行，分隔 debug 块
    _write(out, "[debug]")
    for line in lines:
        _write(out, line)
    for e in events:
        _write(out, "  " + _fmt_debug_event(e))
    if hint:
        _append_hit(out, hint)


def _fmt_debug_event(e: dict) -> str:
    """单条事件：``time  type  pid=..  name/exitCode=..``"""
    parts = [str(e.get("time", "")), str(e.get("type", ""))]
    pid = e.get("pid")
    if pid:
        parts.append(f"pid={pid}")
    detail = e.get("detail") or {}
    if detail.get("name"):
        parts.append(str(detail["name"]))
    if detail.get("exitCode") is not None:
        parts.append(f"exitCode={detail['exitCode']}")
    return "  ".join(parts)


def _terminal_cols() -> int:
    """当前终端列数（stdout 非 TTY 时回退 80）"""
    return shutil.get_terminal_size((80, 24)).columns


def _char_display_w(c: str) -> int:
    """字符显示宽度（CJK 全宽字符按 2 格，与 wcwidth 语义一致）"""
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def _separator(label: str = "") -> str:
    """对齐分隔线：``── 标签 ──`` 总宽对齐当前终端列数，无标签为纯 ``─`` 行"""
    width = _terminal_cols()
    if label:
        inner = f" {label} "
        dashes = width - sum(_char_display_w(c) for c in inner)
        dashes = max(dashes, 0)
        left = dashes // 2
        return "─" * left + inner + "─" * (dashes - left)
    return "─" * width


def _elapsed(r: SessionResult) -> str:
    """命令处理耗时（请求接收至响应装配），基于 monotonic 时钟"""
    ms = (r.program.get("debugInformation") or {}).get("elapsedMs")
    if ms is not None:
        return f"{ms / 1000:.2f}s"
    return ""


def _append_hit(out, hint) -> None:
    """把附加提示/命中说明以 ``(hit: ...)`` 追加到 stdout 末尾（唯一出口）

    所有 hint（会话返回说明、events/list 表后附加、mouse 结果说明等）统一走本函数，
    禁止任何分支自行决定是否加前缀或改格式。
    """
    if hint:
        _write(out, f"(hit: {hint})")


def _format_match(pattern: str, m: dict) -> str:
    """格式化单个 grep 命中坐标：``[grep "<pattern>"] row=.. col=..``（结果正文，非 hit）"""
    start = m.get("start") or {}
    end = m.get("end") or {}
    row = start.get("row")
    col = start.get("col")
    end_col = end.get("col")
    if row is None and col is None:
        return ""
    parts = []
    if row is not None:
        parts.append(f"row={row}")
    if col is not None:
        parts.append(f"col={col}" + (f"..{end_col}" if end_col not in (None, col) else ""))
    return f'[grep "{pattern}"] ' + " ".join(parts)


def _render_list(r: ListResult, out, err) -> None:
    if not r.sessions:
        _write(err, fmt_message(r.hint or "No active session."))
        return
    _write(out, _table(
        ("ID", "COMMAND", "UID", "STATE"),
        [
            (
                s.get("id", ""),
                _trunc(s.get("rawStartCommand") or s.get("command") or "", 24),
                _trunc(s.get("uid", "") or "", 12),
                ("running" if s.get("running") else "ended"),
            )
            for s in r.sessions
        ],
    ))

    _append_hit(out, r.hint)


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


def _render_wait(r: WaitResult, out) -> None:
    """wait 结果：``[wait · ok · <耗时>] waited``（状态行风格）"""
    _write(out, f"[wait · ok · {r.elapsed:.2f}s] waited")


def _render_events(r: EventsResult, out, err) -> None:
    rows = []
    for e in r.events:
        t = e.get("time", "")
        etype = e.get("type", "")
        pid = e.get("pid", "")
        detail = e.get("detail", {}) or {}
        pid_col = str(pid) if pid != "" else ""
        if etype == "process_crash" and detail.get("exitCode") is not None:
            pid_col += f" (code={detail['exitCode']})"
        rows.append((t, etype, pid_col))
    if rows:
        _write(out, _table(("key", "events", "pid"), rows))
    _append_hit(out, r.hint)


def _render_single(r: Result, out, err) -> None:
    if isinstance(r, KillResult):
        msg = r.msg or ("ok" if r.ok else "failed")
    elif isinstance(r, StopResult):
        msg = r.msg or ("ok" if r.ok else "failed")
    elif isinstance(r, CloseWinResult):
        msg = "closed" if r.closed else ("failed" + (f": {r.message}" if r.message else ""))
    else:
        msg = "ok"
    _write(err, fmt_message(msg))


def _render_plugin(r: PluginResult, out, err) -> None:
    if r.plugins:
        # list：列出已加载插件（含状态/形态）
        if any("state" in p for p in r.plugins):
            _write(
                out,
                _table(
                    ("NAME", "VERSION", "STATE", "KIND"),
                    [
                        (
                            p.get("name", ""),
                            str(p.get("version", "")),
                            p.get("state", ""),
                            p.get("kind", ""),
                        )
                        for p in r.plugins
                    ],
                ),
            )
        else:
            # ls：仅列出会话挂载插件
            _write(
                out,
                _table(
                    ("NAME", "VERSION"),
                    [
                        (p.get("name", ""), str(p.get("version", "")))
                        for p in r.plugins
                    ],
                ),
            )
    elif r.result:
        # cmd 命令结果：渲染为 JSON（含状态/错误/详情）
        import json
        _write(out, json.dumps(r.result, ensure_ascii=False, indent=2))
    elif r.info:
        # info / status
        info = r.info
        for key in (
            "name", "version", "description", "kind", "state",
            "path", "error",
        ):
            val = info.get(key)
            if val not in (None, ""):
                _write(out, "%-14s %s" % (key + ":", val))
        if info.get("triggers"):
            _write(out, "triggers:     %s" % ", ".join(info["triggers"]))
        if info.get("messageTypes"):
            _write(out, "messageTypes: %s" % ", ".join(info["messageTypes"]))
        if info.get("permissions"):
            _write(out, "permissions:  %s" % ", ".join(info["permissions"]))
        if info.get("events"):
            _write(out, "events:       %s" % ", ".join(info["events"]))
        if info.get("autoLoad"):
            _write(out, "autoLoad:     %s" % info["autoLoad"])
        if info.get("pollInterval"):
            _write(out, "pollInterval: %s" % info["pollInterval"])
        if info.get("config"):
            import json
            _write(out, "config:\n%s" % json.dumps(info["config"], ensure_ascii=False, indent=2))
    elif r.config:
        import json
        _write(out, json.dumps(r.config, ensure_ascii=False, indent=2))
    elif r.message:
        _write(err, fmt_message(r.message))
    else:
        _write(err, fmt_message("ok"))


def _render_file(r: FileResult, out, err) -> None:
    if r.body:
        _write(out, r.body)
    if r.summary:
        _write(err, fmt_message(r.summary))


def _render_workflow(r: WorkflowResult, out, err) -> None:
    data = r.data or {}
    action = r.action or data.get("action", "")
    if action == "show":
        run = data.get("run")
        if run:
            _render_workflow_show(run, out, err)
            return
    if action == "list":
        runs = data.get("runs") or []
        _render_workflow_list(runs, out)
        return
    if action == "run":
        _render_workflow_simple(data, out)
        return
    if action == "cancel":
        _render_workflow_simple(data, out)
        return
    # 兜底：未知 action / 缺数据 → 不 dump，仅输出原始关键信息
    _write(out, _compact(data))


def _render_workflow_simple(data, out) -> None:
    """workflow run/cancel 简短响应：runId + status 对齐表格"""
    _write(out, _table(
        ("key", "value"),
        [
            ("runId", data.get("runId", "-")),
            ("status", data.get("status", "-")),
        ],
    ))


def _render_workflow_list(runs, out) -> None:
    if not runs:
        _write(out, fmt_message("No workflow runs."))
        return
    _write(out, _table(
        ("RUN", "NAME", "STATUS", "STEPS", "STARTED", "FINISHED"),
        [
            (
                r.get("runId", "-"),
                r.get("name", "-"),
                r.get("status", "-"),
                str(r.get("stepCount", 0)),
                _fmt_ts(r.get("startedAt")),
                _fmt_ts(r.get("finishedAt")),
            )
            for r in runs
        ],
    ))


def _fmt_ts(ts) -> str:
    """epoch 秒 → HH:MM:SS（None → '-'）"""
    if not isinstance(ts, (int, float)):
        return "-"
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _render_workflow_show(run, out, err) -> None:
    _write(out, _separator("workflow"))
    _write(out, _table(
        ("key", "value"),
        [
            ("name", run.get("name", "-")),
            ("runId", run.get("runId", "-")),
            ("status", run.get("status", "-")),
            ("parallel", str(run.get("maxParallel", "-"))),
            ("started", run.get("startedAt", "-")),
            ("finished", run.get("finishedAt") or "-"),
        ],
    ))
    if run.get("error"):
        _write(err, fmt_message(run['error']))

    steps = run.get("steps") or []
    if steps:
        _write(out, _separator("steps"))
        for st in steps:
            _render_workflow_step(st, out)

    log = run.get("log") or []
    if log:
        _write(out, _separator("log"))
        for entry in log:
            t = entry.get("time")
            msg = entry.get("message", "")
            if "reason=" in msg:
                msg = _tag_reason_in_msg(msg)
            if isinstance(t, (int, float)):
                t = _fmt_epoch(t)
            _write(out, f"  {t}  {msg}")


def _render_workflow_step(st, out) -> None:
    sid = st.get("id", "-")
    status = st.get("status", "-")
    reason = st.get("reason") or st.get("note")
    tag = _REASON_TAG.get(reason, reason or "")
    head = [f"[{sid}]", status]
    if tag:
        head.append(tag)
    # 耗时
    s, e = st.get("started_at"), st.get("ended_at")
    if isinstance(s, (int, float)) and isinstance(e, (int, float)):
        head.append(f"{e - s:.2f}s")
    _write(out, "  " + "  ".join(head))
    output = st.get("output", "")
    if output:
        for line in output.splitlines():
            _write(out, "    " + line)


def _fmt_epoch(ts) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _tag_reason_in_msg(msg: str) -> str:
    """把日志消息里的 ``reason=<原始原因>`` 替换成短标签（trigger_matched→matched 等）
    """
    import re

    def _tag(m):
        val = m.group(1)
        return f"reason={_REASON_TAG.get(val, val)}"

    return re.sub(r"reason=(\w+)", _tag, msg)


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


def print_response(resp: dict):
    """渲染 daemon 响应（经类型化 Result + Presenter：内容→stdout / 元信息→stderr）

    不再原样 JSON dump；错误走 stderr 并由 present() 记录 error 标志。
    """
    present(from_response(resp))