"""共享内存通信协议 — 信息区 / 请求信箱 / 请求响应通道

取代原 TCP socket 通信：客户端与守护进程之间的所有消息
（请求 JSON、响应 JSON、单实例检测、停止指令）均通过命名共享内存传递。

协议结构：
  1. 守护进程信息区（单实例 + 心跳）：PID + 状态 + 心跳时间戳
  2. 请求信箱（Mailbox）：32 固定槽位，客户端注册请求，守护进程轮询处理
  3. 请求/响应通道：每次请求一对命名共享内存（PTYAgentReq_{pid}_{seq} /
     PTYAgentResp_{pid}_{seq}），存放 JSON 消息字节

槽位状态机:
    EMPTY(0) → PENDING(1) → PROCESSING(2) → DONE(3) → EMPTY(0)

响应数据布局（请求同构）:
    [0:8]   数据长度（ASCII 十进制，8 字节）
    [8]     truncated 标志（'0'/'1'）
    [16:]   JSON 消息字节
"""

import logging
import os
import time
import threading
from typing import Optional

from ..config import (
    MMAP_DAEMON_INFO_NAME,
    MMAP_DAEMON_INFO_SIZE,
    MMAP_MAILBOX_NAME,
    MAILBOX_SLOT_COUNT,
    MAILBOX_SLOT_SIZE,
    MAILBOX_SIZE,
)
from .shm_utils import (
    open_shm, close_shm, read_bytes, write_bytes,
    read_text, write_text, cleanup_shm,
)
from .message import Message

_logger = logging.getLogger("pty-protocol")

# ── 槽位状态 ──
# 注意：新建共享内存区域为全零（b"\x00"），与释放后的状态一致
SLOT_EMPTY = b"\x00"
SLOT_PENDING = b"1"
SLOT_PROCESSING = b"2"
SLOT_DONE = b"3"

# ── 信息区布局（64 字节）─
_INFO_PID_OFF = 0            # 8 字节 ASCII 十进制
_INFO_STATE_OFF = 8          # 1 字节 '0'/'1'
_INFO_HEARTBEAT_OFF = 9      # 20 字节 ASCII 浮点时间戳

# ── 信箱槽位布局（256 字节）─
_SLOT_STATE_OFF = 0          # 1 字节
_SLOT_PID_OFF = 1            # 8 字节
_SLOT_REQNAME_OFF = 9        # 64 字节
_SLOT_RESPNAME_OFF = 73      # 64 字节
_SLOT_TOKEN_OFF = 137        # 64 字节
_SLOT_SEQ_OFF = 201          # 8 字节

# ── 请求/响应通道数据布局 ──
_DATA_LEN_OFF = 0            # 8 字节
_DATA_TRUNC_OFF = 8          # 1 字节
_DATA_BODY_OFF = 16          # 数据体


# ============================================================
#  守护进程信息区（单实例 + 心跳）
# ============================================================

def write_daemon_info_handle(shm, pid: int, running: bool, heartbeat: float):
    """向已持有的信息区句柄写入 PID + 状态 + 心跳

    供守护进程在持有引用的情况下周期性刷新心跳。
    """
    write_text(shm, _INFO_PID_OFF, str(pid), 8)
    write_bytes(shm, _INFO_STATE_OFF, b"1" if running else b"0")
    write_text(shm, _INFO_HEARTBEAT_OFF, f"{heartbeat:.3f}", 20)


def read_daemon_info() -> Optional[tuple]:
    """读取守护进程信息

    Returns:
        (pid, running, heartbeat) 元组，不存在或格式错误返回 None。
    """
    shm = open_shm(MMAP_DAEMON_INFO_NAME, MMAP_DAEMON_INFO_SIZE, create=False)
    if shm is None:
        return None
    try:
        raw = read_bytes(shm, 0, MMAP_DAEMON_INFO_SIZE)
        if not raw or raw[0:1] == b"\x00":
            return None
        pid_text = raw[_INFO_PID_OFF:_INFO_PID_OFF + 8].rstrip(b"\x00")
        state = raw[_INFO_STATE_OFF:_INFO_STATE_OFF + 1]
        hb_text = raw[_INFO_HEARTBEAT_OFF:_INFO_HEARTBEAT_OFF + 20].rstrip(b"\x00")
        if not pid_text or not hb_text:
            return None
        pid = int(pid_text.decode("ascii"))
        running = state == b"1"
        heartbeat = float(hb_text.decode("ascii"))
        return (pid, running, heartbeat)
    except (ValueError, OSError):
        return None
    finally:
        close_shm(shm)


def cleanup_daemon_info():
    """清理守护进程信息区残留"""
    cleanup_shm(MMAP_DAEMON_INFO_NAME)


# ============================================================
#  请求信箱
# ============================================================

def _slot_state(shm, slot: int) -> bytes:
    """读取槽位状态字节"""
    return read_bytes(shm, slot * MAILBOX_SLOT_SIZE + _SLOT_STATE_OFF, 1)


def _set_slot_state(shm, slot: int, state: bytes):
    """写入槽位状态字节"""
    write_bytes(shm, slot * MAILBOX_SLOT_SIZE + _SLOT_STATE_OFF, state)


class Mailbox:
    """请求信箱 — 客户端注册请求，守护进程轮询消费

    线程安全：同一信箱可在多个线程中并发使用（用锁保护扫描与抢占）。
    """

    def __init__(self, keep_open: bool = False):
        self._shm = open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE) if keep_open else None
        self._lock = threading.Lock()

    # ---- 客户端侧 ----

    def acquire_slot(self, client_pid: int, req_name: str, resp_name: str,
                     token: str, seq: int) -> Optional[int]:
        """抢占一个空槽位并注册请求

        写后读验证（单字节写原子性）防止并发客户端同时抢占同一槽位。

        Returns:
            槽位索引，信箱满时返回 None。
        """
        with self._lock:
            shm = self._shm or open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE)
            if shm is None:
                return None
            try:
                for slot in range(MAILBOX_SLOT_COUNT):
                    if _slot_state(shm, slot) == SLOT_EMPTY:
                        base = slot * MAILBOX_SLOT_SIZE
                        write_text(shm, base + _SLOT_PID_OFF, str(client_pid), 8)
                        write_text(shm, base + _SLOT_REQNAME_OFF, req_name, 64)
                        write_text(shm, base + _SLOT_RESPNAME_OFF, resp_name, 64)
                        write_text(shm, base + _SLOT_TOKEN_OFF, token, 64)
                        write_text(shm, base + _SLOT_SEQ_OFF, str(seq), 8)
                        # 写后读验证：状态仍是 EMPTY 才置 PENDING
                        if _slot_state(shm, slot) != SLOT_EMPTY:
                            continue
                        _set_slot_state(shm, slot, SLOT_PENDING)
                        if _slot_state(shm, slot) == SLOT_PENDING:
                            _logger.debug("mailbox: 槽位 %d 抢占成功 (pid=%d)", slot, client_pid)
                            return slot
                        # 验证失败：清空槽位，重试下一个
                        _set_slot_state(shm, slot, SLOT_EMPTY)
                return None
            finally:
                if self._shm is None:
                    close_shm(shm)

    def wait_done(self, slot: int, timeout: float = 120.0) -> bool:
        """轮询等待槽位变为 DONE

        Args:
            slot:    槽位索引。
            timeout: 等待超时（秒）。

        Returns:
            True 表示守护进程已完成处理（DONE）。
        """
        shm = self._shm or open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE)
        if shm is None:
            return False
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if _slot_state(shm, slot) == SLOT_DONE:
                    return True
                time.sleep(0.01)
            return False
        finally:
            if self._shm is None:
                close_shm(shm)

    def release_slot(self, slot: int):
        """释放槽位（置 EMPTY）"""
        shm = self._shm or open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE)
        if shm is None:
            return
        try:
            _set_slot_state(shm, slot, SLOT_EMPTY)
        finally:
            if self._shm is None:
                close_shm(shm)

    def get_slot_info(self, slot: int) -> dict:
        """读取槽位内容"""
        shm = self._shm or open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE)
        if shm is None:
            return {}
        try:
            base = slot * MAILBOX_SLOT_SIZE
            return {
                "state": read_bytes(shm, base + _SLOT_STATE_OFF, 1),
                "client_pid": read_text(shm, base + _SLOT_PID_OFF, 8),
                "req_name": read_text(shm, base + _SLOT_REQNAME_OFF, 64),
                "resp_name": read_text(shm, base + _SLOT_RESPNAME_OFF, 64),
                "token": read_text(shm, base + _SLOT_TOKEN_OFF, 64),
                "seq": read_text(shm, base + _SLOT_SEQ_OFF, 8),
            }
        finally:
            if self._shm is None:
                close_shm(shm)

    # ---- 守护进程侧 ----

    def find_pending(self) -> Optional[int]:
        """扫描并抢占一个 PENDING 槽位（置 PROCESSING）

        Returns:
            槽位索引，无 PENDING 请求返回 None。
        """
        with self._lock:
            shm = self._shm or open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE)
            if shm is None:
                return None
            try:
                for slot in range(MAILBOX_SLOT_COUNT):
                    if _slot_state(shm, slot) == SLOT_PENDING:
                        _set_slot_state(shm, slot, SLOT_PROCESSING)
                        _logger.debug("mailbox: 处理槽位 %d", slot)
                        return slot
                return None
            finally:
                if self._shm is None:
                    close_shm(shm)

    def mark_done(self, slot: int):
        """标记槽位为 DONE（处理完成）"""
        shm = self._shm or open_shm(MMAP_MAILBOX_NAME, MAILBOX_SIZE)
        if shm is None:
            return
        try:
            _set_slot_state(shm, slot, SLOT_DONE)
        finally:
            if self._shm is None:
                close_shm(shm)

    def close(self):
        """关闭持有的共享内存引用"""
        close_shm(self._shm)
        self._shm = None


# ============================================================
#  请求 / 响应通道
# ============================================================

def make_channel_names(pid: int, seq: int) -> tuple:
    """生成请求/响应共享内存名

    Returns:
        (req_name, resp_name) 元组。
    """
    return f"Local\\PTYAgentReq_{pid}_{seq}", f"Local\\PTYAgentResp_{pid}_{seq}"


def write_message(shm, msg: dict, max_size: int, truncated_marker: bool = True) -> bool:
    """编码并写入 JSON 消息到共享内存通道

    若 JSON 超限，先移除 debug 段，再二分截断 output 字段，
    并在响应中标记 truncated。

    Args:
        shm:              通道 mmap 对象。
        msg:              消息字典。
        max_size:         通道数据区容量。
        truncated_marker: 是否允许截断（False 表示请求通道，超限直接失败）。

    Returns:
        True 写入成功（可能已截断），False 超限失败。
    """
    data = Message.encode(msg)
    if len(data) <= max_size:
        _write_payload(shm, data, truncated=False)
        return True

    if truncated_marker and "output" in msg:
        # 1) 去掉 debug 段重试
        slim = {k: v for k, v in msg.items() if k != "debug"}
        data = Message.encode(slim)
        if len(data) <= max_size:
            _write_payload(shm, data, truncated=False)
            return True
        msg = slim
        # 2) 二分查找最大可容纳的 output 长度
        output = msg.get("output", "")
        lo, hi = 0, len(output)
        best_data = None
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = dict(msg)
            cand["output"] = output[:mid]
            cand["truncated"] = True
            cand["warning"] = "输出过大，已截断（共享内存容量限制）"
            d = Message.encode(cand)
            if len(d) <= max_size:
                best_data = d
                lo = mid + 1
            else:
                hi = mid - 1
        if best_data is not None:
            _write_payload(shm, best_data, truncated=True)
            return True

    _logger.warning("write_message: 消息超限 (%d > %d)，写入失败", len(data), max_size)
    return False


def _write_payload(shm, data: bytes, truncated: bool):
    """写入数据体到通道（头部: 长度 + truncated 标志）"""
    write_text(shm, _DATA_LEN_OFF, str(len(data)), 8)
    write_bytes(shm, _DATA_TRUNC_OFF, b"1" if truncated else b"0")
    write_bytes(shm, _DATA_BODY_OFF, data)


def read_message(shm) -> Optional[dict]:
    """从共享内存通道读取 JSON 消息

    Returns:
        解码后的 dict，读取失败返回 None。
    """
    len_text = read_text(shm, _DATA_LEN_OFF, 8)
    if not len_text:
        return None
    try:
        length = int(len_text)
    except ValueError:
        return None
    data = read_bytes(shm, _DATA_BODY_OFF, length)
    if not data:
        return None
    return Message.decode(data)
