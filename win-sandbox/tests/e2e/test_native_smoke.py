"""
test_native_smoke - pybind11 native 扩展冒烟测试

验证：
  1. win_sandbox_native 模块可 import
  2. SandboxInstance 可构造，capabilities 可读
  3. start_process 启动隔离进程，返回 Process 对象
  4. 句柄属性（process_handle/stdout_handle/stderr_handle）为 int
  5. Python 端用 ctypes ReadFile 读 stdout 原始字节
  6. wait 返回 (exit_code, exit_reason, resource_usage)
  7. close / shutdown 不崩溃
"""
import sys
import os
import ctypes
import pathlib

# 把 build/bin 加入 sys.path（.pyd 所在目录）
BUILD_BIN = pathlib.Path(__file__).resolve().parents[2] / "build" / "bin"
sys.path.insert(0, str(BUILD_BIN))

import win_sandbox_native  # noqa: E402

kernel32 = ctypes.windll.kernel32
kernel32.ReadFile.argtypes = [
    ctypes.c_void_p,  # hFile
    ctypes.c_void_p,  # lpBuffer
    ctypes.c_ulong,   # nNumberOfBytesToRead
    ctypes.POINTER(ctypes.c_ulong),  # lpNumberOfBytesRead
    ctypes.c_void_p,  # lpOverlapped
]
kernel32.ReadFile.restype = ctypes.c_int


def read_pipe(handle: int, size: int = 65536) -> bytes:
    """从管道句柄读取全部数据（循环 ReadFile 直到 EOF）"""
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_ulong()
    chunks = []
    while True:
        ok = kernel32.ReadFile(handle, buf, size, ctypes.byref(read), None)
        if read.value == 0:
            break
        chunks.append(buf.raw[:read.value])
        if not ok:
            break
    return b"".join(chunks)


def test_module_import():
    """模块可 import 且有 docstring"""
    assert "win-sandbox" in win_sandbox_native.__doc__


def test_capabilities():
    """SandboxInstance 可构造，capabilities 可读"""
    sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")
    caps = sb.capabilities
    assert "mode" in caps
    assert "capabilities" in caps
    assert isinstance(caps["capabilities"], list)
    sb.shutdown()


def test_start_and_wait():
    """启动进程 + 读 stdout + wait"""
    sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")

    proc = sb.start_process(
        command_line="cmd.exe /c echo hello from native",
        quota={"memory_mb": 256, "wall_clock_timeout_ms": 10000},
    )

    # 句柄属性
    assert proc.pid > 0
    assert proc.process_id > 0
    assert proc.process_handle != 0
    assert proc.stdout_handle != 0
    assert proc.stderr_handle != 0
    # 非 interactive → stdin_handle 为 None
    assert proc.stdin_handle is None

    # Python 自己读 stdout
    stdout_data = read_pipe(proc.stdout_handle)
    assert b"hello from native" in stdout_data, f"stdout={stdout_data!r}"

    # wait
    exit_code, reason, usage = proc.wait(timeout_ms=10000)
    assert exit_code == 0, f"exit_code={exit_code}, reason={reason}"
    assert reason == "normal"
    assert isinstance(usage, dict)

    proc.close()
    sb.shutdown()


def test_exit_code_nonzero():
    """进程非零退出码"""
    sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")
    proc = sb.start_process(command_line="cmd.exe /c exit 42")
    read_pipe(proc.stdout_handle)  # 消费输出
    exit_code, reason, _ = proc.wait(timeout_ms=10000)
    assert exit_code == 42, f"exit_code={exit_code}"
    proc.close()
    sb.shutdown()


def test_terminate():
    """主动终止进程"""
    sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")
    # 启动一个长跑进程
    proc = sb.start_process(command_line="cmd.exe /c ping -n 100 127.0.0.1")
    # 立即终止
    proc.terminate(exit_code=1)
    exit_code, reason, _ = proc.wait(timeout_ms=10000)
    # 被终止的进程退出码可能是 1 或其他
    assert reason in ("killed_by_user", "normal"), f"reason={reason}"
    proc.close()
    sb.shutdown()


def test_list_processes():
    """list_processes 返回进程列表"""
    sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")
    proc = sb.start_process(command_line="cmd.exe /c ping -n 2 127.0.0.1")
    procs = sb.list_processes()
    assert len(procs) >= 1
    assert any(p["process_id"] == proc.process_id for p in procs)
    read_pipe(proc.stdout_handle)
    proc.wait(timeout_ms=10000)
    proc.close()
    sb.shutdown()


def test_query_methods():
    """query_accounting / query_peak_memory / query_process_list"""
    sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")
    proc = sb.start_process(command_line="cmd.exe /c echo hello")

    # query_accounting
    acc = proc.query_accounting()
    assert isinstance(acc, dict)
    assert "cpu" in acc
    assert "io" in acc

    # query_peak_memory
    peak = proc.query_peak_memory()
    assert isinstance(peak, int)
    assert peak > 0

    # query_process_list
    pids = proc.query_process_list()
    assert proc.pid in pids

    read_pipe(proc.stdout_handle)
    proc.wait(timeout_ms=10000)
    proc.close()
    sb.shutdown()


if __name__ == "__main__":
    # 直接运行模式（不用 pytest）
    tests = [
        test_module_import,
        test_capabilities,
        test_start_and_wait,
        test_exit_code_nonzero,
        test_terminate,
        test_list_processes,
        test_query_methods,
    ]
    for t in tests:
        print(f"--- {t.__name__} ---")
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")
