"""KnownHosts 单元测试

覆盖 TOFU 信任存储的全部行为：
- 首次连接自动信任（TOFU）
- 指纹匹配/不匹配
- get / remove
- 多主机独立存储
- 文件持久化往返（save → reload 保持一致）
- 格式错误行跳过不崩溃
- 注释与空行处理
使用 tmp_path 隔离测试，不污染用户主目录。
"""

import os
import pytest

from src.auth.tls.known_hosts import KnownHosts


# 指纹常量（模拟 sha256:<hex> 格式）
_FP_A = "sha256:" + "a" * 64
_FP_B = "sha256:" + "b" * 64
_FP_C = "sha256:" + "c" * 64


class TestTofuFirstTrust:
    """TOFU 首次信任"""

    def test_first_verify_stores_and_returns_true(self, tmp_path):
        """首次 verify 时自动存储指纹并返回 True"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        result = kh.verify("192.168.1.10", 8443, _FP_A)

        assert result is True, "首次连接应 TOFU 信任"
        assert kh.get("192.168.1.10", 8443) == _FP_A, "指纹应已存储"

    def test_first_verify_persists_to_file(self, tmp_path):
        """首次 verify 后指纹写入文件，新实例可加载"""
        path = str(tmp_path / "known_hosts")
        kh = KnownHosts(path)
        kh.verify("example.com", 443, _FP_A)

        # 新实例从同一文件加载
        kh2 = KnownHosts(path)
        assert kh2.get("example.com", 443) == _FP_A, "新实例应能加载已存储的指纹"


class TestVerifyMatching:
    """指纹匹配验证"""

    def test_matching_fingerprint_returns_true(self, tmp_path):
        """已信任的主机指纹匹配时返回 True"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("10.0.0.1", 9000, _FP_A)

        result = kh.verify("10.0.0.1", 9000, _FP_A)
        assert result is True

    def test_mismatched_fingerprint_returns_false(self, tmp_path):
        """已信任的主机指纹不匹配时返回 False（MITM 检测）"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("10.0.0.1", 9000, _FP_A)

        result = kh.verify("10.0.0.1", 9000, _FP_B)
        assert result is False, "指纹不匹配应拒绝"

    def test_mismatch_does_not_overwrite(self, tmp_path):
        """指纹不匹配时不覆盖已存储的指纹"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("10.0.0.1", 9000, _FP_A)

        kh.verify("10.0.0.1", 9000, _FP_B)
        assert kh.get("10.0.0.1", 9000) == _FP_A, "不匹配不应覆盖已存指纹"


class TestGet:
    """get 方法"""

    def test_get_nonexistent_returns_none(self, tmp_path):
        """查询不存在的主机返回 None"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        assert kh.get("unknown.host", 1234) is None

    def test_get_returns_stored_fingerprint(self, tmp_path):
        """查询已信任主机返回存储的指纹"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("my.daemon", 8443, _FP_A)
        assert kh.get("my.daemon", 8443) == _FP_A


class TestRemove:
    """remove 方法"""

    def test_remove_existing_returns_true(self, tmp_path):
        """移除已存在的主机返回 True 且后续 get 为 None"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("to.remove", 8443, _FP_A)

        result = kh.remove("to.remove", 8443)
        assert result is True
        assert kh.get("to.remove", 8443) is None

    def test_remove_nonexistent_returns_false(self, tmp_path):
        """移除不存在的主机返回 False"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        result = kh.remove("never.existed", 8443)
        assert result is False

    def test_remove_persists_to_file(self, tmp_path):
        """移除后文件同步更新，新实例不再加载该条目"""
        path = str(tmp_path / "known_hosts")
        kh = KnownHosts(path)
        kh.trust("persist.remove", 8443, _FP_A)
        kh.remove("persist.remove", 8443)

        kh2 = KnownHosts(path)
        assert kh2.get("persist.remove", 8443) is None


class TestMultipleHosts:
    """多主机独立存储"""

    def test_multiple_hosts_stored_independently(self, tmp_path):
        """多个主机端口各自独立存储与验证"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("host1", 8000, _FP_A)
        kh.trust("host2", 8001, _FP_B)
        kh.trust("host3", 8002, _FP_C)

        assert kh.get("host1", 8000) == _FP_A
        assert kh.get("host2", 8001) == _FP_B
        assert kh.get("host3", 8002) == _FP_C

        # 各自独立验证
        assert kh.verify("host1", 8000, _FP_A) is True
        assert kh.verify("host2", 8001, _FP_B) is True
        assert kh.verify("host3", 8002, _FP_C) is True
        # 交叉不匹配
        assert kh.verify("host1", 8000, _FP_B) is False

    def test_same_host_different_port(self, tmp_path):
        """同一主机不同端口视为不同条目"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("same.host", 8000, _FP_A)
        kh.trust("same.host", 9000, _FP_B)

        assert kh.get("same.host", 8000) == _FP_A
        assert kh.get("same.host", 9000) == _FP_B


class TestFileRoundtrip:
    """文件持久化往返"""

    def test_save_and_reload_preserves_entries(self, tmp_path):
        """保存后重新加载，条目保持一致"""
        path = str(tmp_path / "known_hosts")
        kh = KnownHosts(path)
        kh.trust("roundtrip.a", 8443, _FP_A)
        kh.trust("roundtrip.b", 8444, _FP_B)

        # 新实例重新加载
        kh2 = KnownHosts(path)
        assert kh2.get("roundtrip.a", 8443) == _FP_A
        assert kh2.get("roundtrip.b", 8444) == _FP_B

    def test_empty_file_no_error(self, tmp_path):
        """空文件不报错，加载为空"""
        path = str(tmp_path / "known_hosts")
        open(path, "w").close()  # 创建空文件

        kh = KnownHosts(path)
        assert kh.get("any.host", 1234) is None

    def test_nonexistent_file_no_error(self, tmp_path):
        """文件不存在时视为空列表，不报错"""
        kh = KnownHosts(str(tmp_path / "does_not_exist"))
        assert kh.get("any.host", 1234) is None


class TestMalformedLines:
    """格式错误行处理"""

    def test_malformed_lines_skipped(self, tmp_path):
        """格式错误的行被跳过，不影响其他有效行"""
        path = str(tmp_path / "known_hosts")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 注释行\n")
            f.write("\n")  # 空行
            f.write("good.host:8443 " + _FP_A + "\n")
            f.write("no_port " + _FP_B + "\n")  # 缺少端口分隔符
            f.write("bad.port:abc " + _FP_C + "\n")  # 端口非数字
            f.write("only_host_port\n")  # 缺少指纹
            f.write("another.good:9000 " + _FP_C + "\n")

        kh = KnownHosts(path)
        # 有效行正常加载
        assert kh.get("good.host", 8443) == _FP_A
        assert kh.get("another.good", 9000) == _FP_C
        # 错误行不影响
        assert kh.get("no_port", 0) is None

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        """注释行和空行被忽略"""
        path = str(tmp_path / "known_hosts")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 第一行注释\n")
            f.write("\n")
            f.write("   # 带空格的注释\n")
            f.write("real.host:443 " + _FP_A + "\n")
            f.write("# 最后一行注释\n")

        kh = KnownHosts(path)
        assert kh.get("real.host", 443) == _FP_A


class TestTrust:
    """trust 方法"""

    def test_trust_overwrites_existing(self, tmp_path):
        """trust 覆盖已存在的指纹（用于证书重新生成后更新）"""
        kh = KnownHosts(str(tmp_path / "known_hosts"))
        kh.trust("overwrite.host", 8443, _FP_A)
        kh.trust("overwrite.host", 8443, _FP_B)

        assert kh.get("overwrite.host", 8443) == _FP_B, "trust 应覆盖旧指纹"

    def test_trust_persists_to_file(self, tmp_path):
        """trust 后指纹持久化，新实例可加载"""
        path = str(tmp_path / "known_hosts")
        kh = KnownHosts(path)
        kh.trust("persist.trust", 8443, _FP_A)

        kh2 = KnownHosts(path)
        assert kh2.get("persist.trust", 8443) == _FP_A
