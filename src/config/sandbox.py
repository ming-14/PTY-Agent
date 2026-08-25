"""沙箱配置 —— win-sandbox 委派（Windows 专属，daemon 侧）

来源: daemon/sandbox.toml（可选；不存在时沙箱功能关闭）

导出的 QUOTA / ISOLATION 直接对齐 win-sandbox start_process 参数
（quota / isolation_policy dict），供 src/sandbox/ 使用。
依赖定位：bin/win_sandbox（vendored python 包 + pyd）。
"""

from ._loader import apply_env_overrides, load_toml

# sandbox.toml 为可选配置：不存在时沙箱功能关闭（ENABLED=false），
# 不触发 src/sandbox 包导入，程序正常运行
try:
    _cfg = load_toml("sandbox.toml", "daemon")
except FileNotFoundError:
    _cfg = None

# 配置文件是否成功加载（sandbox.toml 不存在时为 False）
CONFIG_LOADED = _cfg is not None

if _cfg is not None:
    # 各节展平为 <SECTION>_<KEY>（如 SANDBOX_ENABLED / QUOTA_MEMORY_MB），
    # 统一经环境变量覆写（PTY_AGENT_ 前缀，优先级高于文件）
    _flat = {}
    for _section, _values in _cfg.items():
        for _key, _value in _values.items():
            _flat[f"{_section.upper()}_{_key.upper()}"] = _value
    _flat = apply_env_overrides(_flat)

    # ── 全局开关 ──
    ENABLED = _flat["SANDBOX_ENABLED"]
    LOG_LEVEL = _flat["SANDBOX_LOG_LEVEL"]

    # ── 资源配额（对齐 win-sandbox quota payload）──
    QUOTA = {
        _k[len("QUOTA_"):].lower(): _v
        for _k, _v in _flat.items()
        if _k.startswith("QUOTA_")
    }

    # ── 隔离策略（对齐 win-sandbox isolation_policy payload）──
    # 仅含标量/结构化数据（net_policy/net_allowlist/clipboard_isolate），无环境
    # 变量路径字段；浅拷贝一层防止外部引用改动，manager 不会原地修改
    ISOLATION = {
        _k[len("ISOLATION_"):].lower(): _v
        for _k, _v in _flat.items()
        if _k.startswith("ISOLATION_")
    }
else:
    ENABLED = False
    LOG_LEVEL = "info"
    QUOTA = {}
    ISOLATION = {}

__all__ = [
    "CONFIG_LOADED",
    "ENABLED",
    "ISOLATION",
    "LOG_LEVEL",
    "QUOTA",
]
