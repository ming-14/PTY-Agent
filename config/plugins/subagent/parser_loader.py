"""多 parser 包加载器 — 以独立命名空间包加载各 parser，避免包冲突

每个 parser（如 workbuddyparser、devinparser、opencodeparser、claudeparser）内部使用相对导入
（from ..entities），必须以包形式加载。这里把各 parser 的 src 目录
挂到不同的独立包名（wb_parser、dv_parser、oc_parser、cl_parser）下，使相对导入正常解析
且不与 PTY-Agent 的 src 冲突。

新增 parser：在 _PARSER_REGISTRY 注册 agent → (包名, src 目录) 即可。
"""
from __future__ import annotations

import importlib
import os
import sys
import types

_BASE = os.path.dirname(os.path.abspath(__file__))

# 注册表：agent → { 包名, src 目录 }
_PARSER_REGISTRY = {
    "codebuddy": {
        "pkg": "wb_parser",
        "src": os.path.normpath(os.path.join(_BASE, "parser", "workbuddyparser", "src")),
    },
    "devin": {
        "pkg": "dv_parser",
        "src": os.path.normpath(os.path.join(_BASE, "parser", "devinparser", "src")),
    },
    "opencode": {
        "pkg": "oc_parser",
        "src": os.path.normpath(os.path.join(_BASE, "parser", "opencodeparser", "src")),
    },
    "claude": {
        "pkg": "cl_parser",
        "src": os.path.normpath(os.path.join(_BASE, "parser", "claudeparser", "src")),
    },
    "smartagent": {
        "pkg": "sm_parser",
        "src": os.path.normpath(os.path.join(_BASE, "parser", "smartparser", "src")),
    },
}

_loaded: set = set()


def _ensure_loaded(agent: str) -> None:
    """注册指定 agent 的 parser 为命名空间包（幂等）"""
    if agent in _loaded:
        return
    pkg = _PARSER_REGISTRY[agent]["pkg"]
    src = _PARSER_REGISTRY[agent]["src"]
    if pkg not in sys.modules:
        mod = types.ModuleType(pkg)
        mod.__path__ = [src]
        mod.__package__ = pkg
        sys.modules[pkg] = mod
    _loaded.add(agent)


def import_parser(agent: str, name: str):
    """导入指定 agent 的 parser 子模块

    Args:
        agent: parser agent 名（如 "codebuddy"、"devin"，见 _PARSER_REGISTRY）
        name: 模块路径，如 "adapters.screen"、"adapters.messages_jsonl"
    """
    if agent not in _PARSER_REGISTRY:
        raise ImportError(f"未知 parser agent: {agent}")
    _ensure_loaded(agent)
    pkg = _PARSER_REGISTRY[agent]["pkg"]
    return importlib.import_module(f"{pkg}.{name}")