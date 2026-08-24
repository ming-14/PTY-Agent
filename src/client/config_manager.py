"""客户端配置 — 会话级内存存储（守护进程侧）

--default 设置的值通过 client_defaults 字段发送给守护进程，按 session UID 存储。
配置生命周期与活跃会话绑定，会话结束后自动清理。

配置优先级：命令行显式参数 > --default 覆盖值 > 代码内置默认值
"""

from ..input.text import SEND_EOL_MAP
from ..logging import get_logger
import json
import os
from typing import Any, Optional

_logger = get_logger("pty-client")

_DEFAULTS: dict = {
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

# 会话级默认配置持久化文件（set-default 写入，所有 CLI 调用启动时加载）。
# 路径直接用 expanduser 计算（与 config/common.py 的 DATA_DIR 一致），
# 避免模块级相对导入 —— 本模块须可被独立加载（单元测试用 importlib）。


def _persistent_defaults_file() -> str:
    return os.path.join(
        os.path.expanduser("~"), ".pty-agent", "client_defaults.json"
    )


def load_persistent_defaults() -> dict:
    """读取会话级默认配置持久化文件（set-default 写入，启动时加载）

    返回值在每次 CLI 调用经 client_defaults 随会话命令发送到守护进程
    （与 --default 同一机制）。

    Returns:
        持久化配置字典；文件缺失/损坏时返回空字典。
    """
    try:
        with open(_persistent_defaults_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_persistent_defaults(defaults: dict) -> None:
    """写入会话级默认配置到持久化文件

    Args:
        defaults: 要持久化的配置字典（仅含用户 set-default 覆盖项）。
    """
    try:
        path = _persistent_defaults_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
    except OSError as e:
        _logger.warning("写入默认配置失败: %s", e)


# on/off -> bool 映射
_ON_OFF = {"on": True, "off": False, "true": True, "false": False}


def load_persistent_defaults() -> dict:
    """读取全局默认配置持久化文件（set-default 写入，启动时加载）

    Returns:
        持久化配置字典；文件缺失/损坏时返回空字典。
    """
    try:
        with open(_persistent_defaults_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_persistent_defaults(defaults: dict) -> None:
    """写入会话级默认配置到持久化文件

    Args:
        defaults: 要持久化的配置字典（仅含用户 set-default 覆盖项）。
    """
    try:
        path = _persistent_defaults_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
    except OSError as e:
        _logger.warning("写入默认配置失败: %s", e)


class ConfigManager:
    """客户端配置管理器

    支持 --default / set-default 设置会话级默认值，经 client_defaults 发送到
    守护进程按 session UID 存储（与 --default 同一机制）。set-default 额外
    持久化到本地文件，后续所有 CLI 调用自动加载携带。
    配置优先级：命令行显式参数 > set-default 持久化默认 > 代码内置默认值。
    """

    def __init__(self, overrides: Optional[dict] = None):
        self._config = dict(_DEFAULTS)
        # 加载 set-default 持久化默认（与 --default 同一 client_defaults 机制），
        # 再叠加单次调用覆盖
        persistent = load_persistent_defaults()
        for k, v in persistent.items():
            if k in _DEFAULTS:
                try:
                    self.set(k, v)
                except ValueError:
                    _logger.warning("忽略无效的持久化默认配置 %s=%r", k, v)
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
        if key in ("newline", "keep_ansi", "debug"):
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

        # send_eol: 名称映射 -> 实际字符
        if key == "send_eol":
            if isinstance(value, str) and value.lower() in SEND_EOL_MAP:
                value = SEND_EOL_MAP[value.lower()]
            elif isinstance(value, str) and value in ("\n", "\r\n", "\r", ""):
                pass
            else:
                valid = ", ".join(sorted(SEND_EOL_MAP.keys()))
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
