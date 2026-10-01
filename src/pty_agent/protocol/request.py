"""共享内存请求门面 — 统一往返（协议层）

功能：一次「请求 JSON → 共享内存往返 → 响应 JSON」的完整封装。
客户端侧所有命令（CLI / 未来 MCP）与守护进程管控（stop/ping）都经由
本模块的 roundtrip() 与守护进程通信，是 client 与 daemon 之间
唯一约定的通信入口。

往返流程：
  1. 读取认证令牌
  2. 创建请求/响应命名共享内存通道（PTYAgentReq_{pid}_{seq} / PTYAgentResp_{pid}_{seq}）
  3. 写入请求 JSON
  4. 抢占信箱槽位并注册
  5. 轮询等待 DONE
  6. 读取响应 JSON 并释放槽位
"""

import logging
import os
import time
from typing import Optional

from ..config import (
    REQ_SHM_SIZE,
    RESP_SHM_SIZE,
    DEFAULT_TRIGGER_TIMEOUT,
)
from .auth import read_auth_token
from .shm import (
    Mailbox,
    make_channel_names,
    read_message,
    write_message,
    _DATA_BODY_OFF,
)
from .shm_utils import open_shm, close_shm

_logger = logging.getLogger("pty-protocol")


def roundtrip(msg: dict, timeout: Optional[float] = None) -> dict:
    """通过共享内存向守护进程发送请求并接收响应（完整一次往返）

    Args:
        msg:     请求消息字典（type/id/command 等）。
        timeout: 业务等待超时（秒）。None 时按 DEFAULT_TRIGGER_TIMEOUT 计算。

    Returns:
        响应字典（type: result/ok/error/pong 等）。
        通信失败时返回 {"type": "error", "error": "..."}，不抛出。
    """
    token = read_auth_token() or ""
    seq = int(time.time() * 1000) % 100000
    req_name, resp_name = make_channel_names(os.getpid(), seq)

    req_shm = open_shm(req_name, REQ_SHM_SIZE)
    resp_shm = open_shm(resp_name, RESP_SHM_SIZE)
    if req_shm is None or resp_shm is None:
        close_shm(req_shm)
        close_shm(resp_shm)
        _logger.error("创建共享内存通道失败")
        return {"type": "error", "error": "创建共享内存通信通道失败"}

    mailbox = Mailbox()
    slot = None
    try:
        # 注入认证令牌
        msg["token"] = token
        # 写入请求
        write_message(req_shm, msg, REQ_SHM_SIZE - _DATA_BODY_OFF,
                      truncated_marker=False)
        # 注册到信箱
        slot = mailbox.acquire_slot(os.getpid(), req_name, resp_name, token, seq)
        if slot is None:
            _logger.error("请求信箱已满")
            return {"type": "error", "error": "请求信箱已满，请稍后重试"}
        # 计算等待超时（业务超时 + 处理余量）
        wait_timeout = (timeout if timeout is not None
                        else DEFAULT_TRIGGER_TIMEOUT) + 5
        if not mailbox.wait_done(slot, timeout=wait_timeout):
            _logger.warning("请求超时 (type=%s, timeout=%s)",
                            msg.get("type"), wait_timeout)
            return {"type": "error", "error": "守护进程响应超时"}
        resp = read_message(resp_shm)
        if resp is None:
            _logger.warning("读取响应失败")
            return {"type": "error", "error": "读取响应失败"}
        return resp
    finally:
        if slot is not None:
            mailbox.release_slot(slot)
        close_shm(req_shm)
        close_shm(resp_shm)
