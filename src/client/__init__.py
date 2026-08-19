"""客户端子包 — TCP 连接管理、配置、输入处理与响应格式化"""

from .config_manager import ConfigManager
from .input import safe_print
from .transport import Client
from ..input.text import process_input, unescape_json_string

__all__ = [
    "Client",
    "ConfigManager",
    "process_input",
    "safe_print",
    "unescape_json_string",
]
