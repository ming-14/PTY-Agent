"""日志上下文绑定 — 基于 ContextVar 自动注入 session_id 等字段

线程安全 + async 安全。在线程/协程入口 bind()，后续所有日志自动带该字段。
跨线程需用 contextvars.copy_context() 显式传播（线程池任务提交时）。
"""

import contextvars
from typing import Any, Dict

# 单一 ContextVar 存储当前上下文字段字典
# default=None 避免可变对象共享问题，get_context() 时返回空 dict
_context_var: contextvars.ContextVar = contextvars.ContextVar(
    "pty_log_context", default=None
)


def bind(**kwargs: Any) -> contextvars.Token:
    """绑定上下文字段，返回 token 供 unbind() 使用

    在当前上下文（线程/协程）中合并新字段。已绑定的同名字段会被覆盖。
    返回的 token 仅对当前上下文有效，不可跨上下文 unbind。

    典型用法:
        token = bind(session_id="abc", connection_id="conn1")
        try:
            _logger.info("处理请求")  # 自动带 session_id=abc connection_id=conn1
        finally:
            unbind(token)
    """
    current = _context_var.get()
    new = {**(current or {}), **kwargs}
    return _context_var.set(new)


def unbind(token: contextvars.Token) -> None:
    """解除绑定，恢复到 bind() 前的状态"""
    _context_var.reset(token)


def get_context() -> Dict[str, Any]:
    """获取当前上下文字段字典（格式器调用）"""
    ctx = _context_var.get()
    return ctx if ctx is not None else {}


def clear() -> None:
    """清除当前上下文所有字段"""
    _context_var.set(None)
