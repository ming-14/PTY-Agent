"""插件存储单测 — kv/文件/sqlite 三种视图、惰性建目录、越界防护"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.storage import FileStore, KvStore, PluginStorage  # noqa: E402


class TestKvStore:
    def test_set_get_delete(self, tmp_path):
        store = KvStore(str(tmp_path / "s.json"))
        store.set("k", {"nested": 1})
        assert store.get("k") == {"nested": 1}
        assert store.get("missing", "d") == "d"
        assert store.keys() == ["k"]
        assert store.delete("k") is True
        assert store.delete("k") is False

    def test_persists_to_disk(self, tmp_path):
        path = str(tmp_path / "s.json")
        KvStore(path).set("a", 1)
        reloaded = KvStore(path)
        assert reloaded.get("a") == 1

    def test_corrupt_file_resets(self, tmp_path):
        path = str(tmp_path / "s.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt")
        store = KvStore(path)
        assert store.as_dict() == {}


class TestFileStore:
    def test_write_read_delete(self, tmp_path):
        store = FileStore(str(tmp_path))
        path = store.write("dir/a.txt", b"hello")
        assert os.path.isfile(path)
        assert store.read("dir/a.txt") == b"hello"
        assert store.list_files() == [os.path.join("dir", "a.txt")]
        assert store.list_files(os.path.join("dir", "")) == [os.path.join("dir", "a.txt")]
        assert store.delete("dir/a.txt") is True
        assert store.delete("dir/a.txt") is False

    def test_path_escape_rejected(self, tmp_path):
        store = FileStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.read("../outside.txt")


class TestPluginStorage:
    def test_lazy_root_creation(self, tmp_path):
        root = str(tmp_path / "data")
        storage = PluginStorage(root)
        assert not os.path.exists(root)
        storage.kv("state").set("a", 1)
        assert os.path.isdir(root)
        assert os.path.isfile(os.path.join(root, "state.json"))

    def test_views_share_root(self, tmp_path):
        storage = PluginStorage(str(tmp_path / "data"))
        storage.kv("state").set("a", 1)
        storage.files("uploads").write("f.txt", b"x")
        assert storage.sqlite("db").endswith("db.db")
        assert os.path.isfile(os.path.join(storage.root, "uploads", "f.txt"))

    def test_clear_removes_root(self, tmp_path):
        root = str(tmp_path / "data")
        storage = PluginStorage(root)
        storage.kv("state").set("a", 1)
        storage.clear()
        assert not os.path.exists(root)

    def test_kv_views_isolated(self, tmp_path):
        storage = PluginStorage(str(tmp_path / "data"))
        storage.kv("a").set("k", 1)
        assert storage.kv("b").get("k") is None
