"""响应格式化输出

支持 JSON 模式输出。守护进程响应直接 json.dumps 到 stdout。
"""

import json

from ..protocol.response import Response
from .input import safe_print

_SHOW_DEBUG = True


def set_debug_mode(enabled: bool):
    global _SHOW_DEBUG
    _SHOW_DEBUG = enabled


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
    if resp is None:
        safe_print(
            json.dumps(Response.error("daemon not responding"), ensure_ascii=False)
        )
        return

    if not _SHOW_DEBUG:
        resp = _strip_debug_info(resp)

    safe_print(json.dumps(resp, ensure_ascii=False))
