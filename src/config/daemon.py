"""守护进程配置 —— 仅 Daemon 进程使用的常量

来源: build_config 装配（common.toml + daemon/daemon.toml + shared.toml + logging.toml + daemon/logging.toml + daemon/web.toml）+ 运行时计算属性
包含共有配置（common）与共享配置（shared）的所有常量，可直接从此模块导入。
日志跨侧共享配置（格式/归档/异步队列）来自 logging.toml，侧专属（级别/分组）来自 daemon/logging.toml。
"""

from ._build import build_config
from ._loader import flatten, load_toml

# web.toml 为可选配置：缺失时视为 web 未启用（ENABLE_WEB=False，其余字段给默认值），
# 不触发 src/web 及 vnc/screenshare 等可选模块导入，主流程正常运行。
_WEB_DEFAULTS = {
    "ENABLE_WEB": False,
    "WEB_HOST": "127.0.0.1",
    "WEB_PORT": 18766,
    "WEB_PASSWORD_HASH": "",
    "ENABLE_VNC": False,
    "VNC_WINVNC_PATH": "",
    "VNC_MODULE_DIR": "",
    "ENABLE_FASTSCREEN": False,
    "FASTSCREEN_PACKAGE_DIR": "",
    "FASTSCREEN_DEFAULT_FPS": 30,
    "FASTSCREEN_DEFAULT_QUALITY": 0.8,
    "FASTSCREEN_DEFAULT_BITRATE": 2_000_000,
    "FASTSCREEN_DEFAULT_GOP_SIZE": 30,
    "FASTSCREEN_DEFAULT_METHOD": "auto",
    "FASTSCREEN_DEFAULT_STREAM_FORMAT": "mse",
    "RIKKA_ENABLED": True,
    "DEFAULT_THEME": "dark",
    "IME_ENABLED": True,
    "IME_CANDIDATE_COUNT": 5,
    "IME_VERTICAL": False,
    "IME_DEFAULT_STATE": "chinese",
    "IME_KEYBOARD_LAYOUT": "compact",
    "IME_TOOLBAR_DISPLAY": "always",
    "IME_TB_OPACITY": 100,
    "IME_KB_OPACITY": 100,
    "IME_TB_SCALE": 1.0,
    "IME_KB_SCALE": 1.0,
}

try:
    _web_file = flatten(load_toml("web.toml", "daemon"))
except FileNotFoundError:
    # web.toml 缺失：用默认值（web 禁用）
    _web_file = {}
# 默认值仅兜底：文件已定义的 key 以文件为准，避免与 _WEB_DEFAULTS 在 merge 中同名冲突
_web = {**_WEB_DEFAULTS, **_web_file}

_all = build_config(
    flatten(load_toml("daemon.toml", "daemon")),
    flatten(load_toml("logging.toml", "daemon")),
    _web,
)

globals().update(_all)
__all__ = list(_all.keys())
