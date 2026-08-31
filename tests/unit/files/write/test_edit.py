"""edit 用例单元测试 —— create/replace/delete 三分支与唯一匹配校验"""

import os
import time

import pytest

from src.files.errors import (
    FileReadRequiredError,
    FilePermissionDeniedError,
    FileToolError,
)
from src.files.history import FileHistoryStore
from src.files.permission import PermissionPolicy
from src.files.state import FileRecordStore
from src.files.write import edit_file


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


def _write_read(path, text, record_store):
    """写文件并模拟一次 file read（状态机检查的前置条件）"""
    path.write_text(text, encoding="utf-8")
    record_store.record_read(str(path))
    os.utime(str(path), (time.time() - 10,) * 2)  # modTime 早于 lastRead


class TestEditCreate:
    def test_create_when_not_exists(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "new.txt"
        result = edit_file(str(target), "", "brand new", store=record_store,
                           history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "brand new"
        assert result.existed is False

    def test_create_rejects_existing(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "old", record_store)
        with pytest.raises(FileToolError):
            edit_file(str(target), "", "new", store=record_store,
                      history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "old"

    def test_both_empty_rejected(self, tmp_path, record_store, history_store, allowing_policy):
        with pytest.raises(FileToolError):
            edit_file(str(tmp_path / "x.txt"), "", "", store=record_store,
                      history=history_store, policy=allowing_policy)


class TestEditReplace:
    def test_replace_unique(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "hello world\n", record_store)
        result = edit_file(str(target), "world", "earth", store=record_store,
                           history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "hello earth\n"
        assert result.existed is True

    def test_replace_requires_read(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")  # 未 record_read
        with pytest.raises(FileReadRequiredError):
            edit_file(str(target), "old", "new", store=record_store,
                      history=history_store, policy=allowing_policy)

    def test_replace_rejects_external_modification(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "old", record_store)
        os.utime(str(target), (time.time() + 10,) * 2)  # 外部修改
        with pytest.raises(FileReadRequiredError):
            edit_file(str(target), "old", "new", store=record_store,
                      history=history_store, policy=allowing_policy)

    def test_replace_not_found(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "abc", record_store)
        with pytest.raises(FileToolError):
            edit_file(str(target), "zzz", "new", store=record_store,
                      history=history_store, policy=allowing_policy)

    def test_replace_non_unique(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "x x x", record_store)
        with pytest.raises(FileToolError) as exc:
            edit_file(str(target), "x", "y", store=record_store,
                      history=history_store, policy=allowing_policy)
        assert "not unique" in str(exc.value)

    def test_replace_requires_existing_file(self, tmp_path, record_store, history_store, allowing_policy):
        with pytest.raises(FileToolError):
            edit_file(str(tmp_path / "ghost.txt"), "a", "b", store=record_store,
                      history=history_store, policy=allowing_policy)

    def test_replace_old_equals_new_no_changes(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "abc", record_store)
        with pytest.raises(FileToolError):
            edit_file(str(target), "a", "a", store=record_store,
                      history=history_store, policy=allowing_policy)


class TestEditDelete:
    def test_delete_unique(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "keep\nremove\nkeep\n", record_store)
        result = edit_file(str(target), "remove", "", store=record_store,
                           history=history_store, policy=allowing_policy)
        assert target.read_text(encoding="utf-8") == "keep\n\nkeep\n"
        assert result.existed is True

    def test_delete_appends_version_chain(self, tmp_path, record_store, history_store, allowing_policy):
        target = tmp_path / "a.txt"
        _write_read(target, "old-content", record_store)
        edit_file(str(target), "old", "", store=record_store,
                  history=history_store, policy=allowing_policy)
        latest = history_store.get_latest(str(target))
        assert latest["version"] == "1"
        assert latest["content"] == "-content"


class TestEditPermission:
    def test_denied_does_not_write(self, tmp_path, record_store, history_store):
        policy = _DenyingPolicy()
        target = tmp_path / "a.txt"
        _write_read(target, "old", record_store)
        with pytest.raises(FilePermissionDeniedError):
            edit_file(str(target), "old", "new", store=record_store,
                      history=history_store, policy=policy)
        assert target.read_text(encoding="utf-8") == "old"