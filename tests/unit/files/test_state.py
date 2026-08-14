"""config/plugins/files/state.py 单元测试 — 读写状态机"""

import time

from config.plugins.files.state import FileRecordStore
from config.plugins.files.paths import normalize_key


class TestFileRecordStore:
    def test_never_read_returns_none(self):
        store = FileRecordStore()
        assert store.last_read("C:/x/a.txt") is None

    def test_record_read_then_last_read(self):
        store = FileRecordStore()
        store.record_read("C:/x/a.txt")
        assert store.last_read("C:/x/a.txt") is not None

    def test_write_updates_write_only_not_read(self):
        store = FileRecordStore()
        store.record_write("C:/x/a.txt")
        assert store.last_read("C:/x/a.txt") is None

    def test_reset_clears(self):
        store = FileRecordStore()
        store.record_read("C:/x/a.txt")
        store.reset()
        assert store.last_read("C:/x/a.txt") is None

    def test_key_normalization_windows_case(self):
        store = FileRecordStore()
        store.record_read("C:/X/A.TXT")
        assert store.last_read(normalize_key("c:/x/a.txt")) is not None

    def test_read_time_is_monotonic(self):
        store = FileRecordStore()
        store.record_read("a")
        first = store.last_read("a")
        time.sleep(0.01)
        store.record_read("a")
        second = store.last_read("a")
        assert second > first

    def test_independent_paths(self):
        store = FileRecordStore()
        store.record_read("a")
        assert store.last_read("b") is None

    def test_thread_safety(self):
        import threading

        store = FileRecordStore()
        errors = []

        def worker(path):
            try:
                for _ in range(100):
                    store.record_read(path)
                    store.last_read(path)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=("p%d" % i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []