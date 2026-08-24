"""session/process/gui.py 单元测试"""

import pytest

from src.process.gui import GuiDetector
from src.process.base import PendingEvent


class TestGuiDetectorInit:
    def test_initial_state(self):
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        assert det.gui_windows == []
        assert det.processes == []
        assert det.detected_event.is_set() is False


class TestGuiDetectorCheck:
    def test_check_no_pty(self):
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        det.check(None, "test")
        assert len(events) == 0

    def test_clear(self):
        events = []
        det = GuiDetector(event_sink=lambda e: events.append(e))
        det.gui_windows = [{"hwnd": 1}]
        det.processes = [123]
        det.detected_event.set()
        det.clear()
        assert det.gui_windows == []
        assert det.processes == []
        assert det.detected_event.is_set() is False
