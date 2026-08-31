"""共有配置 —— Daemon 与 Client 均需使用的常量

来源: common.toml + 运行时计算属性
"""

import os
import sys
from typing import Optional

from ._loader import apply_env_overrides, expand_env, flatten, load_toml, merge

# 环境变量覆写（PTY_AGENT_<key>，优先级高于文件）：先覆写再算 DATA_DIR，
# 保证 PTY_AGENT_DATA_DIR 经 ~/%VAR% 展开后同样生效
_all = apply_env_overrides(merge(flatten(load_toml("common.toml"))))

_all["IS_WINDOWS"] = sys.platform == "win32"
# DATA_DIR 来自 common.toml [paths]：支持 ~ 与 %VAR%/$VAR 展开，空值回落默认家目录；
# normpath 归一化分隔符（TOML 中 ~ 展开后可能混用 / 与 \）
_all["DATA_DIR"] = os.path.normpath(
    expand_env(
        _all.get("DATA_DIR") or os.path.join(os.path.expanduser("~"), ".pty-agent")
    )
)
_all["PROJECT_ROOT"] = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

globals().update(_all)
__all__ = list(_all.keys())


def parse_terminal_size(
    size_str: str,
    *,
    min_cols: int = 1,
    min_rows: int = 1,
    max_cols: Optional[int] = None,
    max_rows: Optional[int] = None,
) -> tuple:
    """解析终端尺寸字符串 WxH → (cols, rows)

    统一的 WxH 解析实现（支持 × 分隔符、大小写），边界可参数化；
    消除 client/daemon/workflow 三处复制导致的语义漂移。默认仅要求
    正整数底线，上界由调用方按需传入（如 client 严格 20-500×5-200）。

    Args:
        size_str: 如 "120x40" 或 "80×24" 或 "80X24"。
        min_cols/min_rows: 该维正整数下界。
        max_cols/max_rows: 该维上界（None 表示不限）。

    Returns:
        (cols, rows) 整数元组。

    Raises:
        ValueError: 格式非法，或任一侧低于下界/超出上界。
    """
    s = str(size_str).lower().replace("×", "x")
    parts = s.split("x")
    if len(parts) != 2:
        raise ValueError(f"Invalid terminal-size format: {size_str!r}, expected WxH")
    try:
        cols, rows = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid terminal-size format: {size_str!r}, expected WxH")
    if cols < min_cols or rows < min_rows:
        raise ValueError(
            f"terminal-size out of range: {size_str!r}, below min ({min_cols}x{min_rows})"
        )
    if max_cols is not None and cols > max_cols:
        raise ValueError(
            f"terminal-size out of range: {size_str!r}, above max ({max_cols}x{max_rows})"
        )
    if max_rows is not None and rows > max_rows:
        raise ValueError(
            f"terminal-size out of range: {size_str!r}, above max ({max_cols}x{max_rows})"
        )
    return cols, rows
