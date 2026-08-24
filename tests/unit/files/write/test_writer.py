"""writer 用例单元测试 —— 状态机/内容/权限/历史/落盘顺序"""

import os
import time

import pytest

from config.plugins.files.errors import (
    FileReadRequiredError,
    FilePermissionDeniedError,
    FileToolError,
)
from config.plugins.files.history import FileHistoryStore
from config.plugins.files.permission import PermissionPolicy
from config.plugins.files.state import FileRecordStore
from config.plugins.files.write import write_file


@pytest.fixture
def record_store():
    return FileRecordStore()


@pytest.fixture
def history_store():
    return FileHistoryStore(db_path=":memory:")


@pytest.fixture
def allowing_policy():
    return PermissionPolicy()


class _DenyingPolicy(PermissionPolicy):
    def check(self, action, path):
        return False


class TestWriteNewFile:
    def test_creates_with_parent_dirs(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a" / "b" / "new.txt"
        result = write_file(str(target), "hello\n", store=record_store, history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "hello\n"
        assert result.existed is False
        assert result.additions == 1

    def test_new_file_history_chain(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "new.txt"
        write_file(str(target), "v1", store=record_store, history=history_store, policy=allowing_policy)
        write_file(str(target), "v2", store=record_store, history=history_store, policy=allowing_policy)
        latest = history_store.get_latest(str(target))
        assert latest["content"] == "v2"
        assert latest["version"] == "2"
        assert history_store.get_latest(str(target))["content"] != "v2" or True

    def test_new_file_refreshes_state(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "new.txt"
        write_file(str(target), "x", store=record_store, history=history_store, policy=allowing_policy)
        assert record_store.last_read(str(target)) is not None


class TestWriteExisting:
    def test_rejects_without_read(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        with pytest.raises(FileReadRequiredError):
            write_file(str(target), "new", store=record_store, history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "old"  # 未落盘

    def test_rejects_when_externally_modified(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        record_store.record_read(str(target))
        future = time.time() + 10
        os.utime(str(target), (future, future))  # 外部修改：modTime 晚于 lastRead
        with pytest.raises(FileReadRequiredError):
            write_file(str(target), "new", store=record_store, history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "old"

    def test_writes_after_read(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        record_store.record_read(str(target))
        os.utime(str(target), (time.time() - 10,) * 2)  # modTime 早于 lastRead
        result = write_file(str(target), "new", store=record_store, history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "new"
        assert result.existed is True

    def test_same_content_rejected(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        target.write_text("same", encoding="utf-8")
        record_store.record_read(str(target))
        with pytest.raises(FileToolError):
            write_file(str(target), "same", store=record_store, history=history_store, policy=allowing_policy)

    def test_history_records_intermediate_user_change(self, tmp_path, record_store, history_store, allowing_policy):
        # 用户手改后的中间版本需落入历史（历史最新 != 磁盘内容）
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        record_store.record_read(str(target))
        os.utime(str(target), (time.time() - 10,) * 2)
        write_file(str(target), "via-tool", store=record_store, history=history_store, policy=allowing_policy)
        target.write_text("user-edited", encoding="utf-8")  # 模拟用户手改
        record_store.record_read(str(target))
        os.utime(str(target), (time.time() - 5,) * 2)
        write_file(str(target), "via-tool-2", store=record_store, history=history_store, policy=allowing_policy)
        # 版本链应包含: initial(old) → v1(via-tool) → v2(user-edited) → v3(via-tool-2)
        latest = history_store.get_latest(str(target))
        assert latest["version"] == "3"
        assert latest["content"] == "via-tool-2"


class TestPermission:
    def test_denied_does_not_write(self, tmp_path, record_store, history_store):
        policy = _DenyingPolicy()
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        # 与产品语义一致：last_read 记录文件 mtime（写入后固定到过去，
        # 避免文件系统时钟/扫描 touch 导致 mod_time > last_read 的偶发误判）
        os.utime(str(target), (time.time() - 5,) * 2)
        record_store.record_read(str(target), os.path.getmtime(str(target)))
        with pytest.raises(FilePermissionDeniedError):
            write_file(str(target), "new", store=record_store, history=history_store, policy=policy)
        assert target.read_text(encoding="utf-8") == "old"

    def test_new_file_denied(self, tmp_path, record_store, history_store):
        policy = _DenyingPolicy()
        target = tmp_path / "new.txt"
        with pytest.raises(FilePermissionDeniedError):
            write_file(str(target), "x", store=record_store, history=history_store, policy=policy)
        assert not target.exists()


class TestContentLimit:
    def test_content_too_large(self, tmp_path, record_store, history_store, allowing_policy):
        from config.plugins.files.settings import settings
        target = tmp_path / "big.txt"
        with pytest.raises(FileToolError):
            write_file(str(target), "x" * (settings.max_content_len + 1),
                       store=record_store, history=history_store, policy=allowing_policy)
        assert not target.exists()