"""客户端文件命令混入 —— file read/write/edit/grep/glob/upload/download。

文件命令职责（ClientFileCommandsMixin）。
upload/download 为多往返流式操作：握手 JSON 后切换二进制帧，不走 _send_recv。
"""

import socket
from typing import Optional

from ..protocol.response import Response
from ..logging import get_logger
from . import presenter

_logger = get_logger("pty-client")


class ClientFileCommandsMixin:
    """文件工具命令域（file 子命令）"""

    def cmd_file_read(
        self,
        path: str,
        cwd_session: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ):
        """读取文件内容（file read 子命令）

        路径原样传输，由 daemon 按会话 cwd（cwd_session 指定，不操作该会话）解析。
        """
        _logger.info(
            "cmd_file_read: path=%r cwd_session=%r offset=%s limit=%s",
            path,
            cwd_session,
            offset,
            limit,
        )
        msg: dict = {"type": "file_read", "path": path, "cwd_session": cwd_session}
        if offset is not None:
            msg["offset"] = offset
        if limit is not None:
            msg["limit"] = limit
        resp = self._send_recv(msg)
        presenter.print_response(resp)

    def cmd_file_write(
        self, path: str, cwd_session: str, content: Optional[str] = None
    ):
        """覆盖写/新建文件（file write 子命令）

        路径原样传输，由 daemon 按会话 cwd 解析；content 由调用方保证非空。
        """
        _logger.info(
            "cmd_file_write: path=%r cwd_session=%r content_len=%s",
            path,
            cwd_session,
            None if content is None else len(content),
        )
        msg: dict = {"type": "file_write", "path": path, "cwd_session": cwd_session}
        if content is not None:
            msg["content"] = content
        resp = self._send_recv(msg)
        presenter.print_response(resp)

    def cmd_file_edit(
        self, path: str, cwd_session: str, old: Optional[str], new: Optional[str]
    ):
        """唯一匹配替换/删除/新建（file edit 子命令）

        --old 空 = 新建（文件必须不存在）；--new 空 = 删除；
        均非空 = 替换（old 须唯一匹配）。
        CLI 侧将 None 归一为空串，daemon 恒收到字符串；
        路径由 daemon 按会话 cwd（cwd_session）解析。
        """
        _logger.info(
            "cmd_file_edit: path=%r cwd_session=%r old_len=%s new_len=%s",
            path,
            cwd_session,
            None if old is None else len(old),
            None if new is None else len(new),
        )
        resp = self._send_recv(
            {
                "type": "file_edit",
                "path": path,
                "cwd_session": cwd_session,
                "old": old or "",
                "new": new or "",
            }
        )
        presenter.print_response(resp)

    def cmd_file_grep(
        self,
        pattern: str,
        cwd_session: str,
        path: Optional[str] = None,
        include: Optional[str] = None,
        literal_text: bool = False,
    ):
        """内容搜索（file grep 子命令）

        path 缺省 = 会话 cwd（daemon 侧解析）；提供时按会话 cwd 展开。
        """
        _logger.info(
            "cmd_file_grep: pattern=%r cwd_session=%r path=%r include=%r literal=%s",
            pattern,
            cwd_session,
            path,
            include,
            literal_text,
        )
        msg: dict = {
            "type": "file_grep",
            "pattern": pattern,
            "cwd_session": cwd_session,
        }
        if path is not None:
            msg["path"] = path
        if include is not None:
            msg["include"] = include
        if literal_text:
            msg["literal_text"] = True
        resp = self._send_recv(msg)
        presenter.print_response(resp)

    def cmd_file_glob(self, pattern: str, cwd_session: str, path: Optional[str] = None):
        """文件名匹配（file glob 子命令）

        path 缺省 = 会话 cwd（daemon 侧解析）；提供时按会话 cwd 展开。
        """
        _logger.info(
            "cmd_file_glob: pattern=%r cwd_session=%r path=%r",
            pattern,
            cwd_session,
            path,
        )
        msg: dict = {
            "type": "file_glob",
            "pattern": pattern,
            "cwd_session": cwd_session,
        }
        if path is not None:
            msg["path"] = path
        resp = self._send_recv(msg)
        presenter.print_response(resp)

    def cmd_file_upload(
        self,
        local_path: str,
        remote_path: str,
        cwd_session: str,
        force: bool = False,
        timeout: Optional[float] = None,
    ):
        """上传本地文件/目录到 daemon 侧（file upload 子命令）

        - local_path 为 CLI 本机绝对路径（__main__ 已解析）
        - remote_path 由 daemon 按 cwd_session 会话 cwd 解析
        - 传输为多往返流式操作，不走 _send_recv：握手 JSON 后切换二进制帧
        """
        from ..config.transfer import TRANSFER_TIMEOUT
        from .transfer.client_upload import upload
        from .transfer.common import (
            TransferAbortedError,
            TransferError,
            TransferTimeoutError,
        )

        if timeout is None:
            timeout = TRANSFER_TIMEOUT
        _logger.info(
            "cmd_file_upload: local=%r remote=%r cwd_session=%r force=%s timeout=%s",
            local_path,
            remote_path,
            cwd_session,
            force,
            timeout,
        )
        sock = self._connect()
        try:
            # 握手消息凭证注入（与 _send_recv 一致：token/pubkey 认证字段）
            def enrich(msg: dict):
                if self._credential_provider is not None:
                    self._credential_provider.enrich(msg)

            resp = upload(
                sock,
                local_path,
                remote_path,
                cwd_session,
                force,
                timeout,
                enrich=enrich,
            )
            presenter.print_response(resp)
        except (TransferAbortedError, TransferError, TransferTimeoutError) as e:
            presenter.print_response(Response.error(str(e)))
        except (ConnectionError, socket.timeout, OSError) as e:
            _logger.warning("cmd_file_upload: 连接异常: %s", e)
            presenter.print_response(Response.error("transfer connection failed: %s" % e))
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def cmd_file_download(
        self,
        remote_path: str,
        local_path: str,
        cwd_session: str,
        force: bool = False,
        timeout: Optional[float] = None,
    ):
        """下载 daemon 侧文件/目录到本地（file download 子命令）

        - remote_path 由 daemon 按 cwd_session 会话 cwd 解析
        - local_path 为 CLI 本机绝对路径（__main__ 已解析）
        """
        from ..config.transfer import TRANSFER_TIMEOUT
        from .transfer.client_download import download
        from .transfer.common import (
            TransferAbortedError,
            TransferError,
            TransferTimeoutError,
        )

        if timeout is None:
            timeout = TRANSFER_TIMEOUT
        _logger.info(
            "cmd_file_download: remote=%r local=%r cwd_session=%r force=%s timeout=%s",
            remote_path,
            local_path,
            cwd_session,
            force,
            timeout,
        )
        sock = self._connect()
        try:
            # 握手消息凭证注入（与 _send_recv 一致：token/pubkey 认证字段）
            def enrich(msg: dict):
                if self._credential_provider is not None:
                    self._credential_provider.enrich(msg)

            resp = download(
                sock,
                local_path,
                remote_path,
                cwd_session,
                force,
                timeout,
                enrich=enrich,
            )
            presenter.print_response(resp)
        except (TransferAbortedError, TransferError, TransferTimeoutError) as e:
            presenter.print_response(Response.error(str(e)))
        except (ConnectionError, socket.timeout, OSError) as e:
            _logger.warning("cmd_file_download: 连接异常: %s", e)
            presenter.print_response(Response.error("transfer connection failed: %s" % e))
        finally:
            try:
                sock.close()
            except OSError:
                pass