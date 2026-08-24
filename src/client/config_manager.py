"""客户端配置 — 会话级内存存储（守护进程侧）与本次调用配置（CLI 侧）

- --default 设置的值通过 client_defaults 字段发送给守护进程，按 session UID 存储。
- set-default 的全局默认存于**守护进程内存**（daemon 重启即清空，不写任何文件），
  CLI 启动时经 get_defaults 拉取合并到本次调用配置。
- 配置优先级：命令行显式参数 > --default 覆盖值 > set-default 全局默认 > 代码内置默认值
"""

from ..config.default_keys import (
    DEFAULT_VALUES,
    normalize_default_value,
)
from ..logging import get_logger
from typing import Any, Optional

_logger = get_logger("pty-client")


class ConfigManager:
    """客户端配置管理器（单次调用内存态）

    优先级：命令行显式参数 > --default 覆盖值 > set-default 全局默认（daemon 内存）>
    内置默认值。set-default 全局默认由 CLI 启动时 get_defaults 拉取后合入，
    不依赖任何本地持久化文件。
    """

    def __init__(self, overrides: Optional[dict] = None):
        self._config = dict(DEFAULT_VALUES)
        if overrides:
            for k, v in overrides.items():
                try:
                    self.set(k, v)
                except ValueError:
                    _logger.warning("忽略无效的配置覆盖 %s=%r", k, v)

    # ── 读取 ──

    def get(self, key: str) -> Any:
        """获取指定配置值

        Args:
            key: 配置键名。

        Returns:
            配置值。未设置时返回内置默认值。
        """
        return self._config.get(key, DEFAULT_VALUES.get(key))

    def get_all(self) -> dict:
        """获取全部配置

        Returns:
            完整配置字典。
        """
        return dict(self._config)

    def set(self, key: str, value: Any):
        if key not in DEFAULT_VALUES:
            raise ValueError(
                f"Unknown config key: {key}, "
                f"available: {', '.join(sorted(DEFAULT_VALUES.keys()))}",
            )
        value = normalize_default_value(key, value)
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
            if key not in DEFAULT_VALUES:
                return f"未知配置项: {key}"
            val = self._config.get(key, DEFAULT_VALUES[key])
            return f"{key} = {_format_value(val)}"

        lines = []
        for k in sorted(DEFAULT_VALUES.keys()):
            val = self._config.get(k, DEFAULT_VALUES[k])
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
    """解析终端尺寸字符串 WxH → (cols, rows)（客户端严格边界 20-500×5-200）

    委托共享实现 config.common.parse_terminal_size 并传入严格边界，
    统一 WxH 解析核心，避免与 daemon/workflow 复制漂移。

    Args:
        size_str: 如 "120x40" 或 "80x24"。

    Returns:
        (cols, rows) 整数元组。

    Raises:
        ValueError: 格式无效或超出范围。
    """
    from ..config.common import parse_terminal_size as _core

    return _core(
        size_str,
        min_cols=20,
        min_rows=5,
        max_cols=500,
        max_rows=200,
    )
