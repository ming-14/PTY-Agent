"""daemon 侧 download 发送 —— 握手后扫描远端树并逐文件发送

流程（与 client_download.py 对称）：
1. 回握手 ok（kind: file|dir；参数校验与存在性检查在 handler 完成）
2. 发 MANIFEST 帧（远端 entries，含 size/mtime）
3. 收 CLI 本地清单 → build_plan（本地信息 + transfer_map 判定）
4. denied → ABORT；否则发 PLAN 帧
5. 逐文件发送（DATA 帧流 → FILE_END(sha256)）→ 等 ACK → transfer_map upsert
6. 失败/断连中止（无临时文件——发送侧不落盘）
"""

import hashlib
import json
import logging
import os

from typing import Optional

from ...config.files import TRANSFER_CHUNK_SIZE
from ...protocol.message import Message
from ...protocol.transfer import (
    FT_ABORT,
    FT_ACK,
    FT_DATA,
    FT_FILE_END,
    FT_MANIFEST,
    FT_PLAN,
    TransferProtocolError,
    recv_control_frame,
    send_control_frame,
    send_frame,
)
from .common import ENTRY_DIR, ENTRY_FILE, TransferAbortedError, TransferError
from .judge import build_plan
from .map import TransferMap, get_default_map
from .scan import scan_tree

_logger = logging.getLogger("pty-daemon")


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
    """daemon 侧下载发送主流程（handler 校验通过后调用）

    Args:
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


def _join_remote(root: str, relpath: str) -> str:
    """远端根 + 清单相对路径（清单统一 / 分隔，落地按平台分隔符）"""
    if not relpath:
        return root
    return os.path.join(root, *relpath.split("/"))