"""共享内存工具单元测试

测试认证令牌生成/读写、守护进程信息区（PID+状态+心跳）读写、
请求/响应通道、信箱槽位状态机。
"""

import os
import time
import pytest

from src.session.shm_utils import (
    generate_auth_token,
    read_auth_token,
    write_auth_token,
    cleanup_auth_shm,
)
from src.protocol.shm import (
    write_daemon_info_handle,
    read_daemon_info,
    cleanup_daemon_info,
    Mailbox,
    make_channel_names,
    write_message,
    read_message,
)
from src.protocol.shm_utils import open_shm, close_shm
from src.config import (
    IS_WINDOWS, REQ_SHM_SIZE, RESP_SHM_SIZE, MAILBOX_SLOT_COUNT,
    MMAP_DAEMON_INFO_NAME, MMAP_DAEMON_INFO_SIZE,
)


@pytest.fixture(autouse=True)
def _cleanup_shm():
    """清理共享内存残留，避免测试间污染"""
    cleanup_daemon_info()
    yield
    cleanup_daemon_info()


class TestGenerateAuthToken:
    """generate_auth_token 测试"""

    def test_returns_non_empty_string(self):
        token = generate_auth_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_is_hex(self):
        token = generate_auth_token()
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_length(self):
        token = generate_auth_token()
        assert len(token) == 64

    def test_unique_tokens(self):
        t1 = generate_auth_token()
        t2 = generate_auth_token()
        assert t1 != t2


class TestDaemonInfo:
    """守护进程信息区（PID+状态+心跳）测试

    注意：Windows 下命名映射由句柄引用保持存活，与真实守护进程
    （持有 _info_shm）保持一致，测试中也需持有句柄。
    """

    @pytest.fixture(autouse=True)
    def _held_handle(self):
        shm = open_shm(MMAP_DAEMON_INFO_NAME, MMAP_DAEMON_INFO_SIZE)
        assert shm is not None
        yield shm
        close_shm(shm)

    def test_read_none_when_empty(self, _held_handle):
        assert read_daemon_info() is None

    def test_write_and_read(self, _held_handle):
        write_daemon_info_handle(_held_handle, 12345, True, time.time())
        info = read_daemon_info()
        assert info is not None
        pid, running, heartbeat = info
        assert pid == 12345
        assert running is True
        assert abs(time.time() - heartbeat) < 5

    def test_write_stopped_state(self, _held_handle):
        write_daemon_info_handle(_held_handle, 12345, False, time.time())
        info = read_daemon_info()
        assert info is not None
        assert info[1] is False

    def test_write_overwrite(self, _held_handle):
        write_daemon_info_handle(_held_handle, 11111, True, time.time())
        write_daemon_info_handle(_held_handle, 22222, True, time.time())
        info = read_daemon_info()
        assert info is not None
        assert info[0] == 22222

    def test_large_pid(self, _held_handle):
        write_daemon_info_handle(_held_handle, 999999, True, time.time())
        info = read_daemon_info()
        assert info is not None
        assert info[0] == 999999

    def test_cleanup(self, _held_handle):
        write_daemon_info_handle(_held_handle, 12345, True, time.time())
        cleanup_daemon_info()
        assert read_daemon_info() is None


class TestChannelNames:
    """请求/响应通道命名测试"""

    def test_names_unique_per_pid_seq(self):
        n1 = make_channel_names(100, 1)
        n2 = make_channel_names(100, 2)
        n3 = make_channel_names(200, 1)
        assert n1[0] != n2[0]
        assert n1[0] != n3[0]
        assert n1[1] != n2[1]

    def test_names_short_enough_for_slot(self):
        req, resp = make_channel_names(999999, 99999)
        assert len(req) <= 64
        assert len(resp) <= 64


class TestRequestResponseChannel:
    """请求/响应共享内存通道测试"""

    def test_write_and_read_message(self):
        req_name, resp_name = make_channel_names(os.getpid(), 1)
        req_shm = open_shm(req_name, REQ_SHM_SIZE)
        resp_shm = open_shm(resp_name, RESP_SHM_SIZE)
        try:
            ok = write_message(req_shm, {"type": "ping"}, REQ_SHM_SIZE - 16,
                               truncated_marker=False)
            assert ok is True
            msg = read_message(req_shm)
            assert msg == {"type": "ping"}
            # 写响应
            ok = write_message(resp_shm, {"type": "pong"}, RESP_SHM_SIZE - 16)
            assert ok is True
            resp = read_message(resp_shm)
            assert resp == {"type": "pong"}
        finally:
            close_shm(req_shm)
            close_shm(resp_shm)

    def test_write_large_message_truncates(self):
        resp_name = make_channel_names(os.getpid(), 2)[1]
        resp_shm = open_shm(resp_name, RESP_SHM_SIZE)
        try:
            # 用很小的 max_size 触发截断
            big = {"type": "result", "output": "x" * 10000}
            ok = write_message(resp_shm, big, 4096)
            assert ok is True
            resp = read_message(resp_shm)
            assert resp is not None
            assert resp["truncated"] is True
            assert len(resp["output"]) < len(big["output"])
        finally:
            close_shm(resp_shm)

    def test_request_oversize_fails(self):
        req_name = make_channel_names(os.getpid(), 3)[0]
        req_shm = open_shm(req_name, REQ_SHM_SIZE)
        try:
            # 超过 256KB 且非 output 消息 → 写入失败
            big = {"type": "exec", "command": "x" * (REQ_SHM_SIZE)}
            ok = write_message(req_shm, big, REQ_SHM_SIZE - 16,
                               truncated_marker=False)
            assert ok is False
        finally:
            close_shm(req_shm)


class TestMailbox:
    """请求信箱测试

    注意：Windows 下命名映射由句柄引用保持存活。真实守护进程
    使用 Mailbox(keep_open=True) 持有映射，测试中也需保持打开。
    """

    def test_acquire_and_release(self):
        mailbox = Mailbox(keep_open=True)
        req_name, resp_name = make_channel_names(os.getpid(), 10)
        slot = mailbox.acquire_slot(os.getpid(), req_name, resp_name, "tok", 10)
        assert slot is not None
        info = mailbox.get_slot_info(slot)
        assert info["req_name"] == req_name
        assert info["resp_name"] == resp_name
        assert info["token"] == "tok"
        mailbox.release_slot(slot)
        mailbox.close()

    def test_find_pending_flow(self):
        mailbox = Mailbox(keep_open=True)
        req_name, resp_name = make_channel_names(os.getpid(), 11)
        slot = mailbox.acquire_slot(os.getpid(), req_name, resp_name, "tok", 11)
        assert slot is not None

        # 守护进程侧发现并标记 PROCESSING
        found = mailbox.find_pending()
        assert found == slot
        # 第二次扫描无 PENDING
        assert mailbox.find_pending() is None

        # 标记 DONE，客户端可感知
        mailbox.mark_done(slot)
        assert mailbox.wait_done(slot, timeout=2.0) is True
        mailbox.release_slot(slot)
        mailbox.close()

    def test_wait_done_timeout(self):
        mailbox = Mailbox(keep_open=True)
        req_name, resp_name = make_channel_names(os.getpid(), 12)
        slot = mailbox.acquire_slot(os.getpid(), req_name, resp_name, "tok", 12)
        try:
            assert mailbox.wait_done(slot, timeout=0.2) is False
        finally:
            mailbox.release_slot(slot)
            mailbox.close()

    def test_mailbox_full(self):
        mailbox = Mailbox(keep_open=True)
        slots = []
        try:
            for i in range(MAILBOX_SLOT_COUNT + 1):
                req_name, resp_name = make_channel_names(os.getpid(), 1000 + i)
                s = mailbox.acquire_slot(os.getpid(), req_name, resp_name, "tok", i)
                if s is None:
                    break
                slots.append(s)
            assert len(slots) == MAILBOX_SLOT_COUNT
        finally:
            for s in slots:
                mailbox.release_slot(s)
            mailbox.close()


class TestAuthTokenShm:
    """认证令牌共享内存读写测试"""

    def test_write_and_read_token(self):
        test_token = generate_auth_token()
        shm = write_auth_token(test_token)
        try:
            read = read_auth_token()
            assert read == test_token
        finally:
            if shm:
                try:
                    shm.close()
                except Exception:
                    pass

    def test_cleanup_auth_shm_no_error(self):
        cleanup_auth_shm()
        cleanup_auth_shm()  # 幂等