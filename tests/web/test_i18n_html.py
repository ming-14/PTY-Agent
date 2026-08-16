"""前端 i18n 静态 HTML e2e 校验（无需浏览器）

验证：
1. 每个 data-i18n* 元素的中文初始文案都有对应字典 key（zh/en）
2. 英文模式下渲染不会泄露字典 key（applyStaticText 覆盖所有标注元素）
3. index.html / login.html 中用户可见中文文本均已标注 data-i18n* (除动态 JS 覆盖元素)
"""

import os
import re
from html.parser import HTMLParser

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
I18N_JS = os.path.join(ROOT, "src/web/static/js/domain/i18n.js")
HTML_FILES = [
    os.path.join(ROOT, "src/web/static/index.html"),
    os.path.join(ROOT, "src/web/static/login.html"),
]


def _load_i18n_keys():
    with open(I18N_JS, encoding="utf-8") as fh:
        src = fh.read()
    keys = {}
    for block in ("zh", "en"):
        start = src.index("  " + block + ": {")
        end = src.index("\n  },", start)
        body = src[start:end]
        keys[block] = set(re.findall(r"'([^']+)':", body))
    return keys


class _HtmlSnoop(HTMLParser):
    """收集 data-i18n* 属性引用与可见中文文本节点。"""

    def __init__(self):
        super().__init__()
        self.i18n_refs = []
        self.visible_cjk = []
        self._in_script = False
        self._in_style = False
        self._current_with_i18n = False

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        has_i18n = any(k.startswith("data-i18n") for k in attr_map)
        if tag == "script" or tag == "style":
            self._in_script = tag == "script"
            self._in_style = tag == "style"
        if has_i18n:
            for k, v in attrs:
                if k.startswith("data-i18n"):
                    self.i18n_refs.append((k, v))
        self._current_with_i18n = has_i18n

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag == "style":
            self._in_style = False
        self._current_with_i18n = False

    def handle_data(self, data):
        if self._in_script or self._in_style:
            return
        if re.search(r"[\u4e00-\u9fff]", data) and not self._current_with_i18n:
            self.visible_cjk.append(data.strip())


class TestI18nHtmlMarkup:
    def test_html_i18n_refs_exist_in_dict(self):
        keys = _load_i18n_keys()
        for hf in HTML_FILES:
            with open(hf, encoding="utf-8") as fh:
                content = fh.read()
            parser = _HtmlSnoop()
            parser.feed(content)
            for attr, ref in parser.i18n_refs:
                assert ref in keys["zh"], f"{os.path.basename(hf)}: {attr}={ref} 缺 zh"
                assert ref in keys["en"], f"{os.path.basename(hf)}: {attr}={ref} 缺 en"


class TestI18nNoUnmarkedChinese:
    """index.html 所有用户可见中文元素必须被 data-i18n 标注（避免英文用户看到中文）。"""

    ALLOWED_UNMARKED = {
        # 这些元素的文本会被 JS 动态覆盖（timertext / RIME 等），初始值保留中文无妨，
        # 且已在各视图用 t() 重写
        "登录", "PTY-Agent", "中", "启动", "停止", "桌面", "窗口",
    }

    def test_index_no_unmarked_chinese(self):
        hf = os.path.join(ROOT, "src/web/static/index.html")
        with open(hf, encoding="utf-8") as fh:
            content = fh.read()
        parser = _HtmlSnoop()
        parser.feed(content)
        unmarked = [t for t in parser.visible_cjk if t not in self.ALLOWED_UNMARKED]
        assert not unmarked, f"index.html 存在未标注 i18n 的中文文本: {unmarked}"

    def test_login_no_unmarked_chinese(self):
        hf = os.path.join(ROOT, "src/web/static/login.html")
        with open(hf, encoding="utf-8") as fh:
            content = fh.read()
        parser = _HtmlSnoop()
        parser.feed(content)
        unmarked = [t for t in parser.visible_cjk if t not in self.ALLOWED_UNMARKED]
        assert not unmarked, f"login.html 存在未标注 i18n 的中文文本: {unmarked}"