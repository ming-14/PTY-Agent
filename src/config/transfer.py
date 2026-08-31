"""文件传输协议配置 —— 加载 transfer.toml 供传输两端（daemon/CLI）使用

来源: transfer.toml
消费方: src/protocol/transfer.py（帧协议）、src/transfer/（CLI 驱动）、
src/client/transport.py（CLI 超时）、src/files/transfer/（daemon 侧传输业务）。
"""

from ._loader import apply_env_overrides, flatten, load_toml

# 展平后统一经环境变量覆写（PTY_AGENT_TRANSFER_* 覆盖文件值）
_cfg = apply_env_overrides(flatten(load_toml("transfer.toml")))

TRANSFER_CHUNK_SIZE = int(_cfg["TRANSFER_CHUNK_SIZE"])
TRANSFER_MAX_FILES = int(_cfg["TRANSFER_MAX_FILES"])
TRANSFER_MAX_CONTROL = int(_cfg["TRANSFER_MAX_CONTROL"])
TRANSFER_MAX_SIZE = int(_cfg["TRANSFER_MAX_SIZE"])
TRANSFER_TMP_SUFFIX = str(_cfg["TRANSFER_TMP_SUFFIX"])
TRANSFER_PROGRESS_INTERVAL = float(_cfg["TRANSFER_PROGRESS_INTERVAL"])
TRANSFER_TIMEOUT = float(_cfg["TRANSFER_TIMEOUT"])

__all__ = [
    "TRANSFER_CHUNK_SIZE",
    "TRANSFER_MAX_CONTROL",
    "TRANSFER_MAX_FILES",
    "TRANSFER_MAX_SIZE",
    "TRANSFER_PROGRESS_INTERVAL",
    "TRANSFER_TIMEOUT",
    "TRANSFER_TMP_SUFFIX",
]
