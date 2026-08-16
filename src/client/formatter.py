"""响应格式化输出

支持 JSON 模式输出。守护进程响应直接 json.dumps 到 stdout。
CLI 插件经 set_render_hook 注册渲染钩子：返回 str 时打印文本替代 JSON。
"""

import json

from ..protocol.response import Response
from .input import safe_print
from ..logging import get_logger

_logger = get_logger("pty-client")

_SHOW_DEBUG = False
_render_hook = None
# 本次 CLI 进程是否打印过 error 响应（main 据此设置退出码 1）
_error_printed = False


def error_was_printed() -> bool:
    """是否打印过 error 响应（供 main 提升为进程退出码）"""
    return _error_printed


def set_debug_mode(enabled: bool):
    global _SHOW_DEBUG
    _SHOW_DEBUG = enabled


def set_render_hook(fn):
    """注册 CLI 渲染钩子（插件 render_response 链的入口）

    每次 CLI 进程启动时由 main 设置一次：print_response 先调用 fn(resp)，
    返回 str 则直接打印该文本，返回 None 走默认 JSON 输出。
    """
    global _render_hook
    _render_hook = fn


def _strip_debug_info(obj):
    """递归移除所有 debugInformation 字段"""
    if isinstance(obj, dict):
        return {
            k: _strip_debug_info(v) for k, v in obj.items() if k != "debugInformation"
        }
    if isinstance(obj, list):
        return [_strip_debug_info(item) for item in obj]
    return obj


def print_response(resp: dict):
    """打印守护进程响应（仅 JSON 模式）

    Args:
        resp: 守护进程返回的响应字典。
    """
    global _error_printed
    if resp is None:
        _error_printed = True
        safe_print(
            json.dumps(Response.error("daemon not responding"), ensure_ascii=False)
        )
        return

    if resp.get("type") == "error":
        _error_printed = True

    if not _SHOW_DEBUG:
        resp = _strip_debug_info(resp)

    # CLI 渲染钩子：插件返回文本则打印文本，否则默认 JSON
    if _render_hook is not None:
        try:
            text = _render_hook(resp)
        except Exception:
            _logger.exception("render_hook 异常，回退 JSON 打印")
            text = None
        if text is not None:
            safe_print(text, end="")
            return

    safe_print(json.dumps(resp, ensure_ascii=False))
