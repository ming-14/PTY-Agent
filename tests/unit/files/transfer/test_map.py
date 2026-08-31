"""TransferMap 单元测试 —— SQLite 持久化与读写"""

import pytest

from src.files.transfer.map import TransferMap, TransferRecord


@pytest.fixture
def tmap():
    return TransferMap(db_path=":memory:")


class TestTransferMap:
    def test_get_empty(self, tmap):
        assert tmap.get("/x") is None

    def test_upsert_and_get(self, tmap):
        tmap.upsert("/x", cli_size=100, cli_mtime=1.0, remote_mtime=2.0)
        rec = tmap.get("/x")
        assert rec == TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=2.0)

    def test_upsert_overwrites(self, tmap):
        tmap.upsert("/x", cli_size=100, cli_mtime=1.0, remote_mtime=2.0)
        tmap.upsert("/x", cli_size=200, cli_mtime=3.0, remote_mtime=4.0)
        assert tmap.get("/x") == TransferRecord(cli_size=200, cli_mtime=3.0, remote_mtime=4.0)

    def test_distinct_paths(self, tmap):
        tmap.upsert("/a", 1, 1.0, 1.0)
        tmap.upsert("/b", 2, 2.0, 2.0)
        assert tmap.get("/a").cli_size == 1
        assert tmap.get("/b").cli_size == 2

    def test_persistent_across_instances(self, tmp_path):
        db = str(tmp_path / "history.db")
        TransferMap(db_path=db).upsert("/x", 10, 1.0, 2.0)
        reopened = TransferMap(db_path=db)
        assert reopened.get("/x") == TransferRecord(cli_size=10, cli_mtime=1.0, remote_mtime=2.0)

    def test_clear(self, tmap):
        tmap.upsert("/x", 1, 1.0, 1.0)
        tmap.clear()
        assert tmap.get("/x") is None