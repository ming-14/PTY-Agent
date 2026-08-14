"""客户端子包 — TCP 连接管理、配置、输入处理与响应格式化"""

from .config_manager import ConfigManager
from .input import process_input, safe_print, unescape_json_string
from .transport import Client

__all__ = [
    "Client",
    "ConfigManager",
    "process_input",
    "safe_print",
    "unescape_json_string",
]
