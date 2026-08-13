"""daemon 侧 upload 接收 —— 握手后接管连接做二进制分块接收

流程（与 client_upload.py 对称）：
1. 回握手 ok（参数校验已在 handler 完成）
2. 收 MANIFEST 帧 → build_plan（远端路径 + transfer_map 判定）
3. 存在 denied → ABORT（提示 --force），整体中止
4. 发 PLAN 帧 → 执行：mkdir → 逐文件接收（DATA 帧流 → FILE_END 校验）
5. 单文件：写 tmp → SHA256 校验 → os.replace 原子落盘 → mtime 对齐
   → 文本落 history → 状态机双刷 → transfer_map upsert → ACK
6. 校验失败 → 删 tmp + ACK error → 等对端 ABORT/断连结束

中断安全：任何异常路径 finally 清理 tmp，不留半文件。
"""

import hashlib
import json
import logging
import os
import random
import time
from typing import Optional

from ...config.files import TRANSFER_TMP_SUFFIX
from ...protocol.message import Message
from ...protocol.response import Response
from ...protocol.transfer import (
    FT_ABORT,
    FT_ACK,
    FT_DATA,
    FT_FILE_END,
    FT_MANIFEST,
    FT_PLAN,
    TransferProtocolError,
    recv_control_frame,
    recv_frame,
    send_control_frame,
    send_frame,
)
from ..history import FileHistoryStore
from ..permission import PermissionPolicy
from ..state import get_default_store
from .common import TransferAbortedError, TransferError
from .judge import build_plan
from .map import TransferMap, get_default_map

_logger = logging.getLogger("pty-daemon")


def _read_text_if_utf8(path: str) -> Optional[str]:
    """尝试按 UTF-8 读取文件；非 UTF-8/不存在返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _record_history_if_text(history: FileHistoryStore, path: str,
                            old_text: Optional[str], new_text: Optional[str]) -> None:
    """文本文件落版本链（时序同 writer._commit_write）；二进制跳过"""
    if new_text is None:
        _logger.info("file_upload: 二进制文件跳过 history: %s", path)
        return
    latest = history.get_latest(path)
    old = old_text or ""
    if latest is None:
        history.create(path, old)
    elif latest["content"] != old:
        history.create_version(path, old)
    history.create_version(path, new_text)


def _receive_file(conn, dst: str, expect_relpath: str,
                  tmp_suffix: str) -> tuple:
    """接收一个文件：DATA 帧流 → FILE_END 校验 → 写 tmp

    Returns:
        (FILE_END 元数据, tmp 路径)；校验通过后由调用方 os.replace 原子落盘
    Raises:
        TransferError: 校验失败/协议错误（tmp 已清理）
    """
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = "%s%s.%08x" % (dst, tmp_suffix, random.getrandbits(32))
    hasher = hashlib.sha256()
    received = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                frame = recv_frame(conn)
                if frame is None:
                    raise TransferAbortedError("connection closed during file transfer")
                ftype, payload = frame
                if ftype == FT_DATA:
                    f.write(payload)
                    hasher.update(payload)
                    received += len(payload)
                elif ftype == FT_FILE_END:
                    meta = json.loads(payload.decode("utf-8"))
                    if meta.get("relpath") != expect_relpath:
                        raise TransferError(
                            "file order mismatch: expect %r got %r"
                            % (expect_relpath, meta.get("relpath")))
                    if meta.get("size") != received:
                        raise TransferError(
                            "file size mismatch: expect %d got %d"
                            % (meta.get("size"), received))
                    if meta.get("sha256") != hasher.hexdigest():
                        raise TransferError(
                            "sha256 mismatch for %s" % expect_relpath)
                    return meta, tmp
                else:
                    raise TransferError(
                        "unexpected frame type %d while receiving %s"
                        % (ftype, expect_relpath))
    except Exception:
        _safe_remove(tmp)
        raise


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def daemon_upload(conn, remote_root: str, force: bool,
                  history: Optional[FileHistoryStore] = None,
                  tmap: Optional[TransferMap] = None,
                  store=None) -> None:
    """daemon 侧上传接收主流程（handler 校验通过后调用）

    Args:
        history/tmap/store: 依赖注入（默认单例；测试传 :memory:/独立实例）
    """
    history = history or FileHistoryStore()
    policy = PermissionPolicy()
    store = store or get_default_store()
    tmap = tmap or get_default_map()

    Message.send(conn, {
        "commandType": "file_upload_start",
        "ok": True,
        "remote_path": remote_root,
    })

    manifest = recv_control_frame(conn)
    if manifest is None:
        _logger.warning("file_upload: 清单阶段连接断开: %s", remote_root)
        return
    entries = manifest.get("entries", [])

    def resolver(relpath):
        dst = _join_remote(remote_root, relpath)
        try:
            st = os.stat(dst)
            return True, st.st_size, st.st_mtime
        except OSError:
            return False, 0, 0.0

    def map_getter(relpath):
        # 映射记录以完整远端路径为键（upsert 用 _join_remote 结果），relpath 需先拼接
        return tmap.get(_join_remote(remote_root, relpath))

    plan = build_plan(entries, resolver, map_getter, force=force)
    denied = plan["denied"]
    if denied:
        sample = ", ".join(d["relpath"] or "<root>" for d in denied[:5])
        reason = ("target exists with different content: %s; "
                  "use --force to overwrite" % sample)
        _logger.info("file_upload: 拒绝覆盖 %d 个文件: %s", len(denied), sample)
        send_control_frame(conn, FT_ABORT, {"reason": reason})
        return

    send_control_frame(conn, FT_PLAN, {
        "transfers": plan["transfers"],
        "skips": plan["skips"],
        "mkdirs": plan["mkdirs"],
    })
    _logger.info("file_upload: plan transfers=%d skips=%d mkdirs=%d",
                 len(plan["transfers"]), len(plan["skips"]), len(plan["mkdirs"]))

    for rel in plan["mkdirs"]:
        os.makedirs(_join_remote(remote_root, rel), exist_ok=True)

    failed = False
    for rel in plan["transfers"]:
        dst = _join_remote(remote_root, rel)
        try:
            old_text = _read_text_if_utf8(dst)
            meta, tmp = _receive_file(conn, dst, rel, TRANSFER_TMP_SUFFIX)
            # 校验通过：原子落盘 → mtime 对齐 CLI → 落历史 → 状态机双刷 → 映射
            os.replace(tmp, dst)
            os.utime(dst, (meta["mtime"], meta["mtime"]))
            new_text = _read_text_if_utf8(dst)
            _record_history_if_text(history, dst, old_text, new_text)
            store.record_write(dst)
            store.record_read(dst, os.path.getmtime(dst))
            tmap.upsert(dst, cli_size=meta["size"], cli_mtime=meta["mtime"],
                        remote_mtime=os.path.getmtime(dst))
            if not policy.check("upload", dst):
                _logger.warning("file_upload: 权限拒绝 %s（已落盘，待呈现层拦截）", dst)
            send_control_frame(conn, FT_ACK, {"relpath": rel, "ok": True})
            _logger.info("file_upload: 完成 %s size=%d sha256=%s",
                         dst, meta["size"], meta["sha256"][:12])
        except (TransferError, TransferAbortedError, TransferProtocolError,
                OSError, ValueError) as e:
            failed = True
            _logger.error("file_upload: 失败 %s: %s", dst, e)
            send_control_frame(conn, FT_ACK, {"relpath": rel, "ok": False, "error": str(e)})
            break

    if failed:
        try:
            abort = recv_control_frame(conn)
            if abort is None or abort.get("reason") is None:
                _logger.info("file_upload: 传输中止，连接关闭")
        except (TransferError, OSError):
            pass
        return

    _logger.info("file_upload: 全部完成 root=%s files=%d", remote_root,
                 len(plan["transfers"]))


def _join_remote(root: str, relpath: str) -> str:
    """远端根 + 清单相对路径（清单统一 / 分隔，落地按平台分隔符）"""
    if not relpath:
        return root
    return os.path.join(root, *relpath.split("/"))