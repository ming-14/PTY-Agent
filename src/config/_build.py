"""配置装配工具 —— build_config 统一合并模板

消除 shared/client/daemon 各模块重复的 merge + 运行时常量样板：
基线（common/shared/logging）+ 各侧专属 toml 在此装配为单一 dict，
并统一补充运行时常量（IS_WINDOWS/DATA_DIR/PROJECT_ROOT/LOG_DIR）。
独立于 _loader（文件 IO）与 common（基线数据），避免循环依赖。
"""

import os

from . import common as _common
from ._loader import flatten, load_toml, merge


def build_config(*extra: dict) -> dict:
    """装配配置 dict：common/shared/logging 基线 + 各侧专属 extra

    Args:
        *extra: 额外的展平配置 dict（侧专属 toml，如 daemon/client；可由
                调用方预先合并默认值，如 daemon 的 web.toml 兜底）。

    Returns:
        含运行时常量（IS_WINDOWS/DATA_DIR/PROJECT_ROOT/LOG_DIR）的配置 dict。
    """
    _all = merge(
        flatten(load_toml("common.toml")),
        flatten(load_toml("shared.toml")),
        flatten(load_toml("logging.toml")),
        *extra,
    )
    _all["IS_WINDOWS"] = _common.IS_WINDOWS
    _all["DATA_DIR"] = _common.DATA_DIR
    _all["PROJECT_ROOT"] = _common.PROJECT_ROOT
    _all["LOG_DIR"] = os.path.join(_common.DATA_DIR, "logs")
    return _all