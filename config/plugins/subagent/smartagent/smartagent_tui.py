#!/usr/bin/env python3
"""smartagent 子代理 — 人类侧聊天窗口（独立终端，由服务端 smartagent.py 启动）

只做两件事（**不写任何 JSONL，写由服务端承担**）：
1. 从 stdin 管道接收服务端推送的渲染屏幕/消息，显示聊天界面
2. 读键盘输入，经 stdout 管道回传服务端

与服务端通信协议（stdin/stdout 各一行一个 JSON）：
  服务端 → 本窗口（stdin）: {"type":"screen","text":"<完整屏幕渲染>"}
                         {"type":"msg","role":"ai"|"human","text":"..."}
  本窗口 → 服务端（stdout）: {"type":"input","text":"..."}
                            {"type":"ack"}   （收到屏幕的确认，可选）

Windows 下由服务端以 CREATE_NEW_CONSOLE 打开本窗口，stdin/stdout 为管道，
键盘经 msvcrt 从控制台读取；Unix 下由服务端经终端模拟器（xterm 等）打开，
键盘经 termios 从 tty 读取。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

# ── 日志（写文件：<temp>/smartagent_subagent/<sid>.tui.log，stderr 是管道不可靠） ──

_log_path: str = ""


def _log(fmt: str, *args) -> None:
    """写调试日志到文件（带毫秒时间戳）"""
    if not _log_path:
        return
    try:
        line = "[%s] %s\n" % (time.strftime("%H:%M:%S"),
                              fmt % args if args else fmt)
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass

# ── 终端键盘（跨平台）——从控制台读（stdin 是服务端通信管道） ──

_IS_WINDOWS = os.name == "nt"
_VT_OK = True  # 控制台是否支持 ANSI VT 转义（清屏 \x1b[2J\x1b[H）


def _enable_vt_win(con) -> bool:
    """Windows 下为控制台句柄启用 ENABLE_VIRTUAL_TERMINAL_PROCESSING

    返回 True 表示 VT 已启用（可用 ANSI 清屏），False 表示不支持（退化为追加换行）。
    """
    if not _IS_WINDOWS:
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(con.fileno())
        mode = ctypes.c_uint()
        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ok = kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return bool(ok)
    except (OSError, AttributeError, ImportError):
        pass
    return False


def _console_out():
    """控制台屏幕输出（Windows CONOUT$ / Unix /dev/tty）；
    非管道 stdout——stdout 是服务端通信通道，渲染必须走控制台。

    同时设置模块级 _VT_OK 标志，指示是否支持 ANSI VT 清屏。
    """
    global _VT_OK
    try:
        if _IS_WINDOWS:
            con = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            _VT_OK = _enable_vt_win(con)
            return con
        _VT_OK = True
        return open("/dev/tty", "w", encoding="utf-8", errors="replace")
    except OSError:
        _VT_OK = False
        return None


def _console_in():
    """控制台键盘输入流（Unix 读取用）"""
    try:
        return open("/dev/tty", "r", encoding="utf-8", errors="replace")
    except OSError:
        return None


if _IS_WINDOWS:
    import msvcrt

    def _read_key() -> str:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()  # 功能键/方向键前缀，丢弃
            return ""
        return ch

else:
    import termios
    import tty
    import select

    def _read_key() -> str:
        """从 /dev/tty 读单个按键（stdin 是管道，不能用）"""
        tty_file = _console_in()
        if tty_file is None:
            return ""
        fd = tty_file.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = tty_file.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            tty_file.close()
        if ch == "\x1b":  # ESC 序列（方向键等）丢弃
            ci = _console_in()
            if ci is not None:
                try:
                    if select.select([ci], [], [], 0.05)[0]:
                        ci.read(2)
                except (OSError, TypeError):
                    pass
                ci.close()
            return ""
        return ch


# ── 聊天窗口 ─────────────────────────────────────────

class SmartWindow:
    """人类侧聊天界面：消息区 + 输入行"""

    def __init__(self, sid: str):
        self.sid = sid
        self.messages: list = []        # [(role, text)]
        self.input_buf = ""
        self.status = "idle"            # idle / input / sent
        self._lock = threading.Lock()
        self._out = sys.stdout          # 管道 → 服务端
        self._con = None                # 控制台输出句柄（缓存，首次打开 + 启用 VT）

    def _console(self):
        """获取控制台输出（缓存句柄；失败返回 None）"""
        if self._con is None:
            self._con = _console_out()
        return self._con

    # ── 渲染 ─────────────────────────────────────────

    def _render(self) -> str:
        lines = ["─" * 50, " Smart Chat — %s " % self.sid, "─" * 50]
        for role, text in self.messages:
            prefix = "AI: " if role == "ai" else "You: "
            lines.append(prefix + text)
        lines += ["─" * 50, "> " + self.input_buf, "─" * 50, self.status, "─" * 50]
        return "\n".join(lines) + "\n"

    def _input_row(self) -> int:
        """输入行（> xxx）所在行号（1 基）：标题 2 行 + 分隔 + 消息 + 分隔"""
        return 3 + len(self.messages) + 2

    def _redraw(self) -> None:
        """全量重绘（启动 / 消息变化 / 状态变化 / 提交后调用）"""
        con = self._console()
        if con is None:
            _log("渲染失败: 无法打开控制台输出（CONOUT$/tty）")
            return
        try:
            if _VT_OK:
                con.write("\x1b[2J\x1b[H")
            else:
                con.write("\n")
            con.write(self._render())
            con.flush()
        except OSError as e:
            _log("渲染失败: %s", e)

    def _redraw_input(self) -> None:
        """仅更新输入行（按键时调用，不整屏重绘）

        光标移到输入行行首 → 清除该行 → 重写 "> xxx"。
        VT 不可用时退化为全量重绘。
        """
        if not _VT_OK:
            self._redraw()
            return
        con = self._console()
        if con is None:
            return
        try:
            row = self._input_row()
            con.write("\x1b[%d;1H\x1b[K> %s" % (row, self.input_buf))
            con.flush()
        except OSError as e:
            _log("输入行渲染失败: %s", e)

    # ── 服务端消息（stdin 管道） ─────────────────────

    def _server_reader(self) -> None:
        """后台线程：读 stdin 管道（服务端推送）"""
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                _log("server_reader 异常退出")
                return
            if not line:
                _log("server_reader: stdin EOF（服务端关闭）")
                return
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type", "")
            if mtype == "msg":
                role = msg.get("role", "")
                text = str(msg.get("text", ""))
                replay = bool(msg.get("replay", False))  # 服务端补发历史时标记
                if role != "ai" and not replay:
                    # 人类消息由本地 run() 提交时已记录（self.messages.append + redraw），
                    # 服务端回传 {"type":"msg","role":"human","text":…} 是重复推送，
                    # 跳过本行直接进入下一轮（否则每条人类消息显示两次）。
                    # replay=True 的补发历史人类消息不能跳过（新窗口无本地记录）。
                    _log("跳过服务端回传的人类消息（本地已记录）")
                    continue
                _log("收到消息 role=%s replay=%s text=%s", role, replay, text[:40])
                with self._lock:
                    self.messages.append((role, text))
                    self.status = "idle"
                self._redraw()
            elif mtype == "status":
                with self._lock:
                    self.status = str(msg.get("text", "idle"))
                _log("状态更新: %s", self.status)
                self._redraw()

    # ── 输入提交（stdout 管道 → 服务端） ─────────────

    def _send_input(self, text: str) -> None:
        _log("提交输入: %s", text[:40])
        try:
            self._out.write(json.dumps({"type": "input", "text": text}) + "\n")
            self._out.flush()
        except OSError:
            _log("提交输入失败（管道断开）")

    # ── 主循环 ──────────────────────────────────────

    def run(self) -> None:
        _log("主循环启动")
        threading.Thread(target=self._server_reader, daemon=True).start()
        self._redraw()
        while True:
            try:
                ch = _read_key()
            except (KeyboardInterrupt, EOFError):
                _log("主循环退出（键盘中断/EOF）")
                break
            if ch == "\x03":  # Ctrl+C 退出
                _log("Ctrl+C 退出")
                break
            if ch in ("\r", "\n"):
                text = self.input_buf.strip()
                self.input_buf = ""
                if text:
                    with self._lock:
                        self.messages.append(("human", text))
                        self.status = "sent"
                    self._send_input(text)
                self._redraw()  # 提交：全量重绘（消息区变化）
                continue
            if ch in ("\x7f", "\x08"):  # Backspace
                self.input_buf = self.input_buf[:-1]
                with self._lock:
                    self.status = "input"
                self._redraw_input()  # 仅更新输入行
                continue
            if ch and ch.isprintable():
                self.input_buf += ch
                with self._lock:
                    self.status = "input"
                self._redraw_input()  # 仅更新输入行
                continue


def main(argv=None) -> int:
    global _log_path
    parser = argparse.ArgumentParser(prog="smartagent_tui",
                                     description="smartagent 人类侧聊天窗口")
    parser.add_argument("--sid", required=True, help="会话 ID")
    args = parser.parse_args(argv)
    # 日志路径：<temp>/smartagent_subagent/<sid>.tui.log
    import tempfile
    _log_path = os.path.join(tempfile.gettempdir(), "smartagent_subagent",
                             args.sid + ".tui.log")
    _log("smartagent_tui 启动 sid=%s", args.sid)
    win = SmartWindow(args.sid)
    try:
        win.run()
    except Exception:
        _log("smartagent_tui 异常退出")
        return 1
    _log("smartagent_tui 退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
