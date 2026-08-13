"""file upload/download 集成测试 —— loopback TCP 全链路（握手+二进制帧+落盘+历史+映射）

daemon 侧：真实 handler + 注入 :memory: history/tmap/state，线程内跑
client 侧：真实 upload/download 驱动，走完整协议
不依赖真实 PTY 会话（FakeManager 提供固定 cwd，同现有 handler 单测模式）
"""

import os
import socket
import threading
import time

import pytest

from src.daemon.handlers.base import HandlerContext
from src.daemon.handlers.file_download_handler import FileDownloadHandler
from src.daemon.handlers.file_upload_handler import FileUploadHandler
from src.files.history import FileHistoryStore
from src.files.state import FileRecordStore
from src.files.transfer.client_download import download
from src.files.transfer.client_upload import upload
from src.files.transfer.common import TransferError
from src.files.transfer.map import TransferMap


class _FakeSession:
    cwd = ""


class _FakeManager:
    def __init__(self, cwd):
        _FakeSession.cwd = cwd

    def get_session(self, session_id):
        return _FakeSession() if session_id == "sid" else None


class _DaemonSide:
    """在独立线程中运行 handler（真实 TCP 连接，仿 dispatcher 先 recv 握手 JSON）"""

    def __init__(self, handler, cwd, inject=None):
        self._handler = handler
        self._manager = _FakeManager(cwd)
        self._inject = inject or {}
        self.error = None

    def start(self, conn):
        def run():
            try:
                from src.protocol.message import Message
                ctx = HandlerContext(manager=self._manager)
                msg = Message.recv(conn)  # 仿 dispatcher：先读握手 JSON
                if msg is None:
                    return
                self._handler.handle(ctx, conn, msg, **self._inject)
            except Exception as e:  # 线程内异常上报主线程断言
                self.error = e
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def join(self):
        self._thread.join(timeout=15)


@pytest.fixture
def infra(tmp_path):
    """loopback 连接对 + 注入依赖"""
    return {
        "history": FileHistoryStore(db_path=":memory:"),
        "tmap": TransferMap(db_path=":memory:"),
        "store": FileRecordStore(),
        "tmp": tmp_path,
    }


def _pair():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    daemon_conn, _ = srv.accept()
    srv.close()
    return cli, daemon_conn


def _upload(local, remote_rel, cwd, force=False, timeout=15, infra=None):
    cli, daemon_conn = _pair()
    kwargs = {}
    if infra:
        kwargs = {"history": infra["history"], "tmap": infra["tmap"],
                  "store": infra["store"]}
    side = _DaemonSide(FileUploadHandler(), cwd, kwargs).start(daemon_conn)
    try:
        summary = upload(cli, str(local), remote_rel, "sid", force, timeout)
    finally:
        cli.close()
        side.join()
    assert side.error is None, side.error
    return summary


def _download(remote_rel, local, cwd, force=False, timeout=15, infra=None):
    cli, daemon_conn = _pair()
    kwargs = {"tmap": infra["tmap"]} if infra else {}
    side = _DaemonSide(FileDownloadHandler(), cwd, kwargs).start(daemon_conn)
    try:
        summary = download(cli, str(local), remote_rel, "sid", force, timeout)
    finally:
        cli.close()
        side.join()
    assert side.error is None, side.error
    return summary


class TestUpload:
    def test_single_file_new(self, infra, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello upload")
        os.utime(str(src), (1600000000.0, 1600000000.0))
        remote = tmp_path / "remote" / "src.txt"

        summary = _upload(tmp_path / "src.txt", "remote/src.txt", str(tmp_path), infra=infra)
        assert summary["transferred"] == [""]
        assert summary["failed"] == []
        assert remote.read_bytes() == b"hello upload"
        # mtime 对齐 CLI
        assert os.path.getmtime(str(remote)) == 1600000000.0
        # 文本落 history 版本链
        assert infra["history"].get_latest(str(remote))["content"] == "hello upload"
        # 状态机双刷（后续 file edit 不拒绝）
        assert infra["store"].last_read(str(remote)) is not None
        # 映射 upsert
        rec = infra["tmap"].get(str(remote))
        assert rec.cli_size == 12 and rec.cli_mtime == 1600000000.0

    def test_dir_recursive_with_empty_dir(self, infra, tmp_path):
        tree = tmp_path / "tree"
        (tree / "sub" / "nested").mkdir(parents=True)
        (tree / "sub" / "a.txt").write_bytes(b"a")
        (tree / ".hidden").write_bytes(b"h")

        summary = _upload(tree, "uploaded", str(tmp_path), infra=infra)
        assert set(summary["transferred"]) == {"sub/a.txt", ".hidden"}
        assert summary["failed"] == []
        dst = tmp_path / "uploaded"
        assert (dst / "sub" / "a.txt").read_bytes() == b"a"
        assert (dst / "sub" / "nested").is_dir()  # 空目录也创建
        assert (dst / ".hidden").read_bytes() == b"h"  # 全量不过滤

    def test_binary_file_skips_history(self, infra, tmp_path):
        src = tmp_path / "blob.bin"
        src.write_bytes(bytes(range(256)))
        remote = tmp_path / "remote" / "blob.bin"

        summary = _upload(src, "remote/blob.bin", str(tmp_path), infra=infra)
        assert summary["failed"] == []
        assert remote.read_bytes() == bytes(range(256))
        assert infra["history"].get_latest(str(remote)) is None  # 二进制跳过 history

    def test_same_file_skipped(self, infra, tmp_path):
        src = tmp_path / "f.txt"
        src.write_bytes(b"same content")
        os.utime(str(src), (1600000000.0, 1600000000.0))
        remote = tmp_path / "remote" / "f.txt"

        first = _upload(src, "remote/f.txt", str(tmp_path), infra=infra)
        assert first["transferred"] == [""]
        # 第二次：相同判定命中 → 跳过
        second = _upload(src, "remote/f.txt", str(tmp_path), infra=infra)
        assert second["transferred"] == []
        assert second["skipped"] == [{"relpath": "", "reason": "same file"}]

    def test_different_content_denied_without_force(self, infra, tmp_path):
        src = tmp_path / "f.txt"
        src.write_bytes(b"v1")
        _upload(src, "remote/f.txt", str(tmp_path), infra=infra)
        src.write_bytes(b"v2 different")
        with pytest.raises(TransferError, match="--force"):
            _upload(src, "remote/f.txt", str(tmp_path), infra=infra)
        # 远端未被覆盖
        assert (tmp_path / "remote" / "f.txt").read_bytes() == b"v1"

    def test_force_overwrites_different(self, infra, tmp_path):
        src = tmp_path / "f.txt"
        src.write_bytes(b"v1")
        _upload(src, "remote/f.txt", str(tmp_path), infra=infra)
        src.write_bytes(b"v2 different")
        summary = _upload(src, "remote/f.txt", str(tmp_path), force=True, infra=infra)
        assert summary["transferred"] == [""]
        assert (tmp_path / "remote" / "f.txt").read_bytes() == b"v2 different"

    def test_large_file_multiple_chunks(self, infra, tmp_path):
        src = tmp_path / "big.bin"
        payload = os.urandom(1024 * 1024 + 12345)  # 超过 256KB 分块
        src.write_bytes(payload)
        remote = tmp_path / "remote" / "big.bin"

        summary = _upload(src, "remote/big.bin", str(tmp_path), infra=infra)
        assert summary["failed"] == []
        assert remote.read_bytes() == payload


class TestDownload:
    def test_single_file_new(self, infra, tmp_path):
        remote = tmp_path / "src.txt"
        remote.write_bytes(b"remote content")
        os.utime(str(remote), (1600000000.0, 1600000000.0))
        local = tmp_path / "down" / "src.txt"

        # 显式目标文件路径
        summary = _download("src.txt", local, str(tmp_path), infra=infra)
        assert summary["transferred"] == [""]
        assert local.read_bytes() == b"remote content"
        # 本地 mtime 对齐远端
        assert os.path.getmtime(str(local)) == 1600000000.0
        # 映射记录（cli_mtime=remote_mtime）
        rec = infra["tmap"].get(str(remote))
        assert rec.cli_mtime == 1600000000.0 and rec.remote_mtime == 1600000000.0

    def test_single_file_into_existing_dir(self, infra, tmp_path):
        # scp 语义：目标是已存在目录 → 放入 basename
        remote = tmp_path / "dir" / "note.txt"
        remote.parent.mkdir()
        remote.write_bytes(b"note")
        (tmp_path / "out").mkdir()

        summary = _download("dir/note.txt", tmp_path / "out", str(tmp_path), infra=infra)
        assert summary["transferred"] == [""]
        assert (tmp_path / "out" / "note.txt").read_bytes() == b"note"

    def test_dir_recursive(self, infra, tmp_path):
        tree = tmp_path / "src_tree"
        (tree / "sub").mkdir(parents=True)
        (tree / "sub" / "x.log").write_bytes(b"x")
        (tree / "top.txt").write_bytes(b"t")

        local = tmp_path / "out"
        summary = _download("src_tree", local, str(tmp_path), infra=infra)
        assert set(summary["transferred"]) == {"sub/x.log", "top.txt"}
        assert (local / "sub" / "x.log").read_bytes() == b"x"
        assert (local / "top.txt").read_bytes() == b"t"

    def test_same_file_skipped_then_denied_on_change(self, infra, tmp_path):
        remote = tmp_path / "f.txt"
        remote.write_bytes(b"data")
        local = tmp_path / "out" / "f.txt"

        first = _download("f.txt", local, str(tmp_path), infra=infra)
        assert first["transferred"] == [""]
        # 第二次：相同 → 跳过
        second = _download("f.txt", local, str(tmp_path), infra=infra)
        assert second["transferred"] == []
        assert second["skipped"] == [{"relpath": "", "reason": "same file"}]
        # 本地被修改 → 不同 → 拒绝
        local.write_bytes(b"tampered")
        with pytest.raises(TransferError, match="--force"):
            _download("f.txt", local, str(tmp_path), infra=infra)
        # force → 覆盖
        summary = _download("f.txt", local, str(tmp_path), force=True)
        assert summary["transferred"] == [""]
        assert local.read_bytes() == b"data"

    def test_remote_missing_errors(self, infra, tmp_path):
        cli, daemon_conn = _pair()
        side = _DaemonSide(FileDownloadHandler(), str(tmp_path)).start(daemon_conn)
        try:
            with pytest.raises(TransferError, match="does not exist"):
                download(cli, str(tmp_path / "out"), "nope.txt", "sid", False, 15)
        finally:
            cli.close()
            side.join()

    def test_binary_large_roundtrip(self, infra, tmp_path):
        remote = tmp_path / "blob.bin"
        payload = os.urandom(512 * 1024 + 7)
        remote.write_bytes(payload)
        local = tmp_path / "out" / "blob.bin"

        summary = _download("blob.bin", local, str(tmp_path), infra=infra)
        assert summary["failed"] == []
        assert local.read_bytes() == payload


class TestMisc:
    def test_upload_sets_state_so_edit_works_after(self, infra, tmp_path):
        """upload 后状态机双刷：file edit 可直接替换（不要求先 file read）"""
        src = tmp_path / "f.txt"
        src.write_bytes(b"hello")
        remote = tmp_path / "remote" / "f.txt"
        _upload(src, "remote/f.txt", str(tmp_path), infra=infra)
        from src.files.write.writer import edit_file
        result = edit_file(str(remote), "hello", "world",
                           store=infra["store"], history=infra["history"])
        assert result.path == str(remote)
        assert remote.read_bytes() == b"world"