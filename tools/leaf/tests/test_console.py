"""控制台驱动事件映射的单元测试（leaf.drivers.console._to_domain）

归一化本身在绑定层（pywezterm.ConsoleInput）完成并有 Rust 侧单测，
此处只测绑定 tuple → 领域事件的薄映射。
"""

from leaf.drivers.console import _to_domain
from leaf.domain.events import KeyEvent, MouseEvent, ResizeEvent


def test_key_tuple():
    assert _to_domain(("key", "a", 0, True)) == KeyEvent("a", 0, True)


def test_key_tuple_mods_and_up():
    assert _to_domain(("key", "Up", 2, False)) == KeyEvent("Up", 2, False)


def test_mouse_tuple():
    assert _to_domain(("mouse", 10, 5, "press", "left", 0, 1)) == MouseEvent(10, 5, "press", "left", 0)


def test_mouse_double_click_count_tuple():
    # 双击第二击：count=2（绑定层 Rust 侧判定，leaf 直接消费）
    assert _to_domain(("mouse", 10, 5, "press", "left", 0, 2)) == MouseEvent(10, 5, "press", "left", 0, 2)


def test_mouse_wheel_tuple():
    assert _to_domain(("mouse", 3, 4, "press", "wheel_up", 8, 1)) == MouseEvent(3, 4, "press", "wheel_up", 8)


def test_mouse_tuple_without_count():
    # 无 count 的 6 元组也应正确解析（默认 count=1）
    assert _to_domain(("mouse", 10, 5, "press", "left", 0)) == MouseEvent(10, 5, "press", "left", 0)


def test_resize_tuple():
    assert _to_domain(("resize",)) == ResizeEvent()
