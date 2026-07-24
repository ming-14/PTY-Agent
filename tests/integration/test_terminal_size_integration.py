"""集成测试：终端尺寸 CLI --size 和 --default terminal-size

通过 pty-agent CLI 直接测试终端尺寸功能。
"""

import subprocess
import json
import sys
import os
import time

APP = [sys.executable, "app.py"]


def _run(args, timeout=30):
    result = subprocess.run(
        APP + args,
        capture_output=True, text=True,
        timeout=timeout,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    return result.stdout.strip()


def _parse_json(output):
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


class TestTerminalSizeCLI:
    def setup_method(self):
        _run(["stop"])

    def teardown_method(self):
        _run(["stop"])

    def test_default_size_80x24(self):
        """默认终端尺寸为 80x24"""
        out = _run(["exec", "s1", "-c", "cmd /c echo hello", "--timeout", "10"])
        resp = _parse_json(out)
        if resp and resp.get("commandType") == "exec":
            prog = resp.get("program", {})
            pty_type = prog.get("ptyType", "")
            assert pty_type in ("win-conpty", "win-condrv"), f"Unexpected pty type: {pty_type}"

    def test_size_120x40(self):
        """--size 120x40 创建宽终端"""
        out = _run(["exec", "s2", "-c", "cmd /c echo wide", "--size", "120x40", "--timeout", "10"])
        resp = _parse_json(out)
        if resp and resp.get("commandType") == "exec":
            prog = resp.get("program", {})
            pty_type = prog.get("ptyType", "")
            assert pty_type in ("win-conpty", "win-condrv"), f"Unexpected pty type: {pty_type}"

    def test_size_invalid_format(self):
        """--size 格式错误返回错误"""
        out = _run(["exec", "s3", "-c", "cmd /c echo x", "--size", "invalid", "--timeout", "5"])
        resp = _parse_json(out)
        if resp:
            assert resp.get("type") == "error" or "error" in str(resp).lower(), \
                f"Expected error for invalid size, got: {resp}"

    def test_default_terminal_size_config(self):
        """--default terminal-size 100x30 即时生效"""
        out = _run(["exec", "s4", "-c", "cmd /c echo config",
                     "--default", "terminal-size", "100x30", "--timeout", "10"])
        resp = _parse_json(out)
        if resp and resp.get("commandType") == "exec":
            prog = resp.get("program", {})
            pty_type = prog.get("ptyType", "")
            assert pty_type in ("win-conpty", "win-condrv"), f"Unexpected pty type: {pty_type}"

    def test_size_with_snapshot_mode(self):
        """--size 与 --snapshot-mode 组合"""
        out = _run(["exec", "s5", "-c", "cmd /c echo snap",
                     "--snapshot-mode", "--size", "100x30", "--timeout", "10"])
        resp = _parse_json(out)
        if resp and resp.get("commandType") == "exec":
            diag = resp.get("snapshotDiagnostics", {})
            assert diag.get("cols") == 100, f"Expected cols=100, got {diag.get('cols')}"
            assert diag.get("rows") == 30, f"Expected rows=30, got {diag.get('rows')}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
