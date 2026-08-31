"""daemon 侧传输业务 —— upload 接收 / download 发送（原生 conn 帧收发）

与 src/client/transfer/client_upload.py / client_download.py 对称：
- daemon_upload：握手 ok → 收 MANIFEST → build_plan 判定 → 发 PLAN →
  逐文件接收（DATA → FILE_END 校验 → 原子落盘 → 历史/状态/映射）→ ACK
- daemon_download：握手 ok → 发 MANIFEST → 收本地清单 → build_plan →
  发 PLAN → 逐文件发送（DATA → FILE_END）→ 收 ACK → 映射 upsert
- 中断安全：任何异常路径 finally 清理 tmp，不留半文件

帧收发直接基于连接 socket（Message + protocol.transfer），
不再经插件系统 PluginIO——文件工具已内化为主程序内置功能。
"""

import hashlib
import json
import logging
import os
import random
from typing import Optional

from src.config.transfer import TRANSFER_CHUNK_SIZE, TRANSFER_TMP_SUFFIX
from src.protocol.message import Message
from src.protocol.transfer import (
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
from src.client.transfer.common import (
    ENTRY_DIR,
    ENTRY_FILE,
    TransferAbortedError,
    TransferError,
)
from src.client.transfer.scan import scan_tree
from src.files.history import FileHistoryStore
from src.files.transfer.judge import build_plan
from src.files.transfer.map import TransferMap, get_default_map
from src.files.permission import PermissionPolicy
from src.files.state import get_default_store

_logger = logging.getLogger("pty-daemon")


def _join_remote(root: str, relpath: str) -> str:
    """远端根 + 清单相对路径（清单统一 / 分隔，落地按平台分隔符）"""
    if not relpath:
        return root
    return os.path.join(root, *relpath.split("/"))


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _read_text_if_utf8(path: str) -> Optional[str]:
    """尝试按 UTF-8 读取文件；非 UTF-8/不存在返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _record_history_if_text(history: FileHistoryStore, path: str,
                            old_text: Optional[str], new_text: Optional[str]) -> None:
    """文本文件落版本链（时序同步写侧）；二进制跳过"""
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


def _receive_file(conn, dst: str, expect_relpath: str, tmp_suffix: str) -> tuple:
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


def daemon_upload(conn, remote_root: str, force: bool,
                  history: Optional[FileHistoryStore] = None,
                  tmap: Optional[TransferMap] = None,
                  store=None) -> None:
    """daemon 侧上传接收主流程（命令处理层校验通过后调用）

    Args:
        conn: 连接 socket（消息 + 二进制帧收发）
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


def _send_file(conn, src: str, relpath: str) -> None:
    """发送单文件：DATA 帧流 → FILE_END；收 ACK 校验结果"""
    st = os.stat(src)
    hasher = hashlib.sha256()
    with open(src, "rb") as f:
        while True:
            chunk = f.read(TRANSFER_CHUNK_SIZE)
            if not chunk:
                break
            send_frame(conn, FT_DATA, chunk)
            hasher.update(chunk)
    send_frame(conn, FT_FILE_END, json.dumps({
        "relpath": relpath, "sha256": hasher.hexdigest(),
        "size": st.st_size, "mtime": st.st_mtime,
    }).encode("utf-8"))
    ack = recv_control_frame(conn)
    if ack is None:
        raise TransferAbortedError("no ack for %s" % relpath)
    if ack.get("ok") is not True:
        raise TransferError(ack.get("error") or "client rejected %s" % relpath)


def daemon_download(conn, remote_root: str, force: bool,
                    tmap: Optional[TransferMap] = None) -> None:
    """daemon 侧下载发送主流程（命令处理层校验通过后调用）

    Args:
        conn: 连接 socket（消息 + 二进制帧收发）
        tmap: 映射表依赖注入（默认单例；测试传 :memory: 实例）
    """
    if os.path.isdir(remote_root):
        kind = ENTRY_DIR
        entries = scan_tree(remote_root)
    else:
        kind = ENTRY_FILE
        st = os.stat(remote_root)
        entries = [{"relpath": "", "kind": ENTRY_FILE,
                    "size": st.st_size, "mtime": st.st_mtime}]

    Message.send(conn, {
        "commandType": "file_download_start",
        "ok": True,
        "remote_path": remote_root,
        "kind": kind,
    })
    _logger.info("file_download: root=%r kind=%s entries=%d force=%s",
                 remote_root, kind, len(entries), force)
    send_control_frame(conn, FT_MANIFEST, {"entries": entries})

    local_manifest = recv_control_frame(conn)
    if local_manifest is None:
        _logger.warning("file_download: 本地清单阶段连接断开: %s", remote_root)
        return
    local_entries = local_manifest.get("entries", [])

    remote_by_rel = {e["relpath"]: e for e in entries if e.get("kind") == ENTRY_FILE}
    tmap = tmap or get_default_map()

    def resolver(relpath):
        e = remote_by_rel.get(relpath)
        if e:
            return True, e["size"], e["mtime"]
        return False, 0, 0.0

    def map_getter(relpath):
        return tmap.get(_join_remote(remote_root, relpath))

    # 本地清单 entries 与远端同构（exists=False 时 size/mtime 为 0）
    plan = build_plan(local_entries, resolver, map_getter, force=force)
    denied = plan["denied"]
    if denied:
        sample = ", ".join(d["relpath"] or "<root>" for d in denied[:5])
        reason = ("target exists with different content: %s; "
                  "use --force to overwrite" % sample)
        _logger.info("file_download: 拒绝覆盖 %d 个文件: %s", len(denied), sample)
        send_control_frame(conn, FT_ABORT, {"reason": reason})
        return

    send_control_frame(conn, FT_PLAN, {
        "transfers": plan["transfers"],
        "skips": plan["skips"],
        "mkdirs": plan["mkdirs"],
    })
    _logger.info("file_download: plan transfers=%d skips=%d mkdirs=%d",
                 len(plan["transfers"]), len(plan["skips"]), len(plan["mkdirs"]))

    for rel in plan["transfers"]:
        src = _join_remote(remote_root, rel)
        try:
            _send_file(conn, src, rel)
            # CLI 落盘后本地 mtime = 远端 mtime，映射两端对齐
            st = os.stat(src)
            tmap.upsert(src, cli_size=st.st_size, cli_mtime=st.st_mtime,
                        remote_mtime=st.st_mtime)
            _logger.info("file_download: 完成 %s size=%d", src, st.st_size)
        except (TransferError, TransferAbortedError, TransferProtocolError,
                OSError) as e:
            _logger.error("file_download: 中止 %s: %s", src, e)
            return

    _logger.info("file_download: 全部完成 root=%s files=%d", remote_root,
                 len(plan["transfers"]))