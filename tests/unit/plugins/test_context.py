"""插件上下文输出单测 — scan/find/read/output（CLI 侧输出给用户）"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.context import (  # noqa: E402
    context_text,
    find_plugin_dir,
    output_context,
    output_process_contexts,
    reset_context_state,
    scan_plugin_dirs,
)
from tests.helpers import write_plugin_dir  # noqa: E402


CLI_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def render_response(self, ctx, resp): return 'x'\n"
    "plugin = P()\n"
)

PROCESS_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def handle_message(self, ctx, msg): return {'ok': True}\n"
    "plugin = P()\n"
)

SESSION_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    def on_event(self, ctx, event): pass\n"
    "plugin = P()\n"
)


class TestScan:
    def test_scans_ids_and_kinds(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "proc", "process", PROCESS_SRC,
                                manifest_extra={"messageTypes": ["cmd_a"]})
        cdir = write_plugin_dir(tmp_path, "clix", "cli", CLI_SRC)
        sdir = write_plugin_dir(tmp_path, "sess", "session", SESSION_SRC)
        (tmp_path / "not_plugin").mkdir()
        result = dict((i, (k, p)) for i, k, p in scan_plugin_dirs([str(tmp_path / "proc"),
                                                                    str(tmp_path / "clix"),
                                                                    str(tmp_path / "sess"),
                                                                    str(tmp_path / "not_plugin")]))
        assert result["proc"][0] == ["process"]
        assert result["clix"][0] == ["cli"]
        assert result["sess"][0] == ["session"]
        assert len(result) == 3

    def test_find_plugin_dir(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        assert find_plugin_dir([str(tmp_path / "demo")], "demo") == str(tmp_path / "demo")
        assert find_plugin_dir([str(tmp_path / "demo")], "ghost") is None
        assert find_plugin_dir([], "demo") is None

    def test_bad_manifest_skipped(self, tmp_path):
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "plugin.json").write_text("{bad", encoding="utf-8")
        assert scan_plugin_dirs([str(bad)]) == []


class TestContextText:
    def test_reads_with_markers(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        open(os.path.join(pdir, "demo.md"), "w", encoding="utf-8").write("line1\nline2\n")
        text = context_text("demo", str(pdir))
        assert text == "[plugin demo context]\nline1\nline2\n[plugin demo context end]\n"

    def test_missing_file_returns_none(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        assert context_text("demo", str(pdir)) is None

    def test_oversized_truncated(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        open(os.path.join(pdir, "demo.md"), "w", encoding="utf-8").write("x" * 70000)
        text = context_text("demo", str(pdir))
        assert "[context truncated]" in text
        assert len(text) < 70000

    def test_bad_encoding_returns_none(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        open(os.path.join(pdir, "demo.md"), "wb").write(b"\xff\xfe\x00bad")
        assert context_text("demo", str(pdir)) is None


class TestOutput:
    def test_output_context_to_stream(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        open(os.path.join(pdir, "demo.md"), "w", encoding="utf-8").write("usage")
        buf = io.StringIO()
        assert output_context(buf, "demo", str(pdir), state_file=os.path.join(str(tmp_path), "state.json")) is True
        assert "[plugin demo context]" in buf.getvalue()
        assert "usage" in buf.getvalue()

    def test_output_context_missing_returns_false(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        buf = io.StringIO()
        assert output_context(buf, "demo", str(pdir), state_file=os.path.join(str(tmp_path), "state.json")) is False
        assert buf.getvalue() == ""

    def test_output_process_contexts_only_process(self, tmp_path):
        pdir = write_plugin_dir(tmp_path, "proc", "process", PROCESS_SRC,
                                manifest_extra={"messageTypes": ["cmd_a"]})
        open(os.path.join(pdir, "proc.md"), "w", encoding="utf-8").write("process usage")
        sdir = write_plugin_dir(tmp_path, "sess", "session", SESSION_SRC)
        open(os.path.join(sdir, "sess.md"), "w", encoding="utf-8").write("session usage")
        buf = io.StringIO()
        count = output_process_contexts([str(pdir), str(sdir)], stream=buf,
                              state_file=os.path.join(str(tmp_path), "state.json"))
        assert count == 1
        assert "[plugin proc context]" in buf.getvalue()
        assert "[plugin sess context]" not in buf.getvalue()

    def test_output_process_contexts_disabled_filtered(self, tmp_path):
        """显式禁用的插件（registry.json）不输出上下文"""
        pdir = write_plugin_dir(tmp_path, "proc", "process", PROCESS_SRC,
                                manifest_extra={"messageTypes": ["cmd_a"]})
        open(os.path.join(pdir, "proc.md"), "w", encoding="utf-8").write("process usage")
        buf = io.StringIO()
        count = output_process_contexts([str(pdir)], stream=buf, disabled={"proc"},
                              state_file=os.path.join(str(tmp_path), "state.json"))
        assert count == 0
        assert buf.getvalue() == ""

    def test_output_process_contexts_empty(self, tmp_path):
        buf = io.StringIO()
        assert output_process_contexts([], stream=buf, state_file=os.path.join(str(tmp_path), "state.json")) == 0


class TestSendOnce:
    """只发一次：每 daemon 周期每插件文档只输出一次，内容变化重发"""

    def _plugin_dir(self, tmp_path, content="usage"):
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        open(os.path.join(pdir, "demo.md"), "w", encoding="utf-8").write(content)
        return str(pdir)

    def _state_file(self, tmp_path):
        return os.path.join(str(tmp_path), "state.json")

    def test_first_send_then_skipped(self, tmp_path):
        pdir = self._plugin_dir(tmp_path)
        state_file = self._state_file(tmp_path)
        buf1 = io.StringIO()
        assert output_context(buf1, "demo", pdir, state_file=state_file) is True
        assert "[plugin demo context]" in buf1.getvalue()
        buf2 = io.StringIO()
        assert output_context(buf2, "demo", pdir, state_file=state_file) is False
        assert buf2.getvalue() == ""

    def test_content_change_resends(self, tmp_path):
        pdir = self._plugin_dir(tmp_path, content="v1")
        state_file = self._state_file(tmp_path)
        buf1 = io.StringIO()
        assert output_context(buf1, "demo", pdir, state_file=state_file) is True
        # 内容变化 → 同周期内重新发送
        open(os.path.join(pdir, "demo.md"), "w", encoding="utf-8").write("v2")
        buf2 = io.StringIO()
        assert output_context(buf2, "demo", pdir, state_file=state_file) is True
        assert "v2" in buf2.getvalue()
        # 内容未变 → 跳过
        buf3 = io.StringIO()
        assert output_context(buf3, "demo", pdir, state_file=state_file) is False

    def test_reset_clears_state(self, tmp_path):
        pdir = self._plugin_dir(tmp_path)
        state_file = self._state_file(tmp_path)
        assert output_context(io.StringIO(), "demo", pdir, state_file=state_file) is True
        reset_context_state(state_file)
        buf = io.StringIO()
        assert output_context(buf, "demo", pdir, state_file=state_file) is True

    def test_missing_file_not_marked(self, tmp_path):
        """文档缺失时既不输出也不标记：补上文件后仍会发送"""
        pdir = write_plugin_dir(tmp_path, "demo", "session", SESSION_SRC)
        state_file = self._state_file(tmp_path)
        assert output_context(io.StringIO(), "demo", str(pdir), state_file=state_file) is False
        open(os.path.join(pdir, "demo.md"), "w", encoding="utf-8").write("later")
        buf = io.StringIO()
        assert output_context(buf, "demo", str(pdir), state_file=state_file) is True

    def test_corrupt_state_file_tolerated(self, tmp_path):
        pdir = self._plugin_dir(tmp_path)
        state_file = self._state_file(tmp_path)
        open(state_file, "w", encoding="utf-8").write("{corrupt")
        buf = io.StringIO()
        assert output_context(buf, "demo", pdir, state_file=state_file) is True

    def test_state_isolated_per_plugin(self, tmp_path):
        """不同插件互不影响"""
        pdir = self._plugin_dir(tmp_path, content="demo usage")
        other = write_plugin_dir(tmp_path, "other", "session", SESSION_SRC)
        open(os.path.join(other, "other.md"), "w", encoding="utf-8").write("other usage")
        state_file = self._state_file(tmp_path)
        assert output_context(io.StringIO(), "demo", pdir, state_file=state_file) is True
        buf = io.StringIO()
        assert output_context(buf, "other", str(other), state_file=state_file) is True
        assert "[plugin other context]" in buf.getvalue()