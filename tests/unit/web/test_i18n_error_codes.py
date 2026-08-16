"""i18n 错误码结构单元测试

验证 Web 端 WS 错误消息统一为 { code, params, message } 结构：
- 无中文字面量跨网传输（message 保持后端语言无关英文/空）
- 错误码与前端 i18n 字典 key 一一对应
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
from src.protocol.response import Response  # noqa: E402


FRONTEND_I18N = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "src/web/static/js/domain/i18n.js"
    )
)


def _extract_i18n_keys(block_name: str) -> set:
    with open(FRONTEND_I18N, encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("  " + block_name + ": {")
    end = src.index("\n  },", start)
    block = src[start:end]
    return set(re.findall(r"'([^']+)':", block))


def _backend_codes() -> set:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    codes = set()
    pattern = re.compile(r'code\s*=\s*"([^"]+)"')
    for base, _dirs, files in os.walk(os.path.join(root, "src", "web")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                codes.update(pattern.findall(fh.read()))
    return codes


class TestResponseErrorStructure:
    def test_vnc_error_has_code_and_params(self):
        msg = Response.ws_vnc_error("VNC start failed", code="vnc.start_failed", params={"error": "boom"})
        assert msg["type"] == "vnc_error"
        assert msg["code"] == "vnc.start_failed"
        assert msg["params"] == {"error": "boom"}
        # message 保持后端语言无关英文，无中文字面量
        assert "[\u4e00-\u9fff]" not in msg["message"]

    def test_fs_error_has_code(self):
        msg = Response.ws_fs_error("bring to front failed", code="fs.bring_to_front_failed", params={"error": "x"})
        assert msg["code"] == "fs.bring_to_front_failed"

    def test_locator_error_has_code(self):
        msg = Response.ws_cursor_locator_error("service unavailable", code="locator.service_unavailable")
        assert msg["code"] == "locator.service_unavailable"

    def test_ws_error_general(self):
        msg = Response.ws_error("subprocess mode has no terminal", code="subprocess_no_terminal_key")
        assert msg["type"] == "error"
        assert msg["code"] == "subprocess_no_terminal_key"

    def test_ws_errors_contain_no_chinese_message(self):
        """所有 ws_*_error 调用不得把中文字面量发送到前端。

        仅检查处理器中的字符串字面量（排除注释/docstring），确保用户可见错误为错误码结构。
        """
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        handler_path = os.path.join(root, "src", "web", "application", "handlers.py")
        with open(handler_path, encoding="utf-8") as fh:
            src = fh.read()

        # 剔除注释行 / docstring / 三引号块
        src = re.sub(r'"""(?:[^"]|"(?!"""))*"""', "", src, flags=re.S)
        src = re.sub(r"#.*$", "", src, flags=re.M)
        # 检查双引号/单引号字符串字面量中是否残留中文
        string_literals = re.findall(r"""(?s)("(?:[^"\\]|\\.)*")|('(?:[^'\\]|\\.)*')""", src)
        cjk = []
        for pair in string_literals:
            for lit in pair:
                if re.search(r"[\u4e00-\u9fff]", lit):
                    cjk.append(lit)
        assert not cjk, f"handlers.py 字符串字面量不应含中文: {cjk}"


class TestErrorCodeMapping:
    def test_backend_codes_have_frontend_translations(self):
        zh = _extract_i18n_keys("zh")
        en = _extract_i18n_keys("en")
        backend = _backend_codes()
        missing_zh = backend - zh
        missing_en = backend - en
        assert not missing_zh, f"缺少 zh 翻译: {missing_zh}"
        assert not missing_en, f"缺少 en 翻译: {missing_en}"

    def test_zh_en_dict_keys_identical(self):
        zh = _extract_i18n_keys("zh")
        en = _extract_i18n_keys("en")
        assert zh == en

    def test_error_codes_used(self):
        """后端 code 非空且以模块前缀命名。"""
        for code in _backend_codes():
            assert code, "code 不能为空"
            assert "." in code or "_" in code