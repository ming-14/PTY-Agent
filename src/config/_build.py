"""配置装配工具 —— build_config 统一合并模板

消除 shared/client/daemon 各模块重复的 merge + 运行时常量样板：
基线（common/shared/logging）+ 各侧专属 toml 在此装配为单一 dict，
并统一补充运行时常量（IS_WINDOWS/DATA_DIR/PROJECT_ROOT/LOG_DIR）。
独立于 _loader（文件 IO）与 common（基线数据），避免循环依赖。
"""

import os

from . import common as _common
from ._loader import apply_env_overrides, flatten, load_toml, merge


def build_config(*extra: dict) -> dict:
    """装配配置 dict：common/shared/logging 基线 + 各侧专属 extra

    装配完成后统一经环境变量覆写（PTY_AGENT_<key>，优先级高于文件），
    再补充运行时常量（IS_WINDOWS/DATA_DIR/PROJECT_ROOT/LOG_DIR）——
    运行时常量非文件配置，不参与覆写。

    Args:
        *extra: 额外的展平配置 dict（侧专属 toml，如 daemon/client；可由
                调用方预先合并默认值，如 daemon 的 web.toml 兜底）。

    Returns:
        含运行时常量（IS_WINDOWS/DATA_DIR/PROJECT_ROOT/LOG_DIR）的配置 dict。
    """
    _all = apply_env_overrides(
        merge(
            flatten(load_toml("common.toml")),
            flatten(load_toml("shared.toml")),
            flatten(load_toml("logging.toml")),
            *extra,
        )
    )
    _all["IS_WINDOWS"] = _common.IS_WINDOWS
    _all["DATA_DIR"] = _common.DATA_DIR
    _all["PROJECT_ROOT"] = _common.PROJECT_ROOT
    _all["LOG_DIR"] = os.path.join(_common.DATA_DIR, "logs")
    return _all


def resolve_data_path(value: str, data_dir: str, default_sub: str) -> str:
    """解析数据路径配置：空值回落 <data_dir>/<default_sub>，非空展开 ~ 与 %VAR%/$VAR

    各侧 TOML 中"默认位于数据目录下"的路径字段（如 TLS 证书、authorized_keys）
    以空字符串为默认值，装配时经本函数回落，保证自定义 DATA_DIR 后全部跟随。
    normpath 归一化分隔符（子路径常量跨平台用 /，与 data_dir 拼接后统一平台分隔符）。
    """
    if not value:
        return os.path.normpath(os.path.join(data_dir, default_sub))
    return os.path.normpath(os.path.expandvars(os.path.expanduser(value)))