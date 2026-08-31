"""相同文件判定与传输计划单元测试 —— classify 全分支 + build_plan"""

from src.client.transfer.common import entry
from src.files.transfer.judge import (
    DENIED,
    SKIP,
    TRANSFER,
    build_plan,
    classify,
)
from src.files.transfer.map import TransferRecord


def _cls(remote_exists, remote_size, remote_mtime,
         rec, cli_size, cli_mtime, force):
    return classify(
        remote_exists=remote_exists,
        remote_size=remote_size,
        remote_mtime=remote_mtime,
        record_size=rec.cli_size if rec else None,
        record_cli_mtime=rec.cli_mtime if rec else None,
        record_remote_mtime=rec.remote_mtime if rec else None,
        cli_size=cli_size,
        cli_mtime=cli_mtime,
        force=force,
    )


class TestClassify:
    def test_remote_missing_transfer(self):
        assert _cls(False, 0, 0.0, None, 10, 1.0, False) == TRANSFER

    def test_cli_missing_transfer(self):
        # 下载方向：本地缺失 → 必须传输（不因 size/mtime 0 判定为覆盖）
        assert classify(
            remote_exists=True, remote_size=100, remote_mtime=5.0,
            record_size=None, record_cli_mtime=None, record_remote_mtime=None,
            cli_size=0, cli_mtime=0.0, force=False, cli_exists=False,
        ) == TRANSFER

    def test_size_mismatch_denied_without_force(self):
        assert _cls(True, 100, 5.0, None, 200, 1.0, False) == DENIED

    def test_size_mismatch_transfer_with_force(self):
        assert _cls(True, 100, 5.0, None, 200, 1.0, True) == TRANSFER

    def test_no_record_denied(self):
        # 远端存在、大小相同，但无映射记录 → 无法证明相同 → 拒绝
        assert _cls(True, 100, 5.0, None, 100, 1.0, False) == DENIED

    def test_record_match_skip(self):
        rec = TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=5.0)
        assert _cls(True, 100, 5.0, rec, 100, 1.0, False) == SKIP

    def test_record_match_skip_even_with_force(self):
        # force 不改变相同判定：相同文件永不重传
        rec = TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=5.0)
        assert _cls(True, 100, 5.0, rec, 100, 1.0, True) == SKIP

    def test_cli_mtime_changed_denied(self):
        rec = TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=5.0)
        assert _cls(True, 100, 5.0, rec, 100, 2.0, False) == DENIED

    def test_cli_mtime_changed_transfer_with_force(self):
        rec = TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=5.0)
        assert _cls(True, 100, 5.0, rec, 100, 2.0, True) == TRANSFER

    def test_remote_modified_externally_denied(self):
        # 远端 mtime 偏离记录（被外部修改）→ 视为不同
        rec = TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=5.0)
        assert _cls(True, 100, 9.0, rec, 100, 1.0, False) == DENIED


class TestBuildPlan:
    def test_mixed_plan(self):
        entries = [
            entry("", "dir"),
            entry("a.txt", "file", 100, 1.0),
            entry("b.txt", "file", 200, 2.0),
            entry("sub", "dir"),
            entry("sub/c.txt", "file", 300, 3.0),
        ]
        rec_a = TransferRecord(cli_size=100, cli_mtime=1.0, remote_mtime=1.0)

        def resolver(relpath):
            # a.txt 远端存在且映射命中（skip）；b/c 不存在（transfer）
            if relpath == "a.txt":
                return True, 100, 1.0
            return False, 0, 0.0

        def map_getter(relpath):
            return rec_a if relpath == "a.txt" else None

        plan = build_plan(entries, resolver, map_getter, force=False)
        assert set(plan["mkdirs"]) == {"", "sub"}
        assert plan["transfers"] == ["b.txt", "sub/c.txt"]
        assert plan["skips"] == [{"relpath": "a.txt", "reason": "same file"}]
        assert plan["denied"] == []

    def test_denied_reported(self):
        entries = [
            entry("x.txt", "file", 10, 1.0),
            entry("y.txt", "file", 20, 2.0),
        ]

        def resolver(relpath):
            return True, 99, 9.0  # 均存在且大小不同

        plan = build_plan(entries, resolver, lambda r: None, force=False)
        assert plan["denied"] == [{"relpath": "x.txt"}, {"relpath": "y.txt"}]
        assert plan["transfers"] == []

    def test_force_overrides_denied(self):
        entries = [entry("x.txt", "file", 10, 1.0)]

        def resolver(relpath):
            return True, 99, 9.0

        plan = build_plan(entries, resolver, lambda r: None, force=True)
        assert plan["transfers"] == ["x.txt"]
        assert plan["denied"] == []