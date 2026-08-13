"""file_glob 命令处理 —— file glob 子命令

CLI: pty-agent file glob <pattern> [path] -s <session-id>
- 搜索根在 daemon 侧按 cwd_session 的会话 cwd 解析（缺省=会话 cwd）
- rg 引擎优先，缺失/失败自动降级纯 Python（见 src/files/search/glob_.py）
"""

import logging

from ...protocol.message import Message
from ...protocol.response import Response
from ...config.files import MAX_PATH_LEN
from ...files.search.glob_ import glob_files
from ...files.paths import resolve_session_path
from .base import DaemonHandler, HandlerContext
from .utils import get_session_cwd

_logger = logging.getLogger("pty-daemon")


class FileGlobHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        cwd = get_session_cwd(ctx, conn, msg.get("cwd_session", ""))
        if cwd is None:
            return
        pattern = msg.get("pattern")
        path = resolve_session_path(msg.get("path", "") or ".", cwd)
        _logger.info("file_glob: pattern=%r cwd_session=%r path_arg=%r actual=%r",
                     pattern, msg.get("cwd_session"), msg.get("path"), path)
        if not isinstance(pattern, str) or not pattern:
            Message.send(conn, Response.error("pattern is required"))
            return
        if not path:
            Message.send(conn, Response.error("path is required"))
            return
        if len(path) > MAX_PATH_LEN:
            Message.send(conn, Response.error(
                "path too long (max %d chars)" % MAX_PATH_LEN))
            return

        try:
            result = glob_files(pattern, path)
        except OSError as e:
            _logger.error("file_glob 搜索失败: path=%s err=%s", path, e)
            Message.send(conn, Response.error("failed to glob: %s" % e))
            return

        _logger.info("file_glob 结束: path=%s engine=%s files=%d truncated=%s",
                     path, result.engine, len(result.files), result.truncated)
        Message.send(conn, {
            "commandType": "file_glob",
            "files": result.files,
            "truncated": result.truncated,
        })