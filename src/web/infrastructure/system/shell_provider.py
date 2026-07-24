"""可用 Shell 列表提供者实现。"""

import logging

from ...application.ports import ShellProvider

_logger = logging.getLogger("pty-web")


class ShellProviderImpl(ShellProvider):
    """检测系统可用 Shell。"""

    def list_shells(self) -> dict:
        try:
            from ....pty import detect_available_shells

            return detect_available_shells()
        except Exception as e:
            _logger.warning("detect_available_shells failed: %s", e)
            return {}
