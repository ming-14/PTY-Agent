"""CLI 命令子系统

统一管理 PTY-Agent 的 CLI 子命令：命令注册、解析器构建、派发与公共管线。
入口 main() 供 src/__main__.py 调用。
"""

from .main import main

__all__ = ["main"]
