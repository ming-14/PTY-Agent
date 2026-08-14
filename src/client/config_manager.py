"""客户端配置 — 会话级内存存储（守护进程侧）

--default 设置的值通过 client_defaults 字段发送给守护进程，按 session UID 存储。
配置生命周期与活跃会话绑定，会话结束后自动清理。

配置优先级：命令行显式参数 > --default 覆盖值 > 代码内置默认值
"""

import logging
from typing import Any, Optional

_logger = logging.getLogger("pty-client")

_DEFAULTS: dict = {
    "timeout": 120.0,
    "newline": False,
    "encoding": None,
    "keep_ansi": False,
    "debug": True,
    "send_eol": "\r",
    "always_return_snapshot": False,
    "response_format": "stream",
    "svg_compression_level": 1,
    "terminal_size": "80x24",
    "ai_analyse": "none",
    "ai_prompt": "全面分析该内容，只按内容说话，不给出下一步，不提建议",
}

_SEND_EOL_MAP: dict = {
    "lf": "\n",
    "crlf": "\r\n",
    "cr": "\r",
    "none": "",
}

# on/off -> bool 映射
_ON_OFF = {"on": True, "off": False, "true": True, "false": False}


class ConfigManager:
    """客户端配置管理器

    支持 --default 设置会话级默认值，发送到守护进程按 session UID 存储。
    配置优先级：命令行显式参数 > --default 覆盖值 > 代码内置默认值。
    """

    def __init__(self, overrides: Optional[dict] = None):
        self._config = dict(_DEFAULTS)
        if overrides:
            self._config.update(overrides)

    # ── 读取 ──

    def get(self, key: str) -> Any:
        """获取指定配置值

        Args:
            key: 配置键名。

        Returns:
            配置值。未设置时返回内置默认值。
        """
        return self._config.get(key, _DEFAULTS.get(key))

    def get_all(self) -> dict:
        """获取全部配置

        Returns:
            完整配置字典。
        """
        return dict(self._config)

    def set(self, key: str, value: Any):
        if key not in _DEFAULTS:
            raise ValueError(
                f"Unknown config key: {key}, "
                f"available: {', '.join(sorted(_DEFAULTS.keys()))}",
            )

        # on/off 字符串转为 bool
        if isinstance(value, str) and value.lower() in _ON_OFF:
            value = _ON_OFF[value.lower()]

        # timeout 转为 float
        if key == "timeout":
            value = float(value)

        # newline/keep_ansi/debug 转为 bool
        if key in ("newline", "keep_ansi", "debug", "always_return_snapshot"):
            if not isinstance(value, bool):
                value = bool(value)

        if key == "response_format":
            if isinstance(value, str) and value not in ("stream", "svg"):
                raise ValueError(
                    f"Invalid response-format value: {value!r}, available: stream, svg",
                )

        if key == "svg_compression_level":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid svg-compression-level value: {value!r}, "
                    f"available: 0, 1, 2",
                )
            if value not in (0, 1, 2):
                raise ValueError(
                    f"Invalid svg-compression-level value: {value!r}, "
                    f"available: 0, 1, 2",
                )

        # ai_analyse: AI 分析模式（none/fileOutput/responseOutput）
        if key == "ai_analyse":
            if value not in ("none", "fileOutput", "responseOutput"):
                raise ValueError(
                    f"Invalid ai-analyse value: {value!r}, "
                    f"available: none, fileOutput, responseOutput",
                )

        # ai_prompt: 分析提示词（非空字符串）
        if key == "ai_prompt":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Invalid ai-prompt value: {value!r}, expected non-empty string",
                )

        # send_eol: 名称映射 -> 实际字符
        if key == "send_eol":
            if isinstance(value, str) and value.lower() in _SEND_EOL_MAP:
                value = _SEND_EOL_MAP[value.lower()]
            elif isinstance(value, str) and value in ("\n", "\r\n", "\r", ""):
                pass
            else:
                valid = ", ".join(sorted(_SEND_EOL_MAP.keys()))
                raise ValueError(
                    f"Invalid send-eol value: {value!r}, "
                    f"available names: {valid}; or use \\n / \\r\\n / \\r / empty string",
                )

        # terminal_size: WxH 格式验证
        if key == "terminal_size":
            if isinstance(value, str):
                value = value.lower().replace("×", "x")
            parts = str(value).split("x")
            if len(parts) != 2:
                raise ValueError(
                    f"Invalid terminal-size value: {value!r}, expected WxH (e.g. 120x40)",
                )
            try:
                c, r = int(parts[0]), int(parts[1])
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid terminal-size value: {value!r}, expected WxH (e.g. 120x40)",
                )
            if not (20 <= c <= 500 and 5 <= r <= 200):
                raise ValueError(
                    f"Invalid terminal-size value: {value!r}, "
                    f"cols must be 20-500, rows must be 5-200",
                )
            value = f"{c}x{r}"

        _logger.debug("ConfigManager.set: %s=%r", key, value)
        self._config[key] = value

    # ── 展示 ──

    def show(self, key: Optional[str] = None) -> str:
        """生成配置展示文本

        Args:
            key: 可选，展示指定配置项。None 表示展示全部。

        Returns:
            格式化的配置文本。
        """
        if key is not None:
            if key not in _DEFAULTS:
                return f"未知配置项: {key}"
            val = self._config.get(key, _DEFAULTS[key])
            return f"{key} = {_format_value(val)}"

        lines = []
        for k in sorted(_DEFAULTS.keys()):
            val = self._config.get(k, _DEFAULTS[k])
            lines.append(f"  {k} = {_format_value(val)}")
        return "当前调用配置:\n" + "\n".join(lines)


def _format_value(val: Any) -> str:
    """格式化配置值为显示字符串"""
    if isinstance(val, bool):
        return "on" if val else "off"
    if val is None:
        return "(未设置)"
    if val == "\n":
        return "lf (\\n)"
    if val == "\r\n":
        return "crlf (\\r\\n)"
    if val == "\r":
        return "cr (\\r)"
    if val == "":
        return "none (不追加)"
    return str(val)


def parse_terminal_size(size_str: str) -> tuple:
    """解析终端尺寸字符串 WxH → (cols, rows)

    Args:
        size_str: 如 "120x40" 或 "80x24"

    Returns:
        (cols, rows) 整数元组

    Raises:
        ValueError: 格式无效或超出范围
    """
    s = str(size_str).lower().replace("×", "x")
    parts = s.split("x")
    if len(parts) != 2:
        raise ValueError(f"Invalid terminal-size format: {size_str!r}, expected WxH")
    c, r = int(parts[0]), int(parts[1])
    if not (20 <= c <= 500 and 5 <= r <= 200):
        raise ValueError(
            f"terminal-size out of range: {size_str!r}, cols 20-500, rows 5-200"
        )
    return c, r
