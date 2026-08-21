"""config/plugins/files/paths.py 单元测试 — 路径工具（Git-Bash 提示函数已迁核心 utils）"""

import os
import pytest

from src.daemon.response import has_git_bash_style_path
from config.plugins.files.paths import (
    resolve_session_path,
    is_within,
    normalize_key,
)


class TestResolveSessionPath:
    def test_relative_joined_to_session_cwd(self):
        assert resolve_session_path("a/b.txt", "C:/proj") == os.path.normpath("C:/proj/a/b.txt")

    def test_absolute_unchanged(self):
        assert resolve_session_path(os.path.abspath("/tmp/abc"), "C:/proj") == os.path.normpath(os.path.abspath("/tmp/abc"))

    def test_dot_relative(self):
        assert resolve_session_path(".", "C:/proj/sub") == os.path.normpath("C:/proj/sub")

    def test_dotdot_normalized(self):
        assert resolve_session_path("../x.txt", "C:/proj/sub") == os.path.normpath("C:/proj/x.txt")

    def test_home_expanded_by_daemon_user(self):
        # ~ 在 daemon 侧按 daemon 用户展开（expanduser 后为绝对路径，直接使用）
        assert resolve_session_path("~/f.txt", "C:/proj") == os.path.normpath(os.path.expanduser("~/f.txt"))


class TestIsWithin:
    def test_equal_root(self):
        assert is_within("/a/b", "/a/b") is True

    def test_child(self):
        assert is_within("/a/b/c.txt", "/a/b") is True

    def test_sibling_prefix_not_matched(self):
        # 前缀落地误判防护：/a/b2 不在 /a/b 内
        assert is_within("/a/b2/c.txt", "/a/b") is False

    def test_parent(self):
        assert is_within("/a", "/a/b") is False

    def test_with_dot_segments(self):
        assert is_within("/a/b/../b/c.txt", "/a/b") is True


class TestGitBashStyle:
    @pytest.mark.parametrize(
        "text",
        [
            "/c/Users/x/a.txt",
            "abc /d/tmp.txt",
            'x "/e/f.txt"',
        ],
    )
    def test_detects(self, text):
        assert has_git_bash_style_path(text) is True

    @pytest.mark.parametrize(
        "text",
        ["C:/Users/x/a.txt", "abc def", "C:\\Windows\\tmp", ""],
    )
    def test_not_detected(self, text):
        assert has_git_bash_style_path(text) is False

    def test_list_input(self):
        assert has_git_bash_style_path(["cd", "/c/Users"]) is True

    def test_non_string(self):
        assert has_git_bash_style_path(None) is False


class TestNormalizeKey:
    def test_windows_normcase(self):
        assert normalize_key("C:/Foo/Bar") == os.path.normcase("C:/Foo/Bar")