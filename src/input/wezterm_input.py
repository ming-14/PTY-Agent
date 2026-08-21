"""wezterm-py 模式感知输入编码适配器

包装 wezterm-py.Terminal（与终端模型共享同一实例，模式状态一致），
提供键盘/鼠标事件的模式感知编码，返回应写入 pty 的字节。

编码结果直接写 ConPTY 输入管道（OpenConsole 处理），无需进程注入。

键盘/鼠标事件参数与 wezterm-input-types 对齐：
- key:  按键名（"a"/"Up"/"F1"/"Enter"...，见 pywezterm.parse_keycode）
- mods: KeyModifiers 位掩码（SHIFT=1<<1, ALT=1<<2, CTRL=1<<3, SUPER=1<<4）
- mouse: x/y 为 0-based 单元格坐标；kind ∈ press/release/move；
         button ∈ left/middle/right/wheel_up/wheel_down
"""

from ..logging import get_logger

_logger = get_logger("pty-session")

# wezterm KeyModifiers 位定义（与 wezterm-input-types Modifiers 一致）
MOD_SHIFT = 1 << 1
MOD_ALT = 1 << 2
MOD_CTRL = 1 << 3

# 鼠标事件类型 / 按钮（与 pywezterm parse_mouse_kind/parse_mouse_button 一致）
MOUSE_KINDS = ("press", "release", "move")
MOUSE_BUTTONS = ("left", "middle", "right", "wheel_up", "wheel_down", "none")


class WeztermInputEncoder:
    """基于 wezterm-py Terminal 的模式感知输入编码器

    Args:
        terminal: wezterm-py.Terminal 实例（通常来自 TerminalScreen.emulator，
                  与终端模型共享，模式状态由 feed 实时更新）。
    """

    def __init__(self, terminal):
        self._term = terminal

    @property
    def available(self) -> bool:
        """wezterm 终端模型可用（True 才能做模式感知编码）"""
        return self._term is not None

    @property
    def terminal(self):
        return self._term

    def key_down(self, key: str, mods: int = 0) -> bytes:
        """键盘按下编码（模式感知：应用光标模式/kitty/CSI-u/win32）

        Returns:
            应写入 pty 的字节；编码失败或不可用时返回 b""。
        """
        if not self.available:
            return b""
        try:
            return self._term.key_down(key, mods)
        except Exception as e:
            _logger.warning("key_down 编码失败: key=%r mods=%d err=%s", key, mods, e)
            return b""

    def key_up(self, key: str, mods: int = 0) -> bytes:
        """键盘抬起编码（win32 输入模式等需要 keyup 时使用）"""
        if not self.available:
            return b""
        try:
            return self._term.key_up(key, mods)
        except Exception as e:
            _logger.warning("key_up 编码失败: key=%r mods=%d err=%s", key, mods, e)
            return b""

    def mouse(
        self, x: int, y: int, kind: str = "press", button: str = "left", mods: int = 0
    ) -> bytes:
        """鼠标事件编码（模式感知：SGR/kitty/urxvt；未启用上报时返回 b""）"""
        if not self.available:
            return b""
        if kind not in MOUSE_KINDS:
            _logger.warning("mouse 未知事件类型: %r", kind)
            return b""
        if button not in MOUSE_BUTTONS:
            _logger.warning("mouse 未知按钮: %r", button)
            return b""
        try:
            return self._term.mouse(x, y, kind, button, mods)
        except Exception as e:
            _logger.warning(
                "mouse 编码失败: x=%d y=%d kind=%r btn=%r mods=%d err=%s",
                x,
                y,
                kind,
                button,
                mods,
                e,
            )
            return b""
