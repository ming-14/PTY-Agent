"""默认配置键与取值校验 — client（ConfigManager）与 daemon（set_default）共享

set-default / --default 的可用键与取值归一化规则统一在此实现：
- client 侧 ConfigManager.set 复用（--show-config / --default 校验）
- daemon 侧 set_default handler 复用（守护进程内存默认配置落点）
- 取值归一化（on/off→bool、timeout→float、send_eol 名称→字符、WxH 校验）只此一处，
  避免两侧漂移。
"""

from ..config.encoding import is_valid_encoding
from ..input.text import SEND_EOL_MAP

# set-default / --default 可用键（内部下划线形态；CLI 展示为连字符由调用方转换）
DEFAULT_KEYS = (
    "timeout",
    "newline",
    "keep_ansi",
    "encoding",
    "debug",
    "send_eol",
    "response_format",
    "svg_compression_level",
    "terminal_size",
    "shell",
)

# 内置默认值（ConfigManager 与 daemon set_default 共享的单一事实来源）
DEFAULT_VALUES: dict = {
    "timeout": 120.0,
    "newline": False,
    "encoding": None,
    "keep_ansi": False,
    "debug": False,
    "send_eol": "\r",
    "response_format": "stream",
    "svg_compression_level": 1,
    "terminal_size": "80x24",
    "shell": None,
}

# 可被 --default 设置的键（subset of DEFAULT_KEYS）
# shell 仅 set-default 可设（exec 会话创建时按需取用），--default 不支持 shell
DEFAULT_KEYS_DEFAULT_CMD = tuple(k for k in DEFAULT_KEYS if k != "shell")

# on/off -> bool 映射（set-default 命令行取值）
_ON_OFF = {"on": True, "off": False, "true": True, "false": False}


def normalize_key(key: str) -> str:
    """CLI 配置键（连字符）转内部键（下划线）"""
    return key.replace("-", "_")


def normalize_default_value(key: str, value):
    """校验并归一化默认配置取值

    Args:
        key:   内部键（下划线形态）。
        value: 原始取值（set-default 命令行为字符串）。

    Returns:
        归一化后的值（bool/float/str 等）。

    Raises:
        ValueError: 键未知或取值非法。
    """
    key = normalize_key(key)
    if key not in DEFAULT_KEYS:
        raise ValueError(
            f"Unknown config key: {key}, available: {', '.join(DEFAULT_KEYS)}"
        )

    # on/off 字符串转为 bool（所有键通用）
    if isinstance(value, str) and value.lower() in _ON_OFF:
        value = _ON_OFF[value.lower()]

    if key == "timeout":
        value = float(value)
    elif key in ("newline", "keep_ansi", "debug"):
        value = bool(value)
    elif key == "encoding":
        if value is not None and not is_valid_encoding(value):
            raise ValueError(
                f"Invalid encoding: {value!r}. "
                "Use a valid codec name (e.g. utf-8, gbk, cp936, latin-1) "
                "or leave it unset for auto detection."
            )
    elif key == "response_format":
        if value not in ("stream", "svg"):
            raise ValueError(
                f"Invalid response-format value: {value!r}, available: stream, svg"
            )
    elif key == "svg_compression_level":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid svg-compression-level value: {value!r}, available: 0, 1, 2"
            )
        if value not in (0, 1, 2):
            raise ValueError(
                f"Invalid svg-compression-level value: {value!r}, available: 0, 1, 2"
            )
    elif key == "send_eol":
        # 名称映射 -> 实际字符；或直接字符（\n / \r\n / \r / 空串）
        if isinstance(value, str) and value.lower() in SEND_EOL_MAP:
            value = SEND_EOL_MAP[value.lower()]
        elif isinstance(value, str) and value in ("\n", "\r\n", "\r", ""):
            pass
        else:
            valid = ", ".join(sorted(SEND_EOL_MAP.keys()))
            raise ValueError(
                f"Invalid send-eol value: {value!r}, "
                f"available names: {valid}; or use \\n / \\r\\n / \\r / empty string"
            )
    elif key == "terminal_size":
        # WxH 格式验证（20-500×5-200）
        if isinstance(value, str):
            value = value.lower().replace("×", "x")
        parts = str(value).split("x")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid terminal-size value: {value!r}, expected WxH (e.g. 120x40)"
            )
        try:
            c, r = int(parts[0]), int(parts[1])
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid terminal-size value: {value!r}, expected WxH (e.g. 120x40)"
            )
        if not (20 <= c <= 500 and 5 <= r <= 200):
            raise ValueError(
                f"Invalid terminal-size value: {value!r}, "
                f"cols must be 20-500, rows must be 5-200"
            )
        value = f"{c}x{r}"
    # shell：字符串或 None 原样接受（exec 会话创建时取用）

    return value
