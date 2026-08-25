"""领域事件：Win32 原始记录经适配层归一化后的值对象。

用例层只消费这些领域事件，不接触任何 Win32 结构类型。
"""

from dataclasses import dataclass

# pywezterm KeyModifiers 位定义（wezterm-input-types）：SHIFT=2 ALT=4 CTRL=8
MOD_SHIFT = 2
MOD_ALT = 4
MOD_CTRL = 8


@dataclass(frozen=True)
class KeyEvent:
    """一次按键（已按 pywezterm 语义归一）"""

    key: str
    mods: int
    down: bool


@dataclass(frozen=True)
class MouseEvent:
    """一次鼠标动作，按 pywezterm mouse() 语义归一。

    kind: "press" | "move" | "release"
    button: "left" | "right" | "middle" | "none" | "wheel_up" | "wheel_down"
    count: 点击次数（1=单击，2=双击，3=三击；仅 press 有意义，其他事件恒为 1）
    """

    x: int
    y: int
    kind: str
    button: str
    mods: int
    count: int = 1


@dataclass(frozen=True)
class ResizeEvent:
    """宿主窗口尺寸变化标记；新尺寸由 ConsolePort.size() 实时查询。"""
