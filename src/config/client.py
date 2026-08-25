"""客户端配置 —— 仅 Client 进程使用的常量

来源: build_config 装配（common.toml + shared.toml + logging.toml + client/client.toml + client/logging.toml）+ 运行时计算属性
包含共有配置（common）与共享配置（shared）的所有常量，可直接从此模块导入。
日志跨侧共享配置（格式/归档/异步队列）来自 logging.toml，侧专属（级别/分组）来自 client/logging.toml。
"""

import os

from ._build import build_config, resolve_data_path
from ._loader import flatten, load_toml

# 认证路径默认值：client.toml 中为空时回落 <DATA_DIR> 下默认子路径，
# 自定义 DATA_DIR（common.toml [paths]）后自动跟随
_AUTH_PATH_DEFAULTS = {
    "PUBKEY_PRIVATE_KEY_PATH": os.path.join("keys", "id_ed25519"),
    "KNOWN_HOSTS_FILE": "known_hosts",
}

_all = build_config(
    flatten(load_toml("client.toml", "client")),
    flatten(load_toml("logging.toml", "client")),
)

for key, default_sub in _AUTH_PATH_DEFAULTS.items():
    _all[key] = resolve_data_path(_all.get(key, ""), _all["DATA_DIR"], default_sub)

globals().update(_all)
__all__ = list(_all.keys())