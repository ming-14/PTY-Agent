"""内置响应装饰 — 插件按 manifest.decorateTypes 装饰内置命令响应

dispatcher 出站响应包装时调用：对扁平响应体按 commandType 匹配
声明了该类型的进程级插件，调用其 decorate_response 修改响应后再发送。
异常隔离：单插件装饰异常只记日志，不中断响应发送。
"""
from __future__ import annotations

from .base import ProcessPluginContext
from ..logging import get_logger

_logger = get_logger("pty-plugins")


def decorate_builtin_response(manager, body: dict) -> dict:
    """按 commandType 匹配插件装饰响应体

    Args:
        manager: SessionManager（获取插件注册表）。
        body: 扁平响应体（含 commandType）。

    Returns:
        装饰后的响应体（原样或修改后）。
    """
    ctype = body.get("commandType")
    if not ctype:
        return body
    if manager is None:
        return body
    reg = getattr(manager, "plugin_registry", None)
    if reg is None:
        return body
    instances = reg.process_instances()
    if not instances:
        return body
    for name in sorted(instances):
        inst = instances[name]
        manifest = getattr(inst, "manifest", None)
        if manifest is None or ctype not in manifest.decorate_types:
            continue
        try:
            pctx = ProcessPluginContext(manager, inst, None, reg.environment)
            result = inst.decorate_response(pctx, body)
            if result is not None:
                body = result
        except Exception:
            _logger.exception("插件 %s 装饰 %s 响应异常", name, ctype)
    return body
