"""file_upload 命令处理 —— file upload 子命令

CLI: pty-agent file upload <local-path> <remote-path> [--force] [--timeout N] -s <session-id>
- local-path 在 CLI 侧解析（本机文件），remote-path 在 daemon 侧按会话 cwd 解析
- 握手（JSON 校验参数）通过后，由 daemon_upload 接管连接做二进制分块接收
- 传输不受 read-before-write 状态机约束；文本落 history + 状态机双刷
"""

import logging

from ...protocol.message import Message
from ...protocol.response import Response
from ...config.files import MAX_PATH_LEN
from ...files.paths import resolve_session_path
from ...files.transfer.daemon_upload import daemon_upload
from .base import DaemonHandler, HandlerContext
from .utils import get_session_cwd

_logger = logging.getLogger("pty-daemon")


class FileUploadHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict,
               history=None, tmap=None, store=None):
        cwd = get_session_cwd(ctx, conn, msg.get("cwd_session", ""))
        if cwd is None:
            return
        path = resolve_session_path(msg.get("path", ""), cwd)
        force = bool(msg.get("force", False))
        _logger.info("file_upload: path=%r cwd_session=%r actual=%r force=%s",
                     msg.get("path"), msg.get("cwd_session"), path, force)
        if not path:
            Message.send(conn, Response.error("path is required"))
            return
        if len(path) > MAX_PATH_LEN:
            Message.send(conn, Response.error(
                "path too long (max %d chars)" % MAX_PATH_LEN))
            return

        try:
            # history/tmap/store 依赖注入（测试传 :memory:/独立实例，默认单例）
            daemon_upload(conn, path, force, history=history,
                          tmap=tmap, store=store)
        except Exception as e:
            _logger.error("file_upload 处理异常: path=%s err=%s", path, e)
            try:
                Message.send(conn, Response.error("upload failed: %s" % e))
            except OSError:
                pass