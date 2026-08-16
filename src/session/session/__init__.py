"""Session 类实现子包 — 协调器基类与功能混入

- `session.py`     Session 基类（协调器：子组件装配 + 生命周期 + 状态代理）
- `io.py`          InputMixin（输入写入/信号/鼠标动作）
- `output.py`      OutputMixin（输出读取/屏幕快照/resize/终端状态）
- `trigger.py`     TriggerMixin（触发条件与等待）
- `events.py`      EventsMixin（事件接收/历史/退出回调）
- `threads.py`     Threads + Components（后台读者/监控线程管理）
- `_win_console.py` Windows Ctrl+C 控制台辅助
"""

from .session import Session
from .io import InputMixin
from .output import OutputMixin
from .trigger import TriggerMixin
from .events import EventsMixin
from .threads import Threads, Components

__all__ = [
    "Session",
    "InputMixin",
    "OutputMixin",
    "TriggerMixin",
    "EventsMixin",
    "Threads",
    "Components",
]
