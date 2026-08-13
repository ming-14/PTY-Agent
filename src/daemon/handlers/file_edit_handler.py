"""file_edit 命令处理 —— file edit 子命令

CLI: pty-agent file edit <path> --old TEXT [--new TEXT] -s <session-id>
- 路径在 daemon 侧按 cwd_session 的会话 cwd 解析（不操作该会话）
- 三分支（writer.edit_file）：--old 空=新建（文件必须不存在）；--new 空=删除
- replace/delete 前置检查：已读取、未被外部修改、old 唯一匹配（design §4.1/§4.3）
"""

import logging

from ...protocol.message import Message
from ...protocol.response import Response
from ...config.files import MAX_PATH_LEN
from ...files import errors
from ...files.paths import resolve_session_path
from ...files.write.writer import edit_file
from .base import DaemonHandler, HandlerContext
from .utils import get_session_cwd

_logger = logging.getLogger("pty-daemon")


class FileEditHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        cwd = get_session_cwd(ctx, conn, msg.get("cwd_session", ""))
        if cwd is None:
            return
        path = resolve_session_path(msg.get("path", ""), cwd)
        old = msg.get("old")
        new = msg.get("new")
        _logger.info("file_edit: path=%r cwd_session=%r actual=%r",
                     msg.get("path"), msg.get("cwd_session"), path)
        if not path:
            Message.send(conn, Response.error("path is required"))
            return
        if len(path) > MAX_PATH_LEN:
            Message.send(conn, Response.error(
                "path too long (max %d chars)" % MAX_PATH_LEN))
            return
        if not isinstance(old, str) or not isinstance(new, str):
            _logger.warning("file_edit 参数类型非法: path=%s old=%r new=%r", path, old, new)
            Message.send(conn, Response.error("old and new are required"))
            return
        _logger.info("file_edit: path=%r old_len=%s new_len=%s",
                     path, len(old), len(new))

        try:
            result = edit_file(path, old, new)
        except errors.FileReadRequiredError as e:
            # 已存在文件未经读取或已被外部修改 → 提示先 file read
            Message.send(conn, Response.error(str(e)))
            return
        except errors.FileToolError as e:
            Message.send(conn, Response.error(str(e)))
            return
        except OSError as e:
            _logger.error("file_edit 落盘失败: path=%s err=%s", path, e)
            Message.send(conn, Response.error("failed to edit file: %s" % e))
            return

        _logger.info("file_edit 成功: path=%s existed=%s additions=%d removals=%d",
                     path, result.existed, result.additions, result.removals)
        Message.send(conn, {
            "commandType": "file_edit",
            "path": path,
        })