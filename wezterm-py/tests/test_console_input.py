"""ConsoleInput 绑定的条件测试：仅在有真实交互控制台时执行。

自动化环境（stdio 重定向）构造 ConsoleInput 失败，测试跳过；
归一化的核心覆盖在绑定侧 Rust 单测（console_input.rs #[cfg(test)]）。
"""

import pytest

import pywezterm


@pytest.fixture(scope="module")
def console():
    try:
        return pywezterm.ConsoleInput()
    except Exception as e:  # 非交互控制台（管道/文件重定向）构造必然失败
        pytest.skip("无宿主交互控制台: {}".format(e))


def test_wait_input_no_event_timeout(console):
    assert console.wait_input(0) is False  # 无待处理事件：立即超时


def test_size_valid(console):
    cols, rows = console.size()
    assert cols > 0 and rows > 0


def test_read_inputs_type(console):
    # 空事件或任意事件都应能返回（不抛异常）；形状由绑定侧单测保证
    evs = console.read_inputs()
    assert isinstance(evs, list)


def test_restore_idempotent(console):
    console.restore()
    console.restore()
