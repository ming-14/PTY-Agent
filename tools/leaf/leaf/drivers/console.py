"""控制台驱动：pywezterm.ConsoleInput 门面，绑定归一化事件 → 领域事件。

- 控制台模式设置（输出侧 VT 处理/禁用换行自动回车，输入侧原始按键事件）、
  事件采集与归一化全部由 pywezterm.ConsoleInput 完成（原 ctypes 实现已
  下沉绑定层），本模块只做归一化 tuple → 领域事件薄映射；
- 用例层仍只消费领域事件（KeyEvent/MouseEvent/ResizeEvent），不接触任何
  Win32 结构。
"""

import logging
from typing import List

from leaf.drivers import _engine
from leaf.domain.events import KeyEvent, MouseEvent, ResizeEvent
from leaf.usecases.ports import InputEvent

log = logging.getLogger("leaf.console")


def _to_domain(ev) -> InputEvent:
    """绑定归一化事件 tuple → 领域事件。

    tuple 形状：("key", key, mods, down) / ("mouse", x, y, kind, button, mods, count) / ("resize",)
    """
    kind = ev[0]
    if kind == "key":
        return KeyEvent(ev[1], ev[2], ev[3])
    if kind == "mouse":
        return MouseEvent(ev[1], ev[2], ev[3], ev[4], ev[5], ev[6] if len(ev) > 6 else 1)
    return ResizeEvent()


class Console:
    """控制台封装：模式设置、输入事件读取、窗口尺寸查询（ConsolePort 实现）"""

    def __init__(self):
        _engine.ensure_engine()
        import pywezterm

        self._ci = pywezterm.ConsoleInput()
        log.info("console mode set via pywezterm.ConsoleInput")

    def restore(self) -> None:
        """恢复控制台原始模式（退出时调用）"""
        self._ci.restore()

    def wait_input(self, ms: int) -> bool:
        """等待输入事件可用，返回是否有事件（False=超时）"""
        return self._ci.wait_input(ms)

    def read_inputs(self) -> List[InputEvent]:
        """读取全部待处理输入事件并归一化为领域事件列表"""
        return [_to_domain(ev) for ev in self._ci.read_inputs()]

    def size(self):
        """当前窗口逻辑尺寸 (cols, rows)"""
        return self._ci.size()
