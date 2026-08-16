"""文本格式器 — 增强字段：上下文自动注入

无上下文时输出与标准 logging.Formatter 一致。
有上下文时在 message 前插入 [key=val ...] 段：
    2026-08-16 14:30:00.123 [INFO    ] [pty-session:MainThread] session.py:42 - [sid=abc cid=conn1] 消息
"""

import logging

from .context import get_context


class ContextFormatter(logging.Formatter):
    """增强文本格式器：自动从 ContextVar 读取上下文注入到日志行"""

    def format(self, record: logging.LogRecord) -> str:
        # 先格式化 message（处理 %s 占位符）
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)

        # 注入上下文字段到 message 前
        ctx = get_context()
        if ctx:
            ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
            record.message = f"[{ctx_str}] {record.message}"

        s = self.formatMessage(record)
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            if s[-1:] != "\n":
                s = s + "\n"
            s = s + record.exc_text
        if record.stack_info:
            if s[-1:] != "\n":
                s = s + "\n"
            s = s + self.formatStack(record.stack_info)
        return s
