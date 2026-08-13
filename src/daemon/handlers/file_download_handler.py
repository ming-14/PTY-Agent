"""file_download 命令处理 —— file download 子命令

CLI: pty-agent file download <remote-path> <local-path> [--force] [--timeout N] -s <session-id>
- remote-path 在 daemon 侧按会话 cwd 解析；local-path 由 CLI 本机落盘
- 握手校验路径存在后，由 daemon_download 接管连接做二进制分块发送
- 相同文件（transfer_map 命中）跳过；不同文件默认拒绝，--force 覆盖
"""

import logging
import os

from ...protocol.message import Message
from ...protocol.response import Response
from ...config.files import MAX_PATH_LEN
from ...files.paths import resolve_session_path
from ...files.transfer.daemon_download import daemon_download
from .base import DaemonHandler, HandlerContext
from .utils import get_session_cwd

_logger = logging.getLogger("pty-daemon")


class FileDownloadHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict, tmap=None):
        cwd = get_session_cwd(ctx, conn, msg.get("cwd_session", ""))
        if cwd is None:
            return
        path = resolve_session_path(msg.get("path", ""), cwd)
        force = bool(msg.get("force", False))
        _logger.info("file_download: path=%r cwd_session=%r actual=%r force=%s",
                     msg.get("path"), msg.get("cwd_session"), path, force)
        if not path:
            Message.send(conn, Response.error("path is required"))
            return
        if len(path) > MAX_PATH_LEN:
            Message.send(conn, Response.error(
                "path too long (max %d chars)" % MAX_PATH_LEN))
            return
        if not os.path.exists(path):
            Message.send(conn, Response.error(
                "remote path does not exist: %s" % path))
            return

        try:
            # tmap 依赖注入（测试传 :memory: 实例，默认单例）
            daemon_download(conn, path, force, tmap=tmap)
        except Exception as e:
            _logger.error("file_download 处理异常: path=%s err=%s", path, e)
            try:
                Message.send(conn, Response.error("download failed: %s" % e))
            except OSError:
                pass