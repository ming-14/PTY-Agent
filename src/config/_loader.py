"""配置加载工具 —— TOML 文件读取、展平、合并"""

import functools
import os

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

# TOML 配置目录：<项目根>/config/（与 src/ 平级的部署配置目录）。
# 基于 __file__ 定位（src/config/_loader.py → 项目根），与运行 cwd 无关。
# 发布形态（BUILD.ps1）中 config/ 与 src/ 同级，同规则生效。
# 按侧分离：daemon 专属配置在 config/daemon/，client 专属在 config/client/，
# 共享配置（common/shared/transfer.toml）留在 config/ 根。
_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
)


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
