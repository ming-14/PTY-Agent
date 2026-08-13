"""file_read 命令处理 —— file read 子命令

CLI: pty-agent file read <path> [--offset N] [--limit N] -s <session-id>
- 路径在 daemon 侧按 cwd_session 的会话 cwd 解析（不操作该会话）
- 读取成功后刷新读写状态机 readTime（写工具的前置检查依据）
"""

import logging

from ...protocol.message import Message
from ...protocol.response import Response
from ...config.files import MAX_PATH_LEN
from ...files import errors, get_default_store
from ...files.read import reader
from ...files.paths import resolve_session_path
from .base import DaemonHandler, HandlerContext
from .utils import get_session_cwd

_logger = logging.getLogger("pty-daemon")


class FileReadHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        cwd = get_session_cwd(ctx, conn, msg.get("cwd_session", ""))
        if cwd is None:
            return
        path = resolve_session_path(msg.get("path", ""), cwd)
        _logger.info("file_read: path=%r cwd_session=%r actual=%r offset=%s limit=%s",
                     msg.get("path"), msg.get("cwd_session"), path,
                     msg.get("offset"), msg.get("limit"))
        if not path:
            Message.send(conn, Response.error("path is required"))
            return
        if len(path) > MAX_PATH_LEN:
            Message.send(conn, Response.error(
                "path too long (max %d chars)" % MAX_PATH_LEN))
            return

        offset = msg.get("offset") or 0
        limit = msg.get("limit") or 0
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError):
            Message.send(conn, Response.error("offset/limit must be integers"))
            return

        try:
            result = reader.read_file(path, offset=offset, limit=limit)
        except errors.FileToolError as e:
            Message.send(conn, Response.error(str(e)))
            return
        except FileNotFoundError:
            suggestions = reader.suggest_similar(path)
            if suggestions:
                Message.send(conn, Response.error(
                    "File not found: %s\n\nDid you mean one of these?\n%s"
                    % (path, "\n".join(suggestions))))
            else:
                Message.send(conn, Response.error("File not found: %s" % path))
            return
        except OSError as e:
            _logger.error("file_read 读取失败: %s", e)
            Message.send(conn, Response.error("failed to read file: %s" % e))
            return

        # 成功读取后刷新状态机，作为 write/edit 的前置检查依据
        get_default_store().record_read(path)
        Message.send(conn, {
            "commandType": "file_read",
            "path": path,
            "content": result.content,
            "totalLines": result.total_lines,
            "truncated": result.truncated,
        })