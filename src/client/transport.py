"""TCP 传输层 — Client 类

封装与守护进程的 TCP 通信，向 CLI 入口提供简洁的命令接口。
支持自动启动守护进程、临时默认配置覆盖。
"""

import logging
import os
import sys
import socket
import shlex
import time
from typing import Optional

from ..protocol.message import Message
from ..config import DAEMON_HOST, CONNECT_TIMEOUT, IS_WINDOWS
from ..daemon.lifecycle import is_running, start_daemon, stop_daemon
from .input import process_input
from .formatter import print_response
from .config_manager import ConfigManager

_logger = logging.getLogger("pty-client")

# ── --pty 模式下禁止的 shell 操作符 ──
# 这些操作符作为独立 token 出现时表示依赖 shell 语法，在完整伪终端（列表命令）下无效。
_SHELL_OPS = frozenset({'|', '||', '&', '&&', ';', '>', '<', '>>'})


def _has_shell_operators(cmd: str) -> bool:
    """检查命令字符串是否包含 shell 操作符 token

    使用 shlex.split 分词（正确处理引号包围的内容），
    若任一 token 是纯 shell 操作符则返回 True。

    Args:
        cmd: 命令字符串。

    Returns:
        True 表示命令中出现了 shell 操作符。
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # 引号不匹配，无法分词，保守假设无 shell 操作符
        return False
    return any(t in _SHELL_OPS for t in tokens)


def _parse_iso_time(s: str) -> float:
    """将 ISO 8601 时间字符串转为 Unix 时间戳

    Args:
        s: ISO 8601 格式字符串，如 "2026-06-07T18:00:00"。

    Returns:
        Unix 时间戳（float）。

    Raises:
        ValueError: 格式无法解析时抛出。
    """
    from datetime import datetime
    # 兼容有无微秒、有无时区信息的情况
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.timestamp()


def _read_daemon_port() -> int:
    """从共享内存读取守护进程端口号（委托给 shm_utils）"""
    from ..session.shm_utils import read_port_from_shm
    return read_port_from_shm()


def _read_daemon_token() -> str:
    """从共享内存读取守护进程认证令牌"""
    from ..session.shm_utils import read_auth_token
    return read_auth_token() or ""


class Client:
    """前端客户端，封装与守护进程的 TCP 通信

    提供 cmd_start / cmd_stop / cmd_list / cmd_exec / cmd_send /
    cmd_read / cmd_kill / cmd_events / cmd_closewin 方法，
    每个方法构建请求 dict → _send_recv → print_response。

    端口从命名共享内存（Windows）或文件（Unix）动态获取。
    """

    def __init__(self, host: str = DAEMON_HOST, config_overrides: Optional[dict] = None):
        self.host = host
        self._config = ConfigManager(overrides=config_overrides)

    # ---- 连接管理 ----

    def _connect(self) -> socket.socket:
        """连接到守护进程，必要时自动启动

        读取共享内存中的端口，先 ping 验证存活，僵死则自动重启。

        Returns:
            已连接的 socket。

        Raises:
             SystemExit: 无法连接或启动守护进程。
        """
        from ..daemon.lifecycle import _find_daemon_port, _ping_daemon

        port = _find_daemon_port()
        if port is None:
            _logger.info("守护进程未运行，自动启动")
            start_daemon()
            port = _find_daemon_port()
            if port is None:
                _logger.error("启动守护进程失败")
                print("error: failed to start daemon", file=sys.stderr)
                sys.exit(1)

        # 冷启动时守护进程可能尚未 bind/listen，短暂重试
        last_err = None
        for attempt in range(5):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect((self.host, port))
                _logger.info("已连接守护进程 %s:%s", self.host, port)
                return sock
            except ConnectionRefusedError as e:
                last_err = e
                _logger.debug("_connect: attempt %d refused, retrying...", attempt + 1)
                time.sleep(0.2)
                # 重新检测（可能守护进程已崩溃或重启）
                new_port = _find_daemon_port()
                if new_port is None:
                    _logger.info("守护进程已崩溃，自动重启")
                    start_daemon()
                    new_port = _find_daemon_port()
                    if new_port is None:
                        _logger.error("重启守护进程失败")
                        print("error: failed to restart daemon", file=sys.stderr)
                        sys.exit(1)
                if new_port != port:
                    port = new_port
        _logger.error("连接守护进程失败: %s", last_err)
        print(f"error: {last_err}", file=sys.stderr)
        sys.exit(1)

    def _apply_config_defaults(
        self,
        *,
        timeout: Optional[float] = None,
        keep_ansi: Optional[bool] = None,
        encoding: Optional[str] = None,
        newline: Optional[bool] = None,
    ) -> tuple:
        """应用配置默认值

        对未显式传递的参数，使用 ConfigManager 中的默认值填充（含 --default 临时覆盖）。

        Returns:
            (timeout, keep_ansi, encoding, newline) 元组。
        """
        cfg = self._config.get_all()
        if timeout is None:
            timeout = cfg.get("timeout", 120.0)
        if keep_ansi is None:
            keep_ansi = cfg.get("keep_ansi", False)
        if encoding is None:
            encoding = cfg.get("encoding")
        if newline is None:
            newline = cfg.get("newline", False)
        return timeout, keep_ansi, encoding, newline

    def _maybe_save_encoding(self, encoding: Optional[str], save_encoding: bool):
        """自动记忆 encoding 到持久化配置（新旧值不同时更新）"""
        if save_encoding or (encoding is not None and self._config.get("encoding") != encoding):
            self._config.set("encoding", encoding)

    def _send_recv(self, msg: dict) -> dict:
        # 先建立连接（可能触发自动启动守护进程），确保 token 由当前守护进程写入
        sock = self._connect()
        # 注入认证令牌（必须在 connect 之后读取，否则可能读到旧守护进程的过时 token）
        msg["token"] = _read_daemon_token()
        msg_type = msg.get("type", "?")
        _logger.debug("_send_recv: type=%s id=%s", msg_type, msg.get("id", ""))
        try:
            Message.send(sock, msg)
            resp = Message.recv(sock)
            if resp is None:
                _logger.warning("_send_recv: type=%s no response", msg_type)
            return resp or {"type": "error", "error": "no response"}
        except ConnectionError as e:
            _logger.warning("_send_recv: type=%s connection error: %s", msg_type, e)
            return {"type": "ok", "note": "connection closed"}
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def cmd_start(self):
        _logger.info("cmd_start")
        start_daemon()

    def cmd_stop(self):
        _logger.info("cmd_stop")
        stop_daemon()

    def cmd_exec(
        self,
        session_id: str,
        command,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        pty: bool = False,
        force: bool = False,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
    ):
        _logger.info("cmd_exec: id=%r pty=%s force=%s shell=%s", session_id, pty, force, shell)
        timeout, keep_ansi, encoding, newline = self._apply_config_defaults(
            timeout=timeout, keep_ansi=keep_ansi, encoding=encoding, newline=newline,
        )


        # --pty 模式：将字符串分拆成列表，触发完整伪终端（ConPTY/ConDrv）
        # 启用后不支持 shell 语法（| && > 等）
        if pty and shell:
            print_response({
                "type": "error",
                "error": ("--pty 与 --shell 不能同时使用。\n"
                          "  --pty 将命令拆为列表直接在 ConPTY 执行，不经过 shell。\n"
                          "  → 去掉 --pty 使用 --shell 指定解释器\n"
                          "  → 或去掉 --shell 使用 --pty 的完整伪终端"),
            })
            return
        if pty and isinstance(command, str):
            # 检测 shell 操作符，除非用户显式 --force
            if _has_shell_operators(command):
                if not force:
                    print_response({
                        "type": "error",
                        "error": (
                            "--pty 模式下命令中包含 shell 操作符 (| & > < && || ;)，"
                            "这些操作符依赖 shell 解析，在完整伪终端下无效。\n"
                            "  → 去掉 --pty 使用默认 shell 模式\n"
                            "  → 或加 --force-pty-mode 强制执行（shell 操作符将作为字面参数传给程序）"
                        ),
                    })
                    return
                _logger.warning(
                    "--force-pty-mode: 忽略 shell 操作符检测，原样拆分执行, command=%r", command,
                )
            command = shlex.split(command)

        msg = {
            "type": "exec", "id": session_id, "command": command,
            "newline": newline, "fresh": fresh, "full": full,
            "keep_ansi": keep_ansi, "timeout": timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
        if encoding is not None:
            msg["encoding"] = encoding
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if shell is not None:
            msg["shell"] = shell
        msg["cwd"] = cwd if cwd is not None else os.getcwd()

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_send(
        self,
        session_id: str,
        input_text: str,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        json_escaping: bool = False,
    ):
        """发送输入并等待触发（默认启用 GUI 窗口检测）"""
        _logger.info("cmd_send: id=%r trigger=%r timeout=%s json_escaping=%s",
                     session_id, trigger, timeout, json_escaping)
        timeout, keep_ansi, encoding, newline = self._apply_config_defaults(
            timeout=timeout, keep_ansi=keep_ansi, encoding=encoding, newline=newline,
        )

        msg = {
            "type": "send", "id": session_id,
            "input": process_input(input_text, json_escaping=json_escaping),
            "newline": newline, "fresh": fresh, "full": full,
            "keep_ansi": keep_ansi, "timeout": timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
        if encoding is not None:
            msg["encoding"] = encoding
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_read(
        self,
        session_id: str,
        lines: Optional[str] = None,
        grep: Optional[str] = None,
        offset: Optional[int] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
    ):
        """直接读取会话终端输出（不设触发条件）"""
        _logger.info("cmd_read: id=%r lines=%s grep=%r offset=%s full=%s",
                     session_id, lines, grep, offset, full)
        _, keep_ansi, encoding, _ = self._apply_config_defaults(
            keep_ansi=keep_ansi, encoding=encoding,
        )

        msg = {
            "type": "read", "id": session_id,
            "full": full, "keep_ansi": keep_ansi,
        }
        if lines is not None:
            msg["lines"] = lines
        if grep is not None:
            msg["grep"] = grep
        if offset is not None:
            msg["offset"] = offset
        if encoding is not None:
            msg["encoding"] = encoding

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_list(self):
        """列出所有会话"""
        _logger.info("cmd_list")
        resp = self._send_recv({"type": "list"})
        if resp.get("type") == "ok":
            sessions = resp.get("sessions", [])
            if not sessions:
                print_response({"type": "ok", "sessions": [], "note": "无活跃会话"})
            else:
                print_response(resp)
        else:
            print_response(resp)

    def cmd_events(self, session_id: str,
                   last: Optional[int] = None,
                   since: Optional[str] = None,
                   until: Optional[str] = None):
        """获取会话的所有事件

        Args:
            session_id: 会话标识符。
            last:       仅返回最近 N 条事件。
            since:      ISO 8601 时间字符串，仅返回此时间之后的事件。
            until:      ISO 8601 时间字符串，仅返回此时间之前的事件。
        """
        _logger.info("cmd_events: id=%r last=%s since=%s until=%s", session_id, last, since, until)
        msg: dict = {"type": "events", "id": session_id}

        # ISO 8601 → Unix 时间戳，在客户端转换后传给守护进程
        if since:
            msg["since"] = _parse_iso_time(since)
        if until:
            msg["until"] = _parse_iso_time(until)
        if last is not None:
            msg["last"] = last

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_kill(self, session_id: str):
        """终止指定会话"""
        _logger.info("cmd_kill: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            print_response({"type": "error", "error": "invalid session id"})
            return
        try:
            resp = self._send_recv({"type": "kill", "id": session_id})
        except ConnectionError:
            resp = {"type": "ok", "note": "daemon not running"}
        except socket.timeout:
            resp = {"type": "ok", "note": "daemon unresponsive, session likely dead"}
        except OSError:
            resp = {"type": "ok", "note": "daemon connection failed, session likely dead"}
        if resp.get("type") == "ok":
            resp.setdefault("note", f"会话 {session_id} 已终止")
            print_response(resp)
        else:
            print_response(resp)

    def cmd_closewin(self, session_id: str, hwnd: int):
        """关闭指定 GUI 窗口

        Args:
            session_id: 目标会话标识符。
            hwnd:       窗口句柄。
        """
        _logger.info("cmd_closewin: id=%r hwnd=0x%X", session_id, hwnd)
        resp = self._send_recv({
            "type": "closewin",
            "id": session_id,
            "hwnd": hwnd,
        })
        print_response(resp)
