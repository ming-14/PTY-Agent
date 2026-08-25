"""配置加载工具 —— TOML 文件读取、展平、合并、环境变量覆写"""

import functools
import json
import os
import warnings

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

# TOML 配置目录：<项目根>/config/（与 src/ 平级的部署配置目录）。
# 基于 __file__ 定位（src/config/_loader.py → 项目根），与运行 cwd 无关。
# 发布形态（BUILD.ps1）中 config/ 与 src/ 同级，同规则生效。
# 按侧分离：daemon 专属配置在 config/daemon/，client 专属在 config/client/，
# 共享配置（common/shared/transfer.toml）留在 config/ 根。
# 测试隔离：PTY_AGENT_CONFIG_DIR 环境变量可重定向配置目录（e2e 测试用临时
# 目录，避免写入/污染生产配置）；生产环境不设置，行为不变。
_BASE_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
)
_CONFIG_DIR = os.environ.get("PTY_AGENT_CONFIG_DIR") or _BASE_CONFIG_DIR


@functools.lru_cache(maxsize=64)
def load_toml(filename: str, domain: str = "") -> dict:
    """读取指定 TOML 文件

    lru_cache：common.toml/shared.toml 被 common/shared/daemon/client 各加载一次，
    冷启动时文件重复解析消除；返回 dict 仅被 flatten/只读消费（不原地修改）。

    Args:
        filename: 文件名（如 "common.toml"）。
        domain: 配置域（"daemon" / "client" / ""），非空时从对应子目录读取。

    Returns:
        TOML 解析后的嵌套 dict。
    """
    path = (
        os.path.join(_CONFIG_DIR, domain, filename)
        if domain
        else os.path.join(_CONFIG_DIR, filename)
    )
    with open(path, "rb") as f:
        return tomllib.load(f)


def flatten(d: dict) -> dict:
    """将嵌套 dict 展平为顶层 key → value 映射

    同名 key 冲突时抛出 ValueError，防止静默覆盖。
    """
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for fk, fv in flatten(v).items():
                if fk in out:
                    raise ValueError(
                        f"配置 key 冲突: {fk!r} 在同一 TOML 文件中出现多次"
                    )
                out[fk] = fv
        else:
            if k in out:
                raise ValueError(f"配置 key 冲突: {k!r} 在同一 TOML 文件中出现多次")
            out[k] = v
    return out


def merge(*sources: dict) -> dict:
    """合并多个展平后的配置字典，同名 key 冲突时抛出 ValueError"""
    merged = {}
    for src in sources:
        for k, v in src.items():
            if k in merged:
                raise ValueError(f"配置 key 冲突: {k!r} 在不同 TOML 文件中重复定义")
            merged[k] = v
    return merged


# 环境变量覆写前缀：PTY_AGENT_<配置 key>（如 DATA_DIR → PTY_AGENT_DATA_DIR）
ENV_PREFIX = "PTY_AGENT_"

# bool 取值映射（大小写不敏感）
_TRUE_STRINGS = {"true", "1", "yes", "on"}
_FALSE_STRINGS = {"false", "0", "no", "off"}


def _coerce_env_value(env_value: str, file_value):
    """将环境变量字符串按配置原值类型转换

    bool/int/float/str 按类型转换；list/dict（如 sandbox 的 net_allowlist）
    按 JSON 解析；None 原样返回字符串。
    """
    if isinstance(file_value, bool):
        v = env_value.strip().lower()
        if v in _TRUE_STRINGS:
            return True
        if v in _FALSE_STRINGS:
            return False
        raise ValueError(f"invalid boolean value: {env_value!r}")
    if isinstance(file_value, int):
        return int(env_value)
    if isinstance(file_value, float):
        return float(env_value)
    if isinstance(file_value, (list, dict)):
        return json.loads(env_value)
    return env_value


def apply_env_overrides(config: dict, prefix: str = ENV_PREFIX) -> dict:
    """用环境变量覆写配置 key（优先级：环境变量 > 文件）

    约定：环境变量名 = prefix + 配置 key（如 DATA_DIR → PTY_AGENT_DATA_DIR），
    仅对配置中已存在的 key 生效，未设置的变量不改变任何值。
    取值按文件原值类型转换（bool/int/float/str，list/dict 按 JSON）；
    转换失败时警告并保留文件值，不阻断启动。

    Args:
        config: 展平后的配置 dict（merge 结果）。
        prefix: 环境变量前缀。

    Returns:
        覆写后的新 dict（不修改入参）。
    """
    overridden = dict(config)
    for key, file_value in config.items():
        env_value = os.environ.get(prefix + key)
        if env_value is None:
            continue
        try:
            overridden[key] = _coerce_env_value(env_value, file_value)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            warnings.warn(
                f"环境变量 {prefix + key} 取值 {env_value!r} 无法按配置类型 "
                f"{type(file_value).__name__} 转换: {exc}，忽略该覆写"
            )
    return overridden
