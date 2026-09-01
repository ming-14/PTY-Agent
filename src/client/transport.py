"""共享内存传输层 — Client 类

封装与守护进程的共享内存通信，向 CLI 入口提供简洁的命令接口。
支持自动启动守护进程、临时默认配置覆盖。

无 socket、无端口，所有请求/响应通过命名共享内存 + 信箱传递。
"""

import logging
import os
import sys
import shlex
import time
from typing import Optional

from ..protocol.shm import (
    Mailbox, make_channel_names, read_message, write_message,
    _DATA_BODY_OFF,
)
from ..protocol.shm_utils import open_shm, close_shm
from ..config import REQ_SHM_SIZE, RESP_SHM_SIZE, DEFAULT_TRIGGER_TIMEOUT
from ..daemon.lifecycle import is_running, start_daemon, stop_daemon
from ..session.shm_utils import read_auth_token
from .input import process_input
from .formatter import print_response
from .config_manager import ConfigManager

_logger = logging.getLogger("pty-client")

# ── --pty 模式下禁止的 shell 操作符 ──
_SHELL_OPS = frozenset({'|', '||', '&', '&&', ';', '>', '<', '>>'})


def _has_shell_operators(cmd: str) -> bool:
    """检查命令字符串是否包含 shell 操作符 token"""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    return any(t in _SHELL_OPS for t in tokens)


class Client:
    """前端客户端，封装与守护进程的共享内存通信

    提供 cmd_start / cmd_stop / cmd_list / cmd_exec / cmd_send /
    cmd_read / cmd_kill / cmd_closewin 方法，
    每个方法构建请求 dict → _send_recv → print_response。

    请求/响应通过命名共享内存 + 信箱传递，无 socket 依赖。
    """

    def __init__(self, config_overrides: Optional[dict] = None):
        self._config = ConfigManager(overrides=config_overrides)

    # ---- 连接管理（共享内存版） ----

    def _ensure_daemon(self):
        """确保守护进程在运行，必要时自动启动

        Raises:
            SystemExit: 无法启动守护进程。
        """
        if not is_running():
            _logger.info("守护进程未运行，自动启动")
            start_daemon()
            # 等待守护进程就绪
            for _ in range(15):
                if is_running():
                    return
                time.sleep(0.2)
            _logger.error("启动守护进程失败")
            print("error: failed to start daemon", file=sys.stderr)
            sys.exit(1)

    def _send_recv(self, msg: dict) -> dict:
        """通过共享内存发送请求并接收响应

        Args:
            msg: 请求消息字典。

        Returns:
            响应字典。
        """
        self._ensure_daemon()

        seq = int(time.time() * 1000) % 100000
        req_name, resp_name = make_channel_names(os.getpid(), seq)

        req_shm = open_shm(req_name, REQ_SHM_SIZE)
        resp_shm = open_shm(resp_name, RESP_SHM_SIZE)
        if req_shm is None or resp_shm is None:
            close_shm(req_shm)
            close_shm(resp_shm)
            _logger.error("创建共享内存通道失败")
            return {"type": "error", "error": "创建共享内存通信通道失败"}

        try:
            # 注入认证令牌
            token = read_auth_token() or ""
            msg["token"] = token

            # 写入请求
            write_message(req_shm, msg, REQ_SHM_SIZE - _DATA_BODY_OFF,
                          truncated_marker=False)

            # 注册到信箱
            mailbox = Mailbox()
            slot = mailbox.acquire_slot(os.getpid(), req_name, resp_name,
                                        token, seq)
            if slot is None:
                _logger.error("请求信箱已满")
                return {"type": "error", "error": "请求信箱已满，请稍后重试"}

            try:
                # 计算等待超时（请求超时 + 缓冲区）
                wait_timeout = msg.get("timeout", DEFAULT_TRIGGER_TIMEOUT) + 5

                # 等待处理完成
                if not mailbox.wait_done(slot, timeout=wait_timeout):
                    _logger.warning("请求超时 (type=%s, timeout=%s)", msg.get("type"), wait_timeout)
                    return {"type": "error", "error": "守护进程响应超时"}

                # 读取响应
                resp = read_message(resp_shm)
                if resp is None:
                    _logger.warning("读取响应失败")
                    return {"type": "error", "error": "读取响应失败"}
                return resp
            finally:
                mailbox.release_slot(slot)
        finally:
            close_shm(req_shm)
            close_shm(resp_shm)

    # ---- 配置助手 ----

    def _apply_config_defaults(
        self,
        *,
        timeout: Optional[float] = None,
        newline: Optional[bool] = None,
    ) -> tuple:
        """应用配置默认值"""
        cfg = self._config.get_all()
        if timeout is None:
            timeout = cfg.get("timeout", 120.0)
        if newline is None:
            newline = cfg.get("newline", False)
        return timeout, newline

    # ---- 命令方法 ----

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
        full: bool = False,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        pty: bool = False,
        force: bool = False,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
    ):
        _logger.info("cmd_exec: id=%r pty=%s force=%s shell=%s", session_id, pty, force, shell)
        timeout, newline = self._apply_config_defaults(
            timeout=timeout, newline=newline,
        )

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
            "timeout": timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
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
        full: bool = False,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
    ):
        _logger.info("cmd_send: id=%r trigger=%r timeout=%s",
                     session_id, trigger, timeout)
        timeout, newline = self._apply_config_defaults(
            timeout=timeout, newline=newline,
        )

        msg = {
            "type": "send", "id": session_id,
            "input": process_input(input_text),
            "newline": newline, "fresh": fresh, "full": full,
            "timeout": timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
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
        full: bool = False,
    ):
        _logger.info("cmd_read: id=%r lines=%s grep=%r offset=%s full=%s",
                     session_id, lines, grep, offset, full)

        msg = {
            "type": "read", "id": session_id,
            "full": full,
        }
        if lines is not None:
            msg["lines"] = lines
        if grep is not None:
            msg["grep"] = grep
        if offset is not None:
            msg["offset"] = offset

        resp = self._send_recv(msg)
        print_response(resp)

    def cmd_list(self):
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

    def cmd_kill(self, session_id: str):
        _logger.info("cmd_kill: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            print_response({"type": "error", "error": "invalid session id"})
            return
        try:
            resp = self._send_recv({"type": "kill", "id": session_id})
        except Exception as e:
            resp = {"type": "ok", "note": f"daemon not running ({e})"}
        if resp.get("type") == "ok":
            resp.setdefault("note", f"会话 {session_id} 已终止")
            print_response(resp)
        else:
            print_response(resp)

    def cmd_closewin(self, session_id: str, hwnd: int):
        _logger.info("cmd_closewin: id=%r hwnd=0x%X", session_id, hwnd)
        resp = self._send_recv({
            "type": "closewin",
            "id": session_id,
            "hwnd": hwnd,
        })
        print_response(resp)
