"""CLI 侧 upload 驱动 —— 扫描 → 握手 → 清单 → 计划 → 逐文件传输（含进度）

流程（与 config/plugins/files 插件侧 daemon_upload 对称）：
1. scan_tree 本地路径 → 清单 entries
2. 握手 file_upload_start（JSON，沿用签名/认证）→ 收 ok/error
3. 发 MANIFEST 帧 → 收 PLAN 帧（含 ABORT 拒绝路径）
4. 按 PLAN.transfers 逐文件：DATA 帧流 → FILE_END(sha256) → 等 ACK
5. ACK 失败 → 发 ABORT 中止整体；进度实时刷 stderr（TTY \r / 非 TTY 定时）

超时语义（--timeout 总时限）：每帧读写前 settimeout(剩余)，超时抛
TransferTimeoutError，调用方清理中止。
"""

import hashlib
import json
import os
import sys
import time
from typing import List

from ..config.transfer import (
    TRANSFER_CHUNK_SIZE,
    TRANSFER_PROGRESS_INTERVAL,
)
from ..protocol.message import Message
from ..protocol.transfer import (
    FT_ABORT,
    FT_DATA,
    FT_FILE_END,
    FT_MANIFEST,
    FT_PLAN,
    recv_control_frame,
    recv_frame,
    send_control_frame,
    send_frame,
)
from .common import (
    ENTRY_FILE,
    TransferAbortedError,
    TransferError,
    TransferTimeoutError,
    entry,
)
from .scan import scan_tree
from ..logging import get_logger

_logger = get_logger("pty-client")


class ProgressReporter:
    """传输进度报告：TTY 实时进度条（\\r 刷新），非 TTY 每 interval 秒打印一行

    输出到 stderr，不污染 stdout 的 JSON 响应。
    """

    def __init__(
        self,
        total_bytes: int,
        total_files: int,
        interval: float = TRANSFER_PROGRESS_INTERVAL,
    ):
        self._total_bytes = total_bytes
        self._total_files = total_files
        self._interval = interval
        self._tty = sys.stderr.isatty()
        self._start = time.monotonic()
        self._last_print = 0.0
        self._done_before = 0  # 已完成文件累计字节
        self._file_index = 0  # 当前文件序号（0-based）
        self._file_name = ""
        self._file_done = 0  # 当前文件已传字节

    def file_start(self, index: int, relpath: str) -> None:
        """开始传输一个文件（累计基准更新）"""
        self._done_before += self._file_done
        self._file_index = index
        self._file_name = relpath or "<root>"
        self._file_done = 0
        self._render()

    def update(self, file_done: int) -> None:
        """当前文件进度更新（已传字节）"""
        self._file_done = file_done
        now = time.monotonic()
        if self._tty:
            self._render()
        elif now - self._last_print >= self._interval:
            self._last_print = now
            self._render(end="\n")

    def finish(self) -> None:
        """传输结束：非 TTY 补打最终进度行（TTY 已实时显示）"""
        if not self._tty:
            self._render(end="\n")

    def _done_total(self) -> int:
        return self._done_before + self._file_done

    def _render(self, end: str = "") -> None:
        if self._total_bytes <= 0:
            return
        done = self._done_total()
        pct = done * 100.0 / self._total_bytes
        elapsed = max(1e-6, time.monotonic() - self._start)
        speed = done / elapsed
        line = "[%d/%d] %s %.1f%% %s/s %s/%s" % (
            self._file_index + 1,
            self._total_files,
            self._file_name,
            pct,
            _fmt_bytes(speed),
            _fmt_bytes(done),
            _fmt_bytes(self._total_bytes),
        )
        if self._tty:
            sys.stderr.write("\r" + line + "\x1b[K")
            sys.stderr.flush()
        else:
            sys.stderr.write(line + end)
            sys.stderr.flush()


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f%s" % (n, unit) if unit != "B" else "%d%s" % (int(n), unit)
        n /= 1024.0
    return "%d%s" % (int(n), "B")


def _deadline_remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _local_full(local_root: str, relpath: str) -> str:
    """本地根 + 清单相对路径（清单统一 / 分隔，落地按平台分隔符）"""
    if not relpath:
        return local_root
    return os.path.join(local_root, *relpath.split("/"))


def _send_file(
    conn,
    local: str,
    relpath: str,
    size: int,
    mtime: float,
    deadline: float,
    reporter: ProgressReporter,
    index: int,
) -> dict:
    """传输单文件：DATA 帧流 → FILE_END → 收 ACK

    Raises:
        TransferError: ACK 失败（错误原因在 ACK.error）
        TransferAbortedError / TransferTimeoutError: 连接/超时
    """
    reporter.file_start(index, relpath)
    hasher = hashlib.sha256()
    sent = 0
    with open(local, "rb") as f:
        while True:
            chunk = f.read(TRANSFER_CHUNK_SIZE)
            if not chunk:
                break
            conn.settimeout(_deadline_remaining(deadline))
            send_frame(conn, FT_DATA, chunk)
            hasher.update(chunk)
            sent += len(chunk)
            reporter.update(sent)

    conn.settimeout(_deadline_remaining(deadline))
    send_frame(
        conn,
        FT_FILE_END,
        json.dumps(
            {
                "relpath": relpath,
                "sha256": hasher.hexdigest(),
                "size": size,
                "mtime": mtime,
            }
        ).encode("utf-8"),
    )
    ack = recv_control_frame(conn, _deadline_remaining(deadline))
    if ack is None:
        raise TransferAbortedError("no ack for %s" % relpath)
    if ack.get("ok") is not True:
        raise TransferError(ack.get("error") or "transfer rejected for %s" % relpath)
    return ack


def upload(
    conn,
    local_root: str,
    remote_path: str,
    cwd_session: str,
    force: bool,
    timeout: float,
    enrich=None,
) -> dict:
    """CLI 侧上传主流程；返回汇总 dict（供 transport 打印 JSON 响应）

    Args:
        enrich: 可选的握手消息凭证注入回调（token/pubkey 认证字段，
                与 _send_recv 的 credential_provider.enrich 语义一致）
    """
    t_start = time.monotonic()
    entries = scan_tree(local_root)
    # 远端目标以路径分隔符结尾 = 目录语义：本地单文件上传时把清单根条目
    # 归一为文件名（relpath=<文件名>），由 daemon 落到 <目录>/<文件名>；
    # 否则根条目 relpath="" 会落地为与目标同名的文件，违背"上传到该目录"的意图
    if remote_path.endswith(("/", "\\")) and len(entries) == 1 and not entries[0].get("relpath"):
        base = os.path.basename(local_root)
        if base:
            parent = os.path.dirname(local_root) or "."
            root_entry = entries[0]
            entries = [
                entry(base, ENTRY_FILE, root_entry.get("size", 0), root_entry.get("mtime", 0.0))
            ]
            local_root = parent
    file_entries = [e for e in entries if e.get("kind") == ENTRY_FILE]
    total_bytes = sum(e.get("size", 0) for e in file_entries)
    _logger.info(
        "file_upload: local=%r remote=%r force=%s files=%d bytes=%d",
        local_root,
        remote_path,
        force,
        len(file_entries),
        total_bytes,
    )

    conn.settimeout(timeout)
    handshake = {
        "type": "file_upload_start",
        "path": remote_path,
        "force": force,
        "cwd_session": cwd_session,
    }
    if enrich is not None:
        enrich(handshake)
    Message.send(conn, handshake)
    resp = Message.recv(conn)
    if resp is None:
        raise TransferAbortedError("no response to upload handshake")
    if resp.get("type") == "error" or not resp.get("ok"):
        raise TransferError(resp.get("message") or "upload handshake rejected")

    deadline = time.monotonic() + timeout
    send_control_frame(conn, FT_MANIFEST, {"entries": entries})

    frame = recv_frame(conn, _deadline_remaining(deadline))
    if frame is None:
        raise TransferAbortedError("connection closed during plan phase")
    ftype, payload = frame
    if ftype == FT_ABORT:
        reason = "upload aborted by daemon"
        try:
            reason = json.loads(payload.decode("utf-8")).get("reason", reason)
        except (ValueError, UnicodeDecodeError):
            pass
        raise TransferError(reason)
    if ftype != FT_PLAN:
        raise TransferError("unexpected frame type %d during plan phase" % ftype)
    plan = json.loads(payload.decode("utf-8"))

    reporter = ProgressReporter(total_bytes, max(1, len(plan.get("transfers", []))))
    transferred: List[str] = []
    failed: List[str] = []
    error_msg = ""
    for index, rel in enumerate(plan.get("transfers", [])):
        try:
            local = _local_full(local_root, rel)
            st = os.stat(local)
            _send_file(
                conn, local, rel, st.st_size, st.st_mtime, deadline, reporter, index
            )
            transferred.append(rel)
        except (TransferError, TransferAbortedError, TransferTimeoutError) as e:
            failed.append(rel)
            error_msg = str(e)
            _logger.error("file_upload: 中止 %s: %s", rel, error_msg)
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
        "commandType": "file_upload",
        "remotePath": remote_path,
        "transferred": transferred,
        "skipped": plan.get("skips", []),
        "failed": failed,
        "error": error_msg or None,
        "totalSize": total_bytes,
        "durationSec": round(duration, 3),
    }
    _logger.info(
        "file_upload: 结束 transferred=%d skipped=%d failed=%d duration=%.3fs",
        len(transferred),
        len(plan.get("skips", [])),
        len(failed),
        duration,
    )
    return summary
