"""共享配置 —— Daemon 与 Client 两侧均需使用的常量

来源: build_config 装配（common.toml + shared.toml + logging.toml）+ 运行时计算属性
包含共有配置（common）的所有常量，可直接从此模块导入。
日志跨侧共享配置（格式/归档/异步队列）来自 logging.toml。
"""

from ._build import build_config

_all = build_config()
globals().update(_all)
__all__ = list(_all.keys())