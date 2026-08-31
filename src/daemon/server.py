"""共享内存服务器 — DaemonServer

后台守护进程的主循环，负责：
- 轮询请求信箱（Mailbox），发现 PENDING 请求后开线程处理
- 绑定成功后通过共享内存发布 PID + 状态 + 心跳（单实例检测）
- 信号注册与处理
- 资源清理（会话停止、共享内存释放）
- 认证令牌定时轮换

无 socket、无端口、无锁文件，全部进程间通信基于共享内存。
"""

import os
import time
import signal
import logging
import threading
from typing import Optional

from ..config import (
    MMAP_DAEMON_INFO_NAME,
    MMAP_DAEMON_INFO_SIZE,
    REQ_SHM_SIZE,
    RESP_SHM_SIZE,
    DAEMON_POLL_INTERVAL,
    DAEMON_HEARTBEAT_INTERVAL,
    IS_WINDOWS,
    AUTH_TOKEN_ROTATE_INTERVAL,
    AUTH_TOKEN_GRACE_PERIOD,
)
from ..protocol.shm import (
    Mailbox,
    read_daemon_info,
    read_message,
    write_message,
    write_daemon_info_handle,
    _DATA_BODY_OFF,
)
from ..protocol.shm_utils import open_shm, close_shm
from ..session.shm_utils import (
    generate_auth_token,
    write_auth_token,
)
from ..session.manager import SessionManager
from .handler import RequestHandler

_logger = logging.getLogger("pty-daemon")


class DaemonServer:
    """后台守护进程 — 共享内存信箱服务器

    负责：
    - 信箱轮询主循环（替代 TCP accept）
    - 发布守护进程信息（PID + 状态 + 心跳）到共享内存
    - 信号注册与处理
    - 资源清理（会话停止、共享内存释放）
    - 认证令牌定时轮换
    """

    def __init__(self):
        self.manager = SessionManager()
        self._running = False
        self._cleaned_up = False
        self._info_shm = None               # 守护进程信息区句柄（必须持有，否则 Windows 映射被回收）
        self._auth_shm = None               # 认证令牌共享内存句柄
        self._auth_token: str = generate_auth_token()
        self._rotate_timer: Optional[threading.Timer] = None
        self._mailbox = Mailbox(keep_open=True)
        self._handler: Optional[RequestHandler] = None
        self._my_pid: int = os.getpid()
        self._last_heartbeat: float = 0.0

    def _schedule_rotate(self):
        """安排下一次令牌轮换"""
        self._rotate_timer = threading.Timer(
            AUTH_TOKEN_ROTATE_INTERVAL, self._rotate_token,
        )
        self._rotate_timer.daemon = True
        self._rotate_timer.start()

    def _rotate_token(self):
        """生成新令牌并推送到共享内存和 RequestHandler"""
        old_token = self._auth_token
        self._auth_token = generate_auth_token()
        try:
            self._auth_shm.close()
        except Exception:
            pass
        self._auth_shm = write_auth_token(self._auth_token)
        if self._handler:
            self._handler.add_valid_token(self._auth_token, old_token)
        _logger.info("认证令牌已轮换（旧令牌 %d 秒宽限期）", AUTH_TOKEN_GRACE_PERIOD)
        self._schedule_rotate()

    def _update_heartbeat(self):
        """周期性刷新心跳时间戳"""
        now = time.time()
        if now - self._last_heartbeat < DAEMON_HEARTBEAT_INTERVAL:
            return
        self._last_heartbeat = now
        if self._info_shm is not None:
            try:
                write_daemon_info_handle(self._info_shm, self._my_pid, True, now)
            except Exception:
                pass

    def run(self):
        """启动服务器主循环"""
        # 单实例检查：共享内存已有存活守护进程则拒绝启动
        existing = read_daemon_info()
        if existing is not None:
            existing_pid, existing_running, existing_hb = existing
            from .lifecycle import _pid_exists, _heartbeat_fresh
            if existing_running and _pid_exists(existing_pid) and _heartbeat_fresh(existing_hb):
                _logger.error("守护进程已在运行 (PID:%d)，拒绝启动", existing_pid)
                raise RuntimeError(f"守护进程已在运行 (PID:{existing_pid})")

        # 写入认证令牌（先于守护进程信息，防止客户端在 token 就绪前请求）
        try:
            self._auth_shm = write_auth_token(self._auth_token)
        except Exception as e:
            _logger.error("写入认证令牌失败: %s", e)
            raise

        # 发布守护进程信息并持有句柄
        self._info_shm = open_shm(MMAP_DAEMON_INFO_NAME, MMAP_DAEMON_INFO_SIZE)
        if self._info_shm is None:
            _logger.error("创建守护进程信息区失败")
            raise RuntimeError("创建共享内存失败")
        self._last_heartbeat = time.time()
        write_daemon_info_handle(self._info_shm, self._my_pid, True, self._last_heartbeat)
        _logger.info("守护进程信息已发布 PID:%d", self._my_pid)

        self._handler = RequestHandler(self.manager, server=self, auth_token=self._auth_token)
        self._schedule_rotate()
        self._running = True

        def _signal_handler(signum, frame):
            _logger.info("收到信号 %s，关闭守护进程...", signum)
            self._running = False
        signal.signal(signal.SIGTERM, _signal_handler)
        if not IS_WINDOWS:
            signal.signal(signal.SIGHUP, _signal_handler)

        _logger.info("守护进程启动，轮询信箱（PID:%d）", self._my_pid)

        try:
            while self._running:
                if not self._verify_shm():
                    _logger.error("共享内存被覆盖，检测到另一个守护进程启动，自动退出")
                    break
                self._update_heartbeat()
                slot = self._mailbox.find_pending()
                if slot is not None:
                    info = self._mailbox.get_slot_info(slot)
                    t = threading.Thread(
                        target=self._handle_slot,
                        args=(slot, info),
                        daemon=True,
                        name=f"req-{slot}",
                    )
                    t.start()
                time.sleep(DAEMON_POLL_INTERVAL)
        finally:
            self._cleanup()

    def _handle_slot(self, slot: int, info: dict):
        """处理一个信箱槽位：读请求 → 派发 → 写响应 → 标记 DONE

        Args:
            slot: 槽位索引。
            info: 槽位内容（req_name / resp_name / token 等）。
        """
        req_name = info.get("req_name", "")
        resp_name = info.get("resp_name", "")
        slot_token = info.get("token", "")
        msg = None
        resp: dict = {"type": "error", "error": "服务器内部错误"}

        try:
            req_shm = open_shm(req_name, REQ_SHM_SIZE, create=False)
            if req_shm is None:
                resp = {"type": "error", "error": "请求通道不可用"}
            else:
                try:
                    msg = read_message(req_shm)
                finally:
                    close_shm(req_shm)
                if msg is None:
                    resp = {"type": "error", "error": "请求解析失败"}
                else:
                    # 从槽位注入认证令牌（请求体中的 token 不信任）
                    msg["token"] = slot_token or msg.get("token", "")
                    resp = self._handler.handle(msg)
        except Exception as e:
            _logger.error("请求处理异常: %s", e, exc_info=True)

        # 写入响应通道
        resp_shm = open_shm(resp_name, RESP_SHM_SIZE, create=False)
        if resp_shm is not None:
            try:
                write_message(resp_shm, resp, RESP_SHM_SIZE - _DATA_BODY_OFF)
            finally:
                close_shm(resp_shm)
        else:
            _logger.warning("响应通道 %s 不可用", resp_name)

        self._mailbox.mark_done(slot)
        _logger.debug("槽位 %d 处理完成 (type=%s)", slot, msg.get("type") if msg else "?")

        # stop 请求：响应写入完成后停止服务器
        if msg and msg.get("type") == "stop":
            _logger.info("收到停止命令，关闭守护进程...")
            self.stop()

    def stop(self):
        """停止服务器"""
        self._running = False
        self._cleanup()

    def _verify_shm(self) -> bool:
        """检查守护进程信息区是否仍属于当前进程。

        如果被另一个实例覆盖（PID 不同），返回 False，调用方应退出。
        """
        try:
            info = read_daemon_info()
            if info is None:
                return True
            if info[0] != self._my_pid:
                _logger.warning("守护进程信息区变更: 期望 PID %d，实际 %d",
                                self._my_pid, info[0])
                return False
        except Exception:
            pass
        return True

    def _cleanup(self):
        """清理资源：停止所有会话 + 释放共享内存"""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._running = False
        if self._rotate_timer:
            self._rotate_timer.cancel()
            self._rotate_timer = None
        self.manager.stop_all()
        close_shm(self._info_shm)
        self._info_shm = None
        close_shm(self._auth_shm)
        self._auth_shm = None
        self._mailbox.close()
        _logger.info("守护进程已停止")
