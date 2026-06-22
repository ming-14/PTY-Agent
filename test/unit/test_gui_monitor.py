"""GuiWindowMonitor 单元测试

测试 GUI 窗口检测、去重、WM_CLOSE 关闭功能。
仅在 Windows 平台运行，非 Windows 平台自动跳过。
"""

import sys
import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="GUI Monitor 仅在 Windows 平台可用"),
]


@pytest.fixture
def monitor():
    """创建 GuiWindowMonitor（带空 Job）"""
    from src.pty.windows.job import ProcessJob
    from src.pty.windows.gui_monitor import GuiWindowMonitor
    job = ProcessJob(name="test-gui")
    m = GuiWindowMonitor(job=job)
    yield m
    m.close()
    job.close()


class TestGuiWindowInfo:
    """GuiWindowInfo 数据类测试"""

    def test_to_dict(self):
        """GuiWindowInfo 可正确转换为字典"""
        from src.pty.windows.gui_monitor import GuiWindowInfo
        info = GuiWindowInfo(hwnd=0x12345678, pid=1234,
                             title="Test Window", class_name="TestClass")
        d = info.to_dict()
        assert d["hwnd"] == 0x12345678
        assert d["pid"] == 1234
        assert d["title"] == "Test Window"
        assert d["class_name"] == "TestClass"


class TestGuiWindowMonitor:
    """GuiWindowMonitor 功能测试"""

    def test_create_without_job(self):
        """不传入 Job 创建 Monitor 应不异常"""
        from src.pty.windows.gui_monitor import GuiWindowMonitor
        m = GuiWindowMonitor(job=None)
        assert m is not None
        m.close()

    def test_poll_with_empty_job(self, monitor):
        """空 Job 的 poll() 应返回空列表"""
        windows = monitor.poll()
        assert windows == []

    def test_poll_returns_list(self, monitor):
        """poll() 始终返回列表"""
        windows = monitor.poll()
        assert isinstance(windows, list)

    def test_windows_property(self, monitor):
        """windows 属性返回列表且可重复读取"""
        w1 = monitor.windows
        w2 = monitor.windows
        assert isinstance(w1, list)
        assert w1 == w2

    def test_clear_resets_state(self, monitor):
        """clear() 后 windows 应为空"""
        monitor.clear()
        assert monitor.windows == []

    def test_close_window_invalid(self, monitor):
        """关闭无效句柄不应抛异常"""
        result = monitor.close_window(0xDEADBEEF)
        assert isinstance(result, bool)

    def test_close_window_valid(self, monitor):
        """关闭 0 句柄（空窗口）应正常返回"""
        result = monitor.close_window(0)
        assert isinstance(result, bool)

    def test_initial_windows_empty(self, monitor):
        """新创建的 Monitor 的 windows 应为空"""
        assert monitor.windows == []


class TestGuiMonitorEdgeCases:
    """边界情况测试"""

    def test_close_after_close(self):
        """重复 close 应安全"""
        from src.pty.windows.gui_monitor import GuiWindowMonitor
        m = GuiWindowMonitor(job=None)
        m.close()
        m.close()

    def test_poll_after_close(self):
        """close 后 poll 应安全返回"""
        from src.pty.windows.gui_monitor import GuiWindowMonitor
        m = GuiWindowMonitor(job=None)
        m.close()
        result = m.poll()
        assert result == []

    def test_windows_after_close(self):
        """close 后 windows 应为空"""
        from src.pty.windows.gui_monitor import GuiWindowMonitor
        m = GuiWindowMonitor(job=None)
        m.close()
        assert m.windows == []

    def test_close_process_windows_empty(self, monitor):
        """close_process_windows 对不存在的 PID 返回 0"""
        count = monitor.close_process_windows(99999999)
        assert count == 0
