"""可用 Shell 列表提供者实现。"""


from ...application.ports import ShellProvider
from ....logging import get_logger

_logger = get_logger("pty-web")


class ShellProviderImpl(ShellProvider):
    """检测系统可用 Shell。"""

    def list_shells(self) -> dict:
        try:
            from ....common.shells import detect_available_shells

            return detect_available_shells()
        except Exception as e:
            _logger.warning("detect_available_shells failed: %s", e)
            return {}

    def default_cwd(self) -> str:
        """守护进程当前工作目录。"""
        import os

        return os.getcwd()
