"""src/files/search/ignore.py 单元测试 — 搜索忽略过滤"""

import os
import pytest

from src.files.search.ignore import is_ignored


class TestIsIgnored:
    @pytest.mark.parametrize(
        "path",
        [
            "/proj/src/.hidden/file.txt",
            "/proj/.git/config",
            "/proj/node_modules/pkg/index.js",
            "/proj/vendor/lib/a.go",
            os.path.join("/proj", "build", "out.obj"),
            "/proj/__pycache__/a.pyc",
        ],
    )
    def test_ignored(self, path):
        assert is_ignored(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/proj/src/main.py",
            "/proj/README.md",
            "/proj/docs/well_known/x.txt",
        ],
    )
    def test_not_ignored(self, path):
        assert is_ignored(path) is False

    def test_hidden_dir_ignored(self):
        assert is_ignored("/proj/.well-known/x.txt") is True

    def test_any_segment_hits_full_path(self):
        assert is_ignored("/a/b/node_modules/c/d/e.txt") is True

    def test_configured_list_applies(self):
        # 插件 plugin.json 配置的忽略清单应被加载
        from src.files.settings import settings

        assert "node_modules" in settings.ignored_dirs
        assert ".git" in settings.ignored_dirs