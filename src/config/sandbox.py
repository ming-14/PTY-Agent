"""沙箱配置 —— win-sandbox 委派（Windows 专属）

来源: sandbox.toml

导出的 QUOTA / ISOLATION 直接对齐 win-sandbox start_process 参数
（quota / isolation_policy dict），供 src/sandbox/ 使用。
依赖定位：bin/win_sandbox（vendored python 包 + pyd），与 fastscreen 同模式。
"""

from ._loader import load_toml

_cfg = load_toml("sandbox.toml")

# ── 全局开关 ──
ENABLED = _cfg["sandbox"]["enabled"]
LOG_LEVEL = _cfg["sandbox"]["log_level"]

# ── 资源配额（对齐 win-sandbox quota payload）──
QUOTA = dict(_cfg["quota"])

# ── 隔离策略（对齐 win-sandbox isolation_policy payload）──
# 仅含标量/结构化数据（net_policy/net_allowlist/clipboard_isolate），无环境
# 变量路径字段；浅拷贝一层防止外部引用改动，manager 不会原地修改
ISOLATION = dict(_cfg["isolation"])

__all__ = [
    "ENABLED", "LOG_LEVEL",
    "QUOTA", "ISOLATION",
]