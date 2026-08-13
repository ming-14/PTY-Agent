"""file_write 命令处理 —— file write 子命令

CLI: pty-agent file write <path> --content TEXT -s <session-id>
- 路径在 daemon 侧按 cwd_session 的会话 cwd 解析（不操作该会话）
- 内部顺序（writer.py）：状态机检查 → diff → 权限 → 落盘 → 历史 → 刷状态
- diff/历史为后台机制，响应不进 CLI 呈现（design §4.3）
"""

import logging

from ...protocol.message import Message
from ...protocol.response import Response
from ...config.files import MAX_PATH_LEN
from ...files import errors, get_default_store
from ...files.paths import resolve_session_path
from ...files.write.writer import write_file
from .base import DaemonHandler, HandlerContext
from .utils import get_session_cwd

_logger = logging.getLogger("pty-daemon")


class FileWriteHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        cwd = get_session_cwd(ctx, conn, msg.get("cwd_session", ""))
        if cwd is None:
            return
        path = resolve_session_path(msg.get("path", ""), cwd)
        content = msg.get("content")
        _logger.info("file_write: path=%r cwd_session=%r actual=%r content_len=%s",
                     msg.get("path"), msg.get("cwd_session"), path,
                     None if content is None else len(content))
        if not path:
            Message.send(conn, Response.error("path is required"))
            return
        if len(path) > MAX_PATH_LEN:
            Message.send(conn, Response.error(
                "path too long (max %d chars)" % MAX_PATH_LEN))
            return
        if not isinstance(content, str):
            Message.send(conn, Response.error("content is required"))
            return

        try:
            result = write_file(path, content)
        except errors.FileReadRequiredError as e:
            # 已存在文件未经读取或已被外部修改 → 提示先 file read
            Message.send(conn, Response.error(str(e)))
            return
        except errors.FileToolError as e:
            Message.send(conn, Response.error(str(e)))
            return
        except OSError as e:
            _logger.error("file_write 落盘失败: path=%s err=%s", path, e)
            Message.send(conn, Response.error("failed to write file: %s" % e))
            return

        _logger.info("file_write 成功: path=%s existed=%s additions=%d removals=%d",
                     path, result.existed, result.additions, result.removals)
        Message.send(conn, {
            "commandType": "file_write",
            "path": path,
            "existed": result.existed,
        })