"""file 文件工具命令处理 —— 内置 handler（原 files 功能内化）

接管消息类型：file_read / file_write / file_edit / file_grep / file_glob /
file_upload_start / file_download_start（与原插件声明一致，客户端零改动）。

路径解析：按 cwd_session 指定会话的 cwd 解析（不操作该会话）。
上传/下载：多帧传输（握手 JSON 后切二进制帧），处理过程自行收发，不再发送响应。
共享状态：FileRecordStore（read-before-write）、TransferMap、FileHistoryStore
均为 daemon 级单例，由 src/files/ 模块持有。
"""

import logging
import os
from typing import Optional

from ...protocol.message import Message
from ...protocol.response import Response
from ...execution.context import HandlerContext
from ..handlers.base import DaemonHandler
from ...files import errors
from ...files.read import reader as read_impl
from ...files.write import writer as write_impl
from ...files.search import grep as grep_impl
from ...files.search import glob_ as glob_impl
from ...files.transfer import transfer as transfer_impl
from ...files.transfer.map import TransferMap, get_default_map
from ...files.paths import resolve_session_path
from ...files.state import get_default_store
from ...files.history import FileHistoryStore
from ...files.settings import settings

_logger = logging.getLogger("pty-daemon")


class FileHandler(DaemonHandler):
    """文件工具命令处理（内置）"""

    def __init__(self, history=None, tmap=None, store=None):
        """依赖注入（测试传 :memory:/独立实例；默认 None = 业务默认单例）"""
        self._history = history
        self._tmap = tmap
        self._store = store

    def handle(self, ctx: HandlerContext, conn, msg: dict):
        msg_type = msg.get("type", "")
        if msg_type == "file_read":
            Message.send(conn, _handle_read(ctx, msg))
        elif msg_type == "file_write":
            Message.send(conn, _handle_write(ctx, msg))
        elif msg_type == "file_edit":
            Message.send(conn, _handle_edit(ctx, msg))
        elif msg_type == "file_grep":
            Message.send(conn, _handle_grep(ctx, msg))
        elif msg_type == "file_glob":
            Message.send(conn, _handle_glob(ctx, msg))
        elif msg_type == "file_upload_start":
            _handle_upload(ctx, conn, msg,
                           history=self._history, tmap=self._tmap, store=self._store)
        elif msg_type == "file_download_start":
            _handle_download(ctx, conn, msg, tmap=self._tmap)
        else:
            Message.send(conn, Response.error("未知 file 子命令: %s" % msg_type))


def _session_cwd(ctx: HandlerContext, msg: dict) -> tuple:
    """按 cwd_session 解析会话 cwd；失败返回 (None, 错误响应)"""
    cwd_session = msg.get("cwd_session", "")
    if not cwd_session:
        return None, Response.error("cwd_session is required")
    session = ctx.manager.get_session(cwd_session) if ctx.manager else None
    if session is None:
        return None, Response.error(
            "cwd_session: session not found: %s" % cwd_session)
    cwd = session.cwd
    if not cwd:
        return None, Response.error(
            "cwd_session: session has no cwd: %s" % cwd_session)
    return cwd, None


def _check_path(path: str) -> Optional[dict]:
    """路径合法性校验；非法返回错误响应，合法返回 None"""
    if not path:
        return Response.error("path is required")
    if len(path) > settings.max_path_len:
        return Response.error("path too long (max %d chars)" % settings.max_path_len)
    return None


def _handle_read(ctx, msg: dict):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        return err
    path = resolve_session_path(msg.get("path", ""), cwd)
    _logger.info("file_read: path=%r cwd_session=%r actual=%r offset=%s limit=%s",
                 msg.get("path"), msg.get("cwd_session"), path,
                 msg.get("offset"), msg.get("limit"))
    err = _check_path(path)
    if err is not None:
        return err

    offset = msg.get("offset") or 0
    limit = msg.get("limit") or 0
    try:
        offset = int(offset)
        limit = int(limit)
    except (TypeError, ValueError):
        return Response.error("offset/limit must be integers")

    try:
        result = read_impl.read_file(path, offset=offset, limit=limit)
    except errors.FileToolError as e:
        return Response.error(str(e))
    except FileNotFoundError:
        suggestions = read_impl.suggest_similar(path)
        if suggestions:
            return Response.error(
                "File not found: %s\n\nDid you mean one of these?\n%s"
                % (path, "\n".join(suggestions)))
        return Response.error("File not found: %s" % path)
    except OSError as e:
        _logger.error("file_read 读取失败: %s", e)
        return Response.error("failed to read file: %s" % e)

    # 成功读取后刷新状态机，作为 write/edit 的前置检查依据
    get_default_store().record_read(path)
    return {
        "commandType": "file_read",
        "path": path,
        "content": result.content,
        "totalLines": result.total_lines,
        "truncated": result.truncated,
    }


def _handle_write(ctx, msg: dict):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        return err
    path = resolve_session_path(msg.get("path", ""), cwd)
    content = msg.get("content")
    _logger.info("file_write: path=%r cwd_session=%r actual=%r content_len=%s",
                 msg.get("path"), msg.get("cwd_session"), path,
                 None if content is None else len(content))
    err = _check_path(path)
    if err is not None:
        return err
    if not isinstance(content, str):
        return Response.error("content is required")

    try:
        result = write_impl.write_file(path, content)
    except errors.FileReadRequiredError as e:
        # 已存在文件未经读取或已被外部修改 → 提示先 file read
        return Response.error(str(e))
    except errors.FileToolError as e:
        return Response.error(str(e))
    except OSError as e:
        _logger.error("file_write 落盘失败: path=%s err=%s", path, e)
        return Response.error("failed to write file: %s" % e)

    _logger.info("file_write 成功: path=%s existed=%s additions=%d removals=%d",
                 path, result.existed, result.additions, result.removals)
    return {
        "commandType": "file_write",
        "path": path,
        "existed": result.existed,
    }


def _handle_edit(ctx, msg: dict):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        return err
    path = resolve_session_path(msg.get("path", ""), cwd)
    old = msg.get("old")
    new = msg.get("new")
    _logger.info("file_edit: path=%r cwd_session=%r actual=%r",
                 msg.get("path"), msg.get("cwd_session"), path)
    err = _check_path(path)
    if err is not None:
        return err
    if not isinstance(old, str) or not isinstance(new, str):
        _logger.warning("file_edit 参数类型非法: path=%s old=%r new=%r", path, old, new)
        return Response.error("old and new are required")
    _logger.info("file_edit: path=%r old_len=%s new_len=%s",
                 path, len(old), len(new))

    try:
        result = write_impl.edit_file(path, old, new)
    except errors.FileReadRequiredError as e:
        # 已存在文件未经读取或已被外部修改 → 提示先 file read
        return Response.error(str(e))
    except errors.FileToolError as e:
        return Response.error(str(e))
    except OSError as e:
        _logger.error("file_edit 落盘失败: path=%s err=%s", path, e)
        return Response.error("failed to edit file: %s" % e)

    _logger.info("file_edit 成功: path=%s existed=%s additions=%d removals=%d",
                 path, result.existed, result.additions, result.removals)
    return {
        "commandType": "file_edit",
        "path": path,
    }


def _handle_grep(ctx, msg: dict):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        return err
    pattern = msg.get("pattern")
    path = resolve_session_path(msg.get("path", "") or ".", cwd)
    include = msg.get("include")
    literal_text = bool(msg.get("literal_text"))
    _logger.info("file_grep: pattern=%r cwd_session=%r path_arg=%r actual=%r include=%r literal=%s",
                 pattern, msg.get("cwd_session"), msg.get("path"), path,
                 include, literal_text)
    if not isinstance(pattern, str) or not pattern:
        return Response.error("pattern is required")
    err = _check_path(path)
    if err is not None:
        return err
    if not os.path.exists(path):
        return Response.error("path not found: %s" % path)

    try:
        result = grep_impl.grep_files(pattern, path, include=include,
                                      literal_text=literal_text)
    except OSError as e:
        _logger.error("file_grep 搜索失败: path=%s err=%s", path, e)
        return Response.error("failed to search: %s" % e)

    _logger.info("file_grep 结束: path=%s engine=%s matches=%d truncated=%s",
                 path, result.engine, len(result.matches), result.truncated)
    return {
        "commandType": "file_grep",
        "matches": [
            {"path": m.path, "lineNumber": m.line_number, "content": m.content}
            for m in result.matches
        ],
        "truncated": result.truncated,
    }


def _handle_glob(ctx, msg: dict):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        return err
    pattern = msg.get("pattern")
    path = resolve_session_path(msg.get("path", "") or ".", cwd)
    _logger.info("file_glob: pattern=%r cwd_session=%r path_arg=%r actual=%r",
                 pattern, msg.get("cwd_session"), msg.get("path"), path)
    if not isinstance(pattern, str) or not pattern:
        return Response.error("pattern is required")
    err = _check_path(path)
    if err is not None:
        return err
    if not os.path.exists(path):
        return Response.error("path not found: %s" % path)

    try:
        result = glob_impl.glob_files(pattern, path)
    except OSError as e:
        _logger.error("file_glob 搜索失败: path=%s err=%s", path, e)
        return Response.error("failed to glob: %s" % e)

    _logger.info("file_glob 结束: path=%s engine=%s files=%d truncated=%s",
                 path, result.engine, len(result.files), result.truncated)
    return {
        "commandType": "file_glob",
        "files": result.files,
        "truncated": result.truncated,
    }


def _handle_upload(ctx, conn, msg, history=None, tmap=None, store=None):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        Message.send(conn, err)
        return
    path = resolve_session_path(msg.get("path", ""), cwd)
    force = bool(msg.get("force", False))
    _logger.info("file_upload: path=%r cwd_session=%r actual=%r force=%s",
                 msg.get("path"), msg.get("cwd_session"), path, force)
    err = _check_path(path)
    if err is not None:
        Message.send(conn, err)
        return

    try:
        # 握手 ok 与后续帧收发全部经 conn 完成，调用方无需再响应
        transfer_impl.daemon_upload(conn, path, force,
                                    history=history, tmap=tmap, store=store)
    except Exception as e:
        _logger.error("file_upload 处理异常: path=%s err=%s", path, e)
        try:
            Message.send(conn, Response.error("upload failed: %s" % e))
        except OSError:
            pass


def _handle_download(ctx, conn, msg, tmap=None):
    cwd, err = _session_cwd(ctx, msg)
    if err is not None:
        Message.send(conn, err)
        return
    path = resolve_session_path(msg.get("path", ""), cwd)
    force = bool(msg.get("force", False))
    _logger.info("file_download: path=%r cwd_session=%r actual=%r force=%s",
                 msg.get("path"), msg.get("cwd_session"), path, force)
    err = _check_path(path)
    if err is not None:
        Message.send(conn, err)
        return
    if not os.path.exists(path):
        Message.send(conn, Response.error("remote path does not exist: %s" % path))
        return

    try:
        # 握手 ok 与后续帧收发全部经 conn 完成，调用方无需再响应
        transfer_impl.daemon_download(conn, path, force, tmap=tmap)
    except Exception as e:
        _logger.error("file_download 处理异常: path=%s err=%s", path, e)
        try:
            Message.send(conn, Response.error("download failed: %s" % e))
        except OSError:
            pass