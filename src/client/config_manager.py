"""客户端配置 — 纯内存，仅控制本次调用

配置只作用于客户端（每次 exec/send 调用时读取），不传递到守护进程。

命令行显式参数 > --default 设置的临时默认值 > 代码内置默认值

注意：不再持久化到文件，--default 只影响本次 CLI 调用。
"""

import logging
from typing import Optional, Any

_logger = logging.getLogger("pty-client")

# ── 默认值（代码内置）─
_DEFAULTS: dict = {
    "output_by_natural_language": False,
    "timeout": 120.0,
    "newline": False,
    "encoding": None,
    "keep_ansi": False,
    "debug": True,
}

# on/off -> bool 映射
_ON_OFF = {"on": True, "off": False}


class ConfigManager:
    """客户端配置管理器（纯内存，不持久化）

    支持通过 --default 在本次调用中临时覆盖默认值。
    配置优先级：命令行显式参数 > --default 覆盖值 > 代码内置默认值。
    """

    def __init__(self, overrides: Optional[dict] = None):
        """初始化配置管理器

        Args:
            overrides: 可选的临时覆盖值字典（来自 --default）。
        """
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
        """设置配置值（仅本次调用有效，不持久化）

        Args:
            key:   配置键名。
            value: 配置值（字符串形式的 on/off 自动转为 bool）。

        Raises:
            ValueError: 无效的配置键。
        """
        if key not in _DEFAULTS:
            raise ValueError(
                f"未知配置项: {key}，"
                f"可用: {', '.join(sorted(_DEFAULTS.keys()))}",
            )

        # on/off 字符串转为 bool
        if isinstance(value, str) and value.lower() in _ON_OFF:
            value = _ON_OFF[value.lower()]

        # timeout 转为 float
        if key == "timeout":
            value = float(value)

        # newline/keep_ansi/output_by_natural_language 转为 bool
        if key in ("newline", "keep_ansi", "output_by_natural_language", "debug"):
            if not isinstance(value, bool):
                value = bool(value)

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
    """格式化配置值为显示字符串

    Args:
        val: 配置值。

    Returns:
        可读的字符串。
    """
    if isinstance(val, bool):
        return "on" if val else "off"
    if val is None:
        return "(未设置)"
    return str(val)
