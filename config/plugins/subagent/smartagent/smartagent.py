#!/usr/bin/env python3
"""smartagent 子代理 — 服务端主程序（PTY 主进程，spawn 目标）

职责：
- 渲染聊天界面到 stdout = AI 的屏幕（read --rf snapshot 看到）
- 读 PTY stdin（AI send 消息）→ 写 JSONL → 推送给人类窗口
- 开线程启动 smartagent_tui.py（人类侧独立窗口，管道通信）
- 读人类窗口管道（人类输入）→ 写 JSONL → 重绘 AI 屏幕
- **所有 JSONL 写操作只在此发生**，smartagent_tui 不写任何文件

行为约定：
- AI 每次发消息都检查人类窗口是否在线，不在线立即重启并补发历史
- 人类窗口在待反馈期间关闭 → 写 "Smart Agent 已砸锅" 事件 + 屏幕状态
- oneshot 模式（--oneshot）：人类提交首个回复后输出并退出（供 _wait_and_return 返回）

用法：python -u smartagent.py --sid <uuid> --prompt "<任务>" [--oneshot]
      (--sid 和 --prompt 由 AgentSpec.build_command 生成)

参数：
  --sid     会话 ID（必填，AgentSpec 生成）
  --prompt  初始任务描述（AI 第一条消息）
  --role    角色/视角（--model 映射，如 "reviewer"）
  --title   会话标题
  --oneshot 一次性模式：人类提交首个回复后退出（子进程 stdin 关闭时也等待）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid

# ── 路径 ─────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))

def _jsonl_path(sid: str) -> str:
    return os.path.join(tempfile.gettempdir(), "smartagent_subagent", sid + ".jsonl")


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


# ── AI 屏幕渲染（read --rf snapshot 看到） ───────────

_STATUS_IDLE = "idle（等待 AI 消息）"        # 服务端就绪，等 AI 发消息
_STATUS_WORKING = "Smart工作中…"               # AI 已发消息，等人类提交
_STATUS_SENT = "Smart已回复"     # 人类已提交 → turn_complete 信号
_STATUS_DROPPED = "Smart Agent 已砸锅"   # 待反馈期间人类窗口关闭

_MESSAGES: list = []  # [(role, text)]  role="ai"|"human"


def _render_screen(sid: str, title: str, role: str,
                   messages: list, status: str) -> str:
    """渲染 AI 侧聊天界面（read --rf snapshot 看到这个）"""
    lines = ["─" * 60,
             " Smart Chat — %s " % (title or sid)]
    if role:
        lines[1] += " [%s]" % role
    lines.append("─" * 60)
    for r, txt in messages:
        prefix = "[You] " if r == "ai" else "[Smart] "
        lines.append(prefix + txt)
    lines += ["─" * 60, status, "─" * 60]
    return "\n".join(lines) + "\n"


# ── JSONL 写入（服务端唯一写点） ─────────────────────

def _append_event(path: str, event: dict) -> None:
    """追加事件到 JSONL（原子写：读全量 → 写 tmp → replace）

    注意：不能直接 open(path, "a") 追加（非原子，崩溃可能半行）；
    也不能 open(tmp, "a") 后 replace（replace 移走 tmp 导致历史丢失）。
    这里读全量历史 + 写 tmp + os.replace 覆盖，保证原子且保留全部事件。
    """
    _ensure_dir(path)
    lines = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f if ln.strip()]
        except OSError:
            lines = []
    lines.append(json.dumps(event, ensure_ascii=False) + "\n")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, path)
    except OSError as e:
        _log("JSONL 写入失败: %s", e)


# ── 人类窗口管理（子进程 + 管道通信） ────────────────

def _start_smart_window(sid: str, smart_tui_path: str) -> subprocess.Popen:
    """启动人类侧聊天窗口（独立终端），返回 Popen 对象

    Windows：CREATE_NEW_CONSOLE 新窗口，stdin/stdout PIPE 通信
    Linux：xterm -e …（若 xterm 可用），否则提示
    """
    cmd = [sys.executable, "-u", smart_tui_path, "--sid", sid]

    if os.name == "nt":
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,  # 继承 stderr（smartagent_tui 日志写文件，此处仅兜底）
            text=True,  # 文本模式管道（write str / readline 返回 str）
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        _log("人类窗口已启动 (PID=%d)", proc.pid)
        return proc

    # Unix：尝试 xterm / gnome-terminal
    for term in ("xterm", "gnome-terminal", "konsole", "xfce4-terminal"):
        exe = term
        if term == "xterm":
            args = [exe, "-e"] + cmd
        elif term == "gnome-terminal":
            args = [exe, "--", "bash", "-c", " ".join(cmd)]
        else:
            args = [exe, "-e", " ".join(cmd)]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _log("人类窗口已启动 (term=%s PID=%d)", term, proc.pid)
            return proc
        except FileNotFoundError:
            continue
    _log("警告：未找到终端模拟器（xterm/gnome-terminal），人类窗口未启动；"
         "请手动在另一个终端运行: %s", " ".join(cmd))
    return None


def _send_to_smart(proc, msg: dict) -> None:
    """通过管道向人类窗口推送消息"""
    if proc is None or proc.stdin is None:
        return
    try:
        proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except OSError:
        pass


def _read_smart_line(proc) -> str | None:
    """从人类窗口管道读一行（人类输入），非阻塞，无可读时返回 None"""
    if proc is None or proc.stdout is None:
        return None
    import select
    if os.name == "nt":
        # Windows select 不能用于管道，用线程 + timeout
        # 这里简化：直接阻塞读（服务端主循环在单独线程处理）
        try:
            line = proc.stdout.readline()
            return line.strip() if line else None
        except OSError:
            return None
    else:
        try:
            if select.select([proc.stdout], [], [], 0.05)[0]:
                line = proc.stdout.readline()
                return line.strip() if line else None
        except (OSError, select.error):
            pass
        return None


# ── AI 消息读取（PTY stdin） ─────────────────────────

def _read_ai_message() -> str | None:
    """读 PTY stdin（AI send 消息），非阻塞"""
    import select
    if os.name == "nt":
        try:
            line = sys.stdin.readline()
            return line.strip() if line else None
        except OSError:
            return None
    else:
        try:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                line = sys.stdin.readline()
                return line.strip() if line else None
        except (OSError, select.error):
            pass
        return None


# ── 主循环 ──────────────────────────────────────────

def _log(fmt: str, *args) -> None:
    try:
        sys.stderr.write("[smartagent] " + (fmt % args) + "\n")
        sys.stderr.flush()
    except OSError:
        pass


def _reader_thread(stream, queue) -> None:
    """后台线程：阻塞读流，每行推入 queue（EOF 时推 None 结束）"""
    try:
        while True:
            line = stream.readline()
            if not line:
                queue.put(None)
                return
            line = line.strip()
            if line:
                queue.put(line)
    except (OSError, ValueError):
        queue.put(None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="smartagent", description="smartagent 子代理服务端")
    parser.add_argument("--sid", required=True, help="会话 ID")
    parser.add_argument("--prompt", default="", help="初始任务消息")
    parser.add_argument("--role", default="", help="角色/视角（--model 映射）")
    parser.add_argument("--title", default="", help="会话标题")
    parser.add_argument("--oneshot", action="store_true",
                        help="一次性模式：人类提交首个回复后退出")
    args = parser.parse_args(argv)

    sid = args.sid
    jsonl_path = _jsonl_path(sid)
    _ensure_dir(jsonl_path)

    # 发初始消息（AI 任务）
    if args.prompt:
        _MESSAGES.append(("ai", args.prompt))
        _append_event(jsonl_path, {"type": "ai_message", "text": args.prompt,
                                    "ts": int(time.time() * 1000), "sessionId": sid})

    smart_tui = os.path.join(_HERE, "smartagent_tui.py")
    if not os.path.isfile(smart_tui):
        smart_tui = os.path.join(os.path.dirname(_HERE), "smartagent_tui.py")

    import queue as _queue
    ai_q = _queue.Queue()
    smart_q = _queue.Queue()
    threading.Thread(target=_reader_thread, args=(sys.stdin, ai_q), daemon=True).start()

    # ── 人类窗口管理：检查在线 + 重启 + 补发历史 ─────────
    _proc = {"p": None}  # 用 dict 包装，供内层函数修改

    def _window_alive() -> bool:
        p = _proc["p"]
        return p is not None and p.poll() is None

    def _start_window() -> None:
        """启动人类窗口并挂读取线程；失败仅记录（AI 屏幕仍可用）"""
        try:
            p = _start_smart_window(sid, smart_tui)
        except Exception as e:
            _log("人类窗口启动失败: %s", e)
            return
        _proc["p"] = p
        if p is not None and p.stdout is not None:
            threading.Thread(target=_reader_thread, args=(p.stdout, smart_q),
                             daemon=True).start()
        _log("人类窗口已启动 PID=%s", p.pid if p else "N/A")

    def _resend_history() -> None:
        """向当前人类窗口补发全部消息历史 + working 状态

        人类消息带 replay 标记：窗口侧实时回传的人类消息（无标记）会跳过
        （本地已记录），而重启补发的历史人类消息必须显示，靠标记区分。
        """
        p = _proc["p"]
        if p is None:
            return
        for role, txt in _MESSAGES:
            _send_to_smart(p, {"type": "msg", "role": role, "text": txt,
                               "replay": True})
        _send_to_smart(p, {"type": "status", "text": "working"})

    def _ensure_window() -> None:
        """AI 发消息前调用：窗口不在线则重启 + 补发全部历史"""
        if _window_alive():
            return
        _log("人类窗口不在线，重启")
        # 清空旧队列残留（旧 reader 可能已推入 None，避免重启后再次触发已砸锅）
        while not smart_q.empty():
            try:
                smart_q.get_nowait()
            except _queue.Empty:
                break
        _start_window()
        _resend_history()

    _start_window()
    _resend_history()  # 补发初始 prompt 到人类窗口

    # 初始渲染 AI 屏幕
    _log("会话 %s 已启动", sid)

    # 初始状态：有 prompt 视为 AI 已发首条消息 → 人类工作中（等人类提交）；
    # 无 prompt 等 AI 首条消息（idle）
    app_status = _STATUS_WORKING if args.prompt else _STATUS_IDLE
    _log("主循环启动 status=%s oneshot=%s", app_status, args.oneshot)

    # 信号处理：daemon kill 会话时（SIGTERM）清理人类窗口子进程，防止残留独立终端
    import signal as _signal

    def _on_signal(signum, _frame):
        _log("收到信号 %s，清理人类窗口", signum)
        _cleanup_smart_window(_proc["p"])
        sys.exit(0)

    try:
        _signal.signal(_signal.SIGTERM, _on_signal)
        _signal.signal(_signal.SIGINT, _on_signal)
    except (ValueError, OSError):
        pass  # 非主线程/非 Unix 环境不支持

    def _drain(q):
        """取队列中当前全部元素（阻塞超时后无元素返回空列表）"""
        items = []
        while True:
            try:
                items.append(q.get(timeout=0.05))
            except _queue.Empty:
                break
        return items

    def _on_window_closed():
        """人类窗口关闭（EOF）。待反馈期间关闭 → 写"已砸锅"事件"""
        if app_status == _STATUS_WORKING:
            _log("人类窗口在待反馈期间关闭 → 已砸锅")
            _append_event(jsonl_path, {"type": "system", "text": _STATUS_DROPPED,
                                        "ts": int(time.time() * 1000),
                                        "sessionId": sid})
            return True
        return False

    _done = False
    while not _done:
        # ── 读 AI 消息（PTY stdin）→ 检查窗口 → 推送 ──
        ai_items = _drain(ai_q)
        if any(x is None for x in ai_items) and not args.oneshot:
            _log("stdin EOF，退出")
            break
        if ai_items:
            _log("收到 %d 条 AI 消息", len(ai_items))
        for ai_text in ai_items:
            if ai_text is None:
                continue
            _log("AI 消息: %s", ai_text[:60])
            _ensure_window()  # 需求1：发消息前检查人类窗口在线，不在线重启补发历史
            _MESSAGES.append(("ai", ai_text))
            _append_event(jsonl_path, {"type": "ai_message", "text": ai_text,
                                        "ts": int(time.time() * 1000),
                                        "sessionId": sid})
            _send_to_smart(_proc["p"], {"type": "msg", "role": "ai", "text": ai_text})
            _send_to_smart(_proc["p"], {"type": "status", "text": "working"})
            app_status = _STATUS_WORKING

        # ── 读人类输入（管道）→ 状态变 sent（turn_complete 信号） ──
        smart_items = _drain(smart_q)
        if smart_items:
            _log("收到 %d 条人类输入", len(smart_items))
        for smart_text in smart_items:
            if smart_text is None:
                _log("人类窗口关闭（EOF）")
                if _on_window_closed():  # 需求3：待反馈期间关闭 → 已砸锅事件
                    app_status = _STATUS_DROPPED
                if args.oneshot:
                    _log("oneshot 窗口关闭，退出")
                    _done = True
                    break
                continue
            d = json.loads(smart_text) if smart_text.startswith("{") else {"type": "input", "text": smart_text}
            if d.get("type") == "input":
                text = d.get("text", "")
                _log("人类输入: %s", text[:60])
                _MESSAGES.append(("human", text))
                _append_event(jsonl_path, {"type": "human_message", "text": text,
                                            "ts": int(time.time() * 1000),
                                            "sessionId": sid})
                _send_to_smart(_proc["p"], {"type": "msg", "role": "human", "text": text})
                _send_to_smart(_proc["p"], {"type": "status", "text": "idle"})
                app_status = _STATUS_SENT
                _log("状态变 sent（人类已提交）")
                if args.oneshot:
                    _log("oneshot 已收到回复，输出并退出")
                    sys.stdout.write(text + "\n")
                    sys.stdout.flush()
                    _cleanup_smart_window(_proc["p"])
                    return 0
        if _done:
            break

        # ── 渲染 AI 屏幕（stdout = PTY 输出，read --rf snapshot 看到） ──
        # oneshot 为 subprocess 阻塞模式，全量 stdout 会返回给调用方：
        # 不渲染屏幕（避免 VT 清屏序列污染输出），仅在结束时输出最后人类回复。
        if not args.oneshot:
            screen = _render_screen(sid, args.title, args.role, _MESSAGES, app_status)
            try:
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.write(screen)
                sys.stdout.flush()
            except OSError:
                _log("渲染失败（stdout 异常）")
                break

    _log("会话 %s 结束，最后人类消息数: %d", sid, len([m for m in _MESSAGES if m[0] == "human"]))
    # oneshot 模式：退出时输出最后人类回复（供 _wait_and_return 返回）
    if args.oneshot:
        for r, txt in reversed(_MESSAGES):
            if r == "human":
                sys.stdout.write(txt + "\n")
                sys.stdout.flush()
                break
    _cleanup_smart_window(_proc["p"])
    return 0


def _cleanup_smart_window(proc) -> None:
    """关闭人类窗口子进程（正常退出或信号终止时调用，防止残留独立终端）"""
    if proc is None:
        return
    try:
        if proc.poll() is None:  # 仍在运行
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
            _log("人类窗口已关闭 (PID=%d)", proc.pid)
    except OSError as e:
        _log("关闭人类窗口失败: %s", e)


if __name__ == "__main__":
    sys.exit(main())