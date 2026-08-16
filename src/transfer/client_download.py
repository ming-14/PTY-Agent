"""CLI 侧 download 驱动 —— 握手 → 收清单 → 回本地状态 → 计划 → 逐文件接收

流程（与 config/plugins/files 插件侧 daemon_download 对称）：
1. 握手 file_download_start（JSON）→ 收 ok（含 kind）
2. 收 MANIFEST 帧（远端 entries，size/mtime）
3. 对每个远端 entry 检查本地对应文件 → 回本地清单（exists/size/mtime）
4. 收 PLAN 帧（daemon 用映射表判定；ABORT = 拒绝覆盖）
5. mkdir → 逐文件接收（DATA 帧流 → FILE_END 校验）→ 写 tmp → rename → os.utime(远端 mtime)
   → ACK
6. 校验失败/超时 → 清理 tmp + ABORT 中止
"""

import hashlib
import json
import os
import random
import time
from typing import List

from ..config.transfer import TRANSFER_TMP_SUFFIX
from ..protocol.message import Message
from ..protocol.transfer import (
    FT_ABORT,
    FT_ACK,
    FT_DATA,
    FT_FILE_END,
    FT_MANIFEST,
    FT_PLAN,
    recv_control_frame,
    recv_frame,
    send_control_frame,
)
from .client_upload import ProgressReporter, _deadline_remaining, _local_full
from .common import (
    ENTRY_DIR,
    ENTRY_FILE,
    TransferAbortedError,
    TransferError,
    TransferTimeoutError,
)
from ..logging import get_logger

_logger = get_logger("pty-client")


def _local_dst(
    local_root: str, remote_actual: str, single_file: bool, relpath: str
) -> str:
    """本地落盘目标路径

    单文件：local_root 即目标文件；若 local_root 是已存在目录则放入
    basename（scp 语义）。目录：local_root/relpath（清单 / 分隔落地转换）。
    """
    if single_file:
        if os.path.isdir(local_root):
            return os.path.join(local_root, os.path.basename(remote_actual))
        return local_root
    return _local_full(local_root, relpath)


def _receive_file(
    conn,
    dst: str,
    expect_relpath: str,
    deadline: float,
    reporter: ProgressReporter,
    index: int,
) -> dict:
    """接收单文件：DATA 帧流 → FILE_END 校验 → rename 落盘 → mtime 对齐 → ACK

    Returns:
        FILE_END 元数据
    Raises:
        TransferError: 校验失败（tmp 已清理）
    """
    reporter.file_start(index, expect_relpath or "<root>")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = "%s%s.%08x" % (dst, TRANSFER_TMP_SUFFIX, random.getrandbits(32))
    hasher = hashlib.sha256()
    received = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                frame = recv_frame(conn, _deadline_remaining(deadline))
                if frame is None:
                    raise TransferAbortedError("connection closed during file transfer")
                ftype, payload = frame
                if ftype == FT_DATA:
                    f.write(payload)
                    hasher.update(payload)
                    received += len(payload)
                    reporter.update(received)
                elif ftype == FT_FILE_END:
                    meta = json.loads(payload.decode("utf-8"))
                    if meta.get("relpath") != expect_relpath:
                        raise TransferError(
                            "file order mismatch: expect %r got %r"
                            % (expect_relpath, meta.get("relpath"))
                        )
                    if meta.get("size") != received:
                        raise TransferError(
                            "file size mismatch: expect %d got %d"
                            % (meta.get("size"), received)
                        )
                    if meta.get("sha256") != hasher.hexdigest():
                        raise TransferError("sha256 mismatch for %s" % expect_relpath)
                    break
                else:
                    raise TransferError(
                        "unexpected frame type %d while receiving %s"
                        % (ftype, expect_relpath)
                    )
        os.replace(tmp, dst)
        os.utime(dst, (meta["mtime"], meta["mtime"]))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    conn.settimeout(_deadline_remaining(deadline))
    send_control_frame(conn, FT_ACK, {"relpath": expect_relpath, "ok": True})
    return meta


def download(
    conn,
    local_root: str,
    remote_path: str,
    cwd_session: str,
    force: bool,
    timeout: float,
    enrich=None,
) -> dict:
    """CLI 侧下载主流程；返回汇总 dict（供 transport 打印 JSON 响应）

    Args:
        enrich: 可选的握手消息凭证注入回调（同 client_upload.upload）
    """
    t_start = time.monotonic()
    _logger.info(
        "file_download: remote=%r local=%r force=%s timeout=%s",
        remote_path,
        local_root,
        force,
        timeout,
    )

    conn.settimeout(timeout)
    handshake = {
        "type": "file_download_start",
        "path": remote_path,
        "force": force,
        "cwd_session": cwd_session,
    }
    if enrich is not None:
        enrich(handshake)
    Message.send(conn, handshake)
    resp = Message.recv(conn)
    if resp is None:
        raise TransferAbortedError("no response to download handshake")
    if resp.get("type") == "error" or not resp.get("ok"):
        raise TransferError(resp.get("message") or "download handshake rejected")

    # 单文件场景：local_root 为目标文件路径（已存在目录时放入 basename）；
    # 目录场景：local_root 为目标根目录
    single_file = resp.get("kind") == ENTRY_FILE
    remote_actual = resp.get("remote_path") or remote_path

    deadline = time.monotonic() + timeout
    manifest = recv_control_frame(conn, _deadline_remaining(deadline))
    if manifest is None:
        raise TransferAbortedError("connection closed during manifest phase")
    remote_entries = manifest.get("entries", [])

    # 构造本地清单：逐项检查本地对应文件（exists/size/mtime 供 daemon 判定）
    local_entries: List[dict] = []
    for e in remote_entries:
        rel = e["relpath"]
        if e.get("kind") == ENTRY_DIR:
            local_entries.append({"relpath": rel, "kind": ENTRY_DIR})
            continue
        local = _local_dst(local_root, remote_actual, single_file, rel)
        if os.path.isfile(local):
            st = os.stat(local)
            local_entries.append(
                {
                    "relpath": rel,
                    "kind": ENTRY_FILE,
                    "exists": True,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        else:
            local_entries.append(
                {
                    "relpath": rel,
                    "kind": ENTRY_FILE,
                    "exists": False,
                    "size": 0,
                    "mtime": 0.0,
                }
            )

    conn.settimeout(_deadline_remaining(deadline))
    send_control_frame(conn, FT_MANIFEST, {"entries": local_entries})

    frame = recv_frame(conn, _deadline_remaining(deadline))
    if frame is None:
        raise TransferAbortedError("connection closed during plan phase")
    ftype, payload = frame
    if ftype == FT_ABORT:
        reason = "download aborted by daemon"
        try:
            reason = json.loads(payload.decode("utf-8")).get("reason", reason)
        except (ValueError, UnicodeDecodeError):
            pass
        raise TransferError(reason)
    if ftype != FT_PLAN:
        raise TransferError("unexpected frame type %d during plan phase" % ftype)
    plan = json.loads(payload.decode("utf-8"))

    file_entries = [e for e in remote_entries if e.get("kind") == ENTRY_FILE]
    plan_total = sum(e.get("size", 0) for e in file_entries)
    reporter = ProgressReporter(plan_total, max(1, len(plan.get("transfers", []))))

    for rel in plan.get("mkdirs", []):
        os.makedirs(_local_full(local_root, rel), exist_ok=True)

    transferred: List[str] = []
    failed: List[str] = []
    error_msg = ""
    for index, rel in enumerate(plan.get("transfers", [])):
        try:
            _receive_file(
                conn,
                _local_dst(local_root, remote_actual, single_file, rel),
                rel,
                deadline,
                reporter,
                index,
            )
            transferred.append(rel)
        except (TransferError, TransferAbortedError, TransferTimeoutError) as e:
            failed.append(rel)
            error_msg = str(e)
            _logger.error("file_download: 中止 %s: %s", rel, error_msg)
            try:
                conn.settimeout(_deadline_remaining(deadline))
                send_control_frame(conn, FT_ABORT, {"reason": error_msg})
            except (OSError, TransferError):
                pass
            break
        except OSError as e:
            failed.append(rel)
            error_msg = "local file error: %s" % e
            try:
                conn.settimeout(_deadline_remaining(deadline))
                send_control_frame(conn, FT_ABORT, {"reason": error_msg})
            except (OSError, TransferError):
                pass
            break

    reporter.finish()
    duration = time.monotonic() - t_start
    summary = {
        "commandType": "file_download",
        "remotePath": remote_path,
        "transferred": transferred,
        "skipped": plan.get("skips", []),
        "failed": failed,
        "error": error_msg or None,
        "totalSize": plan_total,
        "durationSec": round(duration, 3),
    }
    _logger.info(
        "file_download: 结束 transferred=%d skipped=%d failed=%d duration=%.3fs",
        len(transferred),
        len(plan.get("skips", [])),
        len(failed),
        duration,
    )
    return summary
