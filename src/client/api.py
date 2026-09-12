"""PtyClient — 前端无关的统一调用门面

负责把命令参数组装为请求 dict，经 protocol.request 与守护进程通信，
**返回结构化响应 dict，不做任何打印/呈现**。CLI、未来 MCP 等前端
都复用本类。呈现由 client.presenters 负责。

不再依赖 daemon 包：连接管理（ensure/启动）委托 client.controller。
"""

import logging
import os
import shlex
from typing import Optional

from ..protocol.request import roundtrip
from .controller import ensure_daemon
from .config_manager import ConfigManager, resolve_eol
from .input import process_input

_logger = logging.getLogger("pty-client")

# ── --pty 模式下禁止的 shell 操作符 ──
_SHELL_OPS = frozenset({'|', '||', '&', '&&', ';', '>', '<', '>>'})


def _has_shell_operators(cmd: str) -> bool:
    """检查命令字符串是否包含 shell 操作符 token

    Args:
        cmd: 命令字符串。

    Returns:
        True 表示包含 shell 操作符。
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    return any(t in _SHELL_OPS for t in tokens)


class PtyClient:
    """会话操作门面（仅构建请求 + 往返，不呈现）

    提供 cmd_exec / cmd_send / cmd_read / cmd_list / cmd_remove /
    cmd_closewin / cmd_start / cmd_stop 方法，均返回响应 dict。
    """

    def __init__(self, config_overrides: Optional[dict] = None):
        self._config = ConfigManager(overrides=config_overrides)

    # ---- 连接管理 ----

    def _send(self, msg: dict) -> dict:
        """确保守护进程运行后发送一次共享内存往返

        Args:
            msg: 请求消息字典。

        Returns:
            响应字典。
        """
        ensure_daemon()
        return roundtrip(msg, timeout=msg.get("timeout"))

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

    # ---- 命令方法（全部返回 dict）----

    def cmd_start(self) -> dict:
        from .controller import start_daemon
        start_daemon()
        return {"type": "ok"}

    def cmd_stop(self) -> dict:
        from .controller import stop_daemon
        stop_daemon()
        return {"type": "ok"}

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
    ) -> dict:
        """启动会话并等待触发（返回响应 dict）"""
        _logger.info("cmd_exec: id=%r pty=%s force=%s shell=%s",
                     session_id, pty, force, shell)
        timeout, newline = self._apply_config_defaults(
            timeout=timeout, newline=newline,
        )

        if pty and shell:
            return {
                "type": "error",
                "error": ("--pty 与 --shell 不能同时使用。\n"
                          "  --pty 将命令拆为列表直接在终端执行，不经过 shell。\n"
                          "  → 去掉 --pty 使用 --shell 指定解释器\n"
                          "  → 或去掉 --shell 使用 --pty 的完整终端"),
            }
        if pty and isinstance(command, str):
            if _has_shell_operators(command):
                if not force:
                    return {
                        "type": "error",
                        "error": (
                            "--pty 模式下命令中包含 shell 操作符 (| & > < && || ;)，"
                            "这些操作符依赖 shell 解析，在真实终端下无效。\n"
                            "  → 去掉 --pty 使用默认 subprocess 模式\n"
                            "  → 或加 --force-pty-mode 强制执行"
                        ),
                    }
                _logger.warning("--force-pty-mode: 忽略 shell 操作符检测，原样拆分")
            command = shlex.split(command)

        msg = {
            "type": "exec", "id": session_id, "command": command,
            "newline": newline, "fresh": fresh, "full": full,
            "timeout": timeout, "pty": pty,
        }
        if trigger is not None:
            msg["trigger"] = trigger
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if shell is not None:
            msg["shell"] = shell
        msg["cwd"] = cwd if cwd is not None else os.getcwd()

        return self._send(msg)

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
        send_eol: Optional[str] = None,
    ) -> dict:
        """向运行中的会话发送输入并等待触发（返回响应 dict）"""
        _logger.info("cmd_send: id=%r trigger=%r timeout=%s",
                     session_id, trigger, timeout)
        timeout, newline = self._apply_config_defaults(
            timeout=timeout, newline=newline,
        )
        eol_name = send_eol or self._config.get("send_eol")
        eol = resolve_eol(eol_name)
        _logger.debug("cmd_send: send_eol=%r eol=%r", eol_name, eol)

        msg = {
            "type": "send", "id": session_id,
            "input": process_input(input_text, eol=eol),
            "newline": newline, "fresh": fresh, "full": full,
            "timeout": timeout,
        }
        if trigger is not None:
            msg["trigger"] = trigger
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output

        return self._send(msg)

    def cmd_read(
        self,
        session_id: str,
        lines: Optional[str] = None,
        grep: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """读取会话输出（返回响应 dict）"""
        _logger.info("cmd_read: id=%r lines=%s grep=%r full=%s",
                     session_id, lines, grep, full)

        msg = {
            "type": "read", "id": session_id,
            "full": full,
        }
        if lines is not None:
            msg["lines"] = lines
        if grep is not None:
            msg["grep"] = grep

        return self._send(msg)

    def cmd_list(self) -> dict:
        """列出所有会话（返回响应 dict）"""
        _logger.info("cmd_list")
        resp = self._send({"type": "list"})
        if resp.get("type") == "ok" and not resp.get("sessions"):
            resp["note"] = "无活跃会话"
        return resp

    def cmd_remove(self, session_id: str) -> dict:
        """移除指定会话（返回响应 dict）"""
        _logger.info("cmd_remove: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            return {"type": "error", "error": "invalid session id"}
        try:
            resp = self._send({"type": "remove", "id": session_id})
        except Exception as e:
            resp = {"type": "ok", "note": f"daemon not running ({e})"}
        if resp.get("type") == "ok":
            resp.setdefault("note", f"会话 {session_id} 已移除")
        return resp

    def cmd_closewin(self, session_id: str, hwnd: int) -> dict:
        """关闭指定 GUI 窗口（返回响应 dict）"""
        _logger.info("cmd_closewin: id=%r hwnd=0x%X", session_id, hwnd)
        return self._send({
            "type": "closewin",
            "id": session_id,
            "hwnd": hwnd,
        })
