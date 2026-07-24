"""配置加载工具 —— TOML 文件读取、展平、合并"""

import os

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


def load_toml(filename: str) -> dict:
    path = os.path.join(_CONFIG_DIR, filename)
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
                    raise ValueError(f"配置 key 冲突: {fk!r} 在同一 TOML 文件中出现多次")
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
