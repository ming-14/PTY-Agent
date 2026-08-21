"""命令注册清单

register_all() 注册全部命令到 registry；注册顺序 = 帮助显示顺序。
"""

from .closewin import ClosewinCommand
from .events import EventsCommand
from .exec import ExecCommand
from .file import FileCommand
from .keygen import KeygenCommand
from .kill import KillCommand
from .list_ import ListCommand
from .mouse import MouseCommand
from .plugin import PluginCommand
from .read import ReadCommand
from .send import AdvSendCommand, SendCommand
from .set_default import SetDefaultCommand
from .start import StartCommand
from .status import StatusCommand
from .stop import StopCommand
from .wait import WaitCommand
from .workflow import WorkflowCommand


def register_all(registry) -> None:
    """按顺序注册全部命令"""
    registry.register(SetDefaultCommand())
    registry.register(StartCommand())
    registry.register(StopCommand())
    registry.register(StatusCommand())
    registry.register(ListCommand())
    registry.register(ExecCommand())
    registry.register(SendCommand())
    registry.register(AdvSendCommand())
    registry.register(ReadCommand())
    registry.register(KillCommand())
    registry.register(EventsCommand())
    registry.register(ClosewinCommand())
    registry.register(MouseCommand())
    registry.register(WaitCommand())
    registry.register(KeygenCommand())
    registry.register(PluginCommand())
    registry.register(WorkflowCommand())
    registry.register(FileCommand())
