"""文件版本历史单元测试 —— 版本链/幂等/隔离"""

import pytest

from config.plugins.files.history import FileHistoryStore


@pytest.fixture
def store():
    return FileHistoryStore(db_path=":memory:")


class TestFileHistoryStore:
    def test_get_latest_empty(self, store):
        assert store.get_latest("nope.txt") is None

    def test_create_initial_then_versions(self, store):
        store.create("a.txt", "old")
        assert store.get_latest("a.txt")["version"] == "0"
        v = store.create_version("a.txt", "new")
        assert v == "1"
        latest = store.get_latest("a.txt")
        assert latest["content"] == "new"
        assert latest["version"] == "1"

    def test_version_increments_per_path(self, store):
        store.create("a.txt", "a0")
        store.create("b.txt", "b0")
        store.create_version("a.txt", "a1")
        store.create_version("a.txt", "a2")
        assert store.get_latest("a.txt")["version"] == "2"
        assert store.get_latest("b.txt")["version"] == "0"

    def test_create_idempotent(self, store):
        store.create("a.txt", "x")
        store.create("a.txt", "y")
        versions = store.get_latest("a.txt")
        assert versions["content"] == "x"  # 首次内容不被第二次覆盖

    def test_version_order_beyond_nine(self, store):
        # 版本号按整数排序，避免字符串排序 "v10" < "v9" 问题
        store.create("a.txt", "c0")
        for i in range(1, 11):
            store.create_version("a.txt", "c%d" % i)
        latest = store.get_latest("a.txt")
        assert latest["version"] == "10"
        assert latest["content"] == "c10"

    def test_paths_isolated(self, store):
        store.create_version("x.txt", "x1")
        assert store.get_latest("y.txt") is None