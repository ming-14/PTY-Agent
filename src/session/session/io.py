"""Session 输入混入 — InputMixin

负责会话的输入路径：文本/键盘/鼠标写入、信号发送与鼠标动作执行。
输出与屏幕快照见 output.py，触发等待见 trigger.py。
所有方法均通过 Session 实例访问子组件（见 session.py 的 __init__）。
"""

import os

from ...config.common import IS_WINDOWS
from ._win_console import send_ctrl_c
from ...logging import get_logger

_logger = get_logger("pty-session")


class InputMixin:
    """输入写入与信号（会话组合的输入部分）"""

    def write_input(self, data):
        """写入输入到 PTY / 子进程 stdin

        通过 InputInterceptor 拦截 SGR 鼠标序列和键盘 VT 序列后写入（pty 模式）；
        子进程模式无终端拦截，直接写入 stdin。

        Args:
            data: 要写入的数据（str 或 bytes）。

        Raises:
            RuntimeError: 会话未运行或写入失败。
            TypeError:    data 类型不正确。
        """
        if not self._pty or not self.running:
            raise RuntimeError(f"会话 '{self.id}' 未运行")
        if not isinstance(data, (str, bytes)):
            raise TypeError(
                f"输入数据必须是 str 或 bytes, 收到 {type(data).__name__}",
            )
        _logger.debug(
            "write_input: sid=%r len=%d data=%r",
            self.id,
            len(data),
            data[:200] if isinstance(data, str) else data[:200],
        )

        if self._input_interceptor is not None:
            data = self._input_interceptor.intercept(
                data, self._child_encoding, self.encoding, self.id
            )

        if not self._dispatch_input(data):
            _logger.info("write_input: sid=%r 输入被插件拦截", self.id)

    def _dispatch_input(self, data) -> bool:
        """统一输入分发：插件链变换后写入 PTY

        供 write_input/key_input/key_up/mouse_input 共用，保证所有输入
        路径都经过插件 on_input 链。

        Returns:
            True 已写入；False 被插件拦截丢弃。
        """
        data = self.plugin_host.on_input(data)
        if data is None:
            return False
        if data:
            try:
                self._pty.write(data)
            except Exception as e:
                _logger.error("写入输入失败 (会话 '%s'): %s", self.id, e)
                raise RuntimeError(f"写入输入失败: {e}") from e
        return True

    # ── wezterm 模式感知输入（键盘/鼠标事件 → 编码字节 → 直接写 pty）──

    def key_input(self, key: str, mods: int = 0) -> None:
        """模式感知键盘按下编码并写入 PTY

        由 wezterm-py Terminal 按终端当前状态（应用光标模式/kitty/CSI-u/win32）
        编码为对应 VT 序列并直接写入 pty。

        Args:
            key:  按键名（"a"/"Up"/"F1"/"Enter"...）。
            mods: KeyModifiers 位掩码（SHIFT=1<<1, ALT=1<<2, CTRL=1<<3, SUPER=1<<4）。
        """
        if getattr(self, "mode", "pty") == "subprocess":
            raise RuntimeError("子进程模式无终端，不支持键盘编码输入")
        if not self._pty or not self.running:
            raise RuntimeError(f"会话 '{self.id}' 未运行")
        data = self._input_encoder.key_down(key, mods)
        if data and not self._dispatch_input(data):
            _logger.info("key_input: sid=%r 输入被插件拦截", self.id)

    def key_up(self, key: str, mods: int = 0) -> None:
        """模式感知键盘抬起编码并写入 PTY（win32 输入模式等需要 keyup 时）"""
        if getattr(self, "mode", "pty") == "subprocess":
            raise RuntimeError("子进程模式无终端，不支持键盘编码输入")
        if not self._pty or not self.running:
            raise RuntimeError(f"会话 '{self.id}' 未运行")
        data = self._input_encoder.key_up(key, mods)
        if data and not self._dispatch_input(data):
            _logger.info("key_up: sid=%r 输入被插件拦截", self.id)

    def mouse_input(
        self, x: int, y: int, kind: str = "press", button: str = "left", mods: int = 0
    ) -> None:
        """模式感知鼠标事件编码并写入 PTY

        坐标 x/y 为 0-based 单元格；未启用鼠标上报时编码为空（不写入）。
        """
        if getattr(self, "mode", "pty") == "subprocess":
            raise RuntimeError("子进程模式无终端，不支持鼠标输入")
        if not self._pty or not self.running:
            raise RuntimeError(f"会话 '{self.id}' 未运行")
        data = self._input_encoder.mouse(x, y, kind, button, mods)
        if data and not self._dispatch_input(data):
            _logger.info("mouse_input: sid=%r 输入被插件拦截", self.id)

    def send_signal(self, sig: str):
        """向子进程发送信号（如 SIGINT）

        通过 os.kill / GenerateConsoleCtrlEvent 等方式直接发送信号到子进程。
        """
        if not self._pty or not self.running:
            return
        pid = self._pty.get_child_pid()
        if pid is None:
            return
        import signal as _signal

        if sig == "SIGINT":
            try:
                if getattr(self, "mode", "pty") == "subprocess":
                    # 子进程模式：直接向 Popen 进程发送 SIGINT
                    self._pty.send_signal(_signal.SIGINT)
                    _logger.info(
                        "send_signal: sid=%r SIGINT pid=%d (subprocess)", self.id, pid
                    )
                elif IS_WINDOWS:
                    send_ctrl_c(self._pty, pid, self.id)
                else:
                    # Unix：向整个进程组广播 SIGINT（对齐 Windows 控制台广播语义），
                    # 仅杀 root 无法送达其子进程；pgid 获取失败回退单进程
                    pgid = getattr(self.tracker, "pgid", None)
                    if pgid:
                        try:
                            os.killpg(pgid, _signal.SIGINT)
                            _logger.info(
                                "send_signal: sid=%r SIGINT pgid=%d (os.killpg)",
                                self.id,
                                pgid,
                            )
                            return
                        except OSError as e:
                            _logger.debug(
                                "send_signal: killpg failed pgid=%d err=%s, "
                                "fallback os.kill pid",
                                pgid,
                                e,
                            )
                    os.kill(pid, _signal.SIGINT)
                    _logger.info(
                        "send_signal: sid=%r SIGINT pid=%d (os.kill)", self.id, pid
                    )
            except Exception as e:
                _logger.warning(
                    "send_signal failed: sid=%r sig=%s pid=%d err=%s",
                    self.id,
                    sig,
                    pid,
                    e,
                )
        else:
            _logger.warning("send_signal: unsupported sig=%s", sig)

    def perform_mouse_action(self, action: dict) -> dict:
        """执行鼠标动作（委托给 InputInterceptor）"""
        return self._input_interceptor.perform_mouse_action(
            action,
            self._screen,
            self.pty_type,
            self.id,
            self.running,
            write_fn=self.write_input,
        )
