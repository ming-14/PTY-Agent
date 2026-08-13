"""e2e 测试：ConPTY（hpcon 外部传入）集成验证

覆盖（复用 _bbtest 阶段的验证链，正式纳入 e2e）：
  1. hpcon 启动：is_pty=True、stdio 句柄全 None
  2. banner 输出到达 ConPTY 输出管道
  3. 子进程 isatty=True（终端语义完整）
  4. 回显 / 方向键历史回调
  5. resize
  6. Ctrl+C 中断（无 CREATE_NEW_PROCESS_GROUP 时 \x03 生效）
  7. exit 正常退出 + 资源清理

前置：
  - Windows 10 19041+；conhost 服务可用（测试须在具有有效控制台会话的环境中运行，
    无头宿主（如部分服务/CI 执行器）会以 0xC0000142 失败——属环境限制，非产品缺陷）

运行方式（仓库根目录）：
  python tests/e2e/test_hpcon_conpty.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402
from win_sandbox.exceptions import SandboxTimeoutError  # noqa: E402


class _COORD(ctypes.Structure):
    _fields_ = [("X", W.SHORT), ("Y", W.SHORT)]


def _main() -> int:
    hr_ok = 0x0
    INHERIT = 1
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreatePipe.argtypes = [ctypes.POINTER(W.HANDLE), ctypes.POINTER(W.HANDLE),
                               W.LPVOID, W.DWORD]
    k32.CreatePipe.restype = W.BOOL
    k32.CreatePseudoConsole.argtypes = [_COORD, W.HANDLE, W.HANDLE, W.DWORD,
                                        ctypes.POINTER(W.HANDLE)]
    k32.CreatePseudoConsole.restype = ctypes.HRESULT
    k32.SetHandleInformation.argtypes = [W.HANDLE, W.DWORD, W.DWORD]
    k32.SetHandleInformation.restype = W.BOOL
    k32.ReadFile.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD), W.LPVOID]
    k32.ReadFile.restype = W.BOOL
    k32.WriteFile.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD), W.LPVOID]
    k32.WriteFile.restype = W.BOOL
    k32.PeekNamedPipe.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD),
                                  ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD)]
    k32.PeekNamedPipe.restype = W.BOOL
    k32.ResizePseudoConsole.argtypes = [W.HANDLE, W.LPVOID]
    k32.ResizePseudoConsole.restype = ctypes.HRESULT
    k32.ClosePseudoConsole.argtypes = [W.HANDLE]
    k32.ClosePseudoConsole.restype = None

    # 1. 创建 ConPTY（双匿名管道 + 继承标记）
    in_r = W.HANDLE()
    in_w = W.HANDLE()
    out_r = W.HANDLE()
    out_w = W.HANDLE()
    if not k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0):
        print("FAIL: CreatePipe(in)")
        return 1
    if not k32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0):
        print("FAIL: CreatePipe(out)")
        return 1
    k32.SetHandleInformation(in_r, INHERIT, INHERIT)
    k32.SetHandleInformation(out_w, INHERIT, INHERIT)
    hpc = W.HANDLE()
    size = _COORD(120, 30)
    # 尺寸必须以 COORD 结构体按值传递（x64 ABI）；传 byref 会令 conhost
    # 把指针地址解析为尺寸 → 子进程启动失败（0xC0000142）
    hr = k32.CreatePseudoConsole(size, in_r, out_w, 0, ctypes.byref(hpc))
    if hr != hr_ok:
        print(f"FAIL: CreatePseudoConsole hr=0x{hr:x}")
        return 1

    # 2. 启动沙箱进程（hpcon 模式）
    sb = make_sandbox()
    proc = sb.start_process(command_line="cmd.exe", interactive=True, hpcon=int(hpc.value))
    print(f"  pid={proc.pid} is_pty={proc.is_pty}")
    if not proc.is_pty:
        print("FAIL: is_pty expected True")
        return 1
    if not (proc.stdin_handle is None and proc.stdout_handle is None
            and proc.stderr_handle is None):
        print("FAIL: stdio handles expected None in ConPTY mode")
        return 1
    time.sleep(2.0)

    def read_available(timeout: float) -> bytes:
        """轮询读取 ConPTY 输出管道；数据安静 0.5s 视为一轮结束"""
        chunks = []
        idle = 0.0
        deadline = time.time() + timeout
        while True:
            avail = W.DWORD(0)
            if (k32.PeekNamedPipe(out_r, None, 0, None, ctypes.byref(avail), None)
                    and avail.value > 0):
                buf = ctypes.create_string_buffer(avail.value)
                br = W.DWORD(0)
                k32.ReadFile(out_r, buf, avail.value, ctypes.byref(br), None)
                chunks.append(buf.raw[:br.value])
                idle = time.time() + 0.5
                continue
            if idle and time.time() >= idle:
                break
            if time.time() >= deadline:
                break
            time.sleep(0.05)
        return b"".join(chunks)

    def write_in(data: bytes) -> None:
        nw = W.DWORD(0)
        buf = ctypes.create_string_buffer(data)
        if not k32.WriteFile(in_w, buf, len(data), ctypes.byref(nw), None):
            raise OSError(ctypes.get_last_error(), "WriteFile")

    # 3. banner
    data = read_available(3.0)
    if b"Microsoft Windows" not in data:
        print(f"FAIL: banner missing: {data[:80]!r}")
        return 1
    print(f"  banner ok ({len(data)} bytes)")

    # 4. isatty
    write_in(b"python -c \"import sys; print('TTY', sys.stdin.isatty(), sys.stdout.isatty())\"\r\n")
    data = read_available(5.0)
    if b"TTY True True" not in data:
        print(f"FAIL: isatty expected True True: {data!r}")
        return 1
    print("  isatty True True ok")

    # 5. 回显
    write_in(b"echo HELLO_HPCON_E2E\r\n")
    data = read_available(3.0)
    if b"HELLO_HPCON_E2E" not in data:
        print(f"FAIL: echo {data!r}")
        return 1
    print("  echo ok")

    # 6. 方向键历史
    write_in(b"echo FIRST_HISTORY\r\n")
    read_available(3.0)
    write_in(b"\x1b[A\r\n")
    data = read_available(3.0)
    if b"FIRST_HISTORY" not in data:
        print(f"FAIL: history {data!r}")
        return 1
    print("  history ok")

    # 7. (resize 移到 ctrl+c 之后：resize 会触发 conhost 全屏重绘，可能与输入
    #    处理产生竞态，故中断验证放在干净的输入窗口内)

    # 8. Ctrl+C 中断（回归：CREATE_NEW_PROCESS_GROUP 会使其失效；事件送达有竞态，
    #    故补发一次并辅以"中断后无新 TTL"旁证；直跑程序被中断时无 Control-C 文本，
    #    以 cmd 壳场景断言）
    write_in(b"ping -n 60 127.0.0.1\r\n")
    time.sleep(3.0)
    write_in(b"\x03")
    time.sleep(0.8)
    write_in(b"\x03")
    data = read_available(8.0)
    interrupted = (b"Control-C" in data or b"^C" in data)
    ttl_seen = b"TTL=" in data
    if b"TTL" in data:
        i = data.rfind(b"TTL=")
        ttl_uuid = b"TTL=" not in read_available(2.0)
    else:
        ttl_uuid = True
    if not (interrupted and ttl_uuid):
        print(f"FAIL: ctrl+c not effective: interrupt={interrupted} "
              f"stop={ttl_uuid} ttl={ttl_seen} {data[-120:]!r}")
        return 1
    print("  ctrl+c ok")

    # 9. resize（中断后，避免全屏重绘与输入竞态）
    hr = k32.ResizePseudoConsole(hpc, ctypes.byref(_COORD(100, 25)))
    if hr != hr_ok:
        print(f"FAIL: resize hr=0x{hr:x}")
        return 1
    print(f"  resize ok (hr=0x{hr:x})")

    # resize 触发 conhost 全屏重绘；重绘输出未消费完前发送输入可能被吞，
    # 先 drain 至输出静止再继续
    read_available(2.0)

    # 10. exit
    # resize 触发 conhost 全屏重绘；重绘输出未消费完前发送输入可能被吞，
    # 先长 drain 至输出静止；wait 超时（输入被吞）则补发 exit 重试一次。
    read_available(5.0)
    write_in(b"exit\r\n")
    try:
        exit_code, reason, _ = proc.wait(timeout_ms=15000)
    except SandboxTimeoutError:
        print("  wait timeout #1, resending exit")
        write_in(b"exit\r\n")
        exit_code, reason, _ = proc.wait(timeout_ms=15000)
    print(f"  exit_code={exit_code} reason={reason}")
    proc.close()
    sb.shutdown()
    k32.ClosePseudoConsole(hpc)
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_main())