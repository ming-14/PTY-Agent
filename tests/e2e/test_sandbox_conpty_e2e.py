"""E2E：沙箱 ConPTY 完整终端语义验证（SandboxPty + 外部传入 hpcon）

覆盖（对齐 win-sandbox tests/e2e/test_hpcon_conpty.py 已验证链路）：
  1. banner 输出到达 ConPTY 输出管道
  2. 子进程 isatty=True（终端语义完整）
  3. 回显 / 方向键历史回调
  4. Ctrl+C 中断（\x03 经 ConPTY 输入管道）
  5. resize（ResizePseudoConsole 直调）
  6. exit 正常退出 + 资源清理（沙箱 shutdown）

前置：
  - Windows 10 19041+；需有效控制台会话（无头宿主会 0xC0000142，环境限制）
  - bin/win_sandbox/_native/win_sandbox_native*.pyd 已构建（vendored）

运行：
  python tests/e2e/test_sandbox_conpty_e2e.py
  或 pytest tests/e2e/test_sandbox_conpty_e2e.py
"""

import glob
import os
import re
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

# win-sandbox 为 Windows 原生组件；非 Windows 平台模块级跳过（import 前）
if sys.platform != "win32":
    pytest.skip("沙箱 ConPTY 仅支持 Windows", allow_module_level=True)

from src.sandbox.manager import SandboxSessionManager  # noqa: E402
from src.sandbox.pty import SandboxPty  # noqa: E402

_HAS_SANDBOX = bool(glob.glob(os.path.join(
    _PROJECT_ROOT, "bin", "win_sandbox", "_native", "win_sandbox_native*.pyd")))

# 沙箱隔离：无网络隔离 + 剪贴板不隔离（无路径白名单/能力集）
_ISOLATION = {
    "net_policy": "unrestricted",
    "net_allowlist": [],
    "clipboard_isolate": False,
}
_QUOTA = {"memory_mb": 256, "max_processes": 64, "crash_silent": True}


def _read_available(pty, timeout: float) -> bytes:
    """轮询读取 ConPTY 输出；数据安静 0.5s 视为一轮结束（对齐 win-sandbox e2e）"""
    chunks = []
    idle = 0.0
    deadline = time.time() + timeout
    while True:
        data = pty.drain(65536)
        if data:
            chunks.append(data)
            idle = time.time() + 0.5
            continue
        if idle and time.time() >= idle:
            break
        if time.time() >= deadline:
            break
        time.sleep(0.05)
    return b"".join(chunks)


def _strip_trailing_ansi(data: bytes) -> bytes:
    """剥除尾部 ANSI 转义序列（CSI/OSC），还原文本语义尾

    ConPTY 会在输出尾部追加光标显示（\x1b[?25h）、光标定位（\x1b[21;1H）等
    CSI 序列，直接以 endswith 判 prompt 会误判；文本语义判断前先剥净尾部序列。
    """
    while True:
        csi = re.search(rb"\x1b\[[0-9;?]*[ -/]*[@-~]$", data)
        if csi:
            data = data[: csi.start()]
            continue
        osc = re.search(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)$", data)
        if osc:
            data = data[: osc.start()]
            continue
        return data


def _run() -> int:
    # cwd 不显式设置（WRITE_RESTRICTED 语义：以宿主 %TEMP% 下目录为工作
    # 目录会被拒——"当前目录无效"；继承父进程项目根即可，可读可遍历）
    mgr = SandboxSessionManager(quota=dict(_QUOTA), isolation=dict(_ISOLATION),
                                log_level="info")
    pty = SandboxPty(["cmd.exe"], 120, 30, cwd=None, tracker=None, manager=mgr)
    try:
        # 1. banner
        data = _read_available(pty, 3.0)
        if b"Microsoft Windows" not in data:
            print(f"FAIL: banner missing: {data[:80]!r}")
            return 1
        print(f"  banner ok ({len(data)} bytes)")

        # 2. isatty（子进程在 ConPTY 中运行，stdin/stdout 均应为终端）
        pty.write(b"python -c \"import sys; print('TTY', sys.stdin.isatty(), sys.stdout.isatty())\"\r\n")
        data = _read_available(pty, 5.0)
        if b"TTY True True" not in data:
            print(f"FAIL: isatty expected True True: {data!r}")
            return 1
        print("  isatty True True ok")

        # 3. 回显
        pty.write(b"echo HELLO_HPCON_E2E\r\n")
        data = _read_available(pty, 3.0)
        if b"HELLO_HPCON_E2E" not in data:
            print(f"FAIL: echo {data!r}")
            return 1
        print("  echo ok")

        # 4. 方向键历史（UP 箭头回调上一条命令）
        pty.write(b"echo FIRST_HISTORY\r\n")
        _read_available(pty, 3.0)
        pty.write(b"\x1b[A\r\n")
        data = _read_available(pty, 3.0)
        if b"FIRST_HISTORY" not in data:
            print(f"FAIL: history {data!r}")
            return 1
        print("  history ok")

        # 5. Ctrl+C 中断（\x03 走 ConPTY 输入管道；事件送达有竞态，双发 +
        #    "进程树收敛" 旁证；resize 移到中断后避免全屏重绘与输入竞态）。
        #    目标选 sort（阻塞读 stdin 的常驻进程，无网络依赖）：比 ping 更
        #    稳定（无 ICMP 语义差异），可中断目标语义明确。
        #    中断语义断言用进程树：sort 以 STATUS_CONTROL_C_EXIT 终止后 Job 内
        #    只剩根进程（^C 文本标记是 conhost 渲染细节）
        pty.write(b"sort\r\n")
        time.sleep(1.5)
        pty.write(b"\x03")
        time.sleep(0.8)
        pty.write(b"\x03")
        data = _read_available(pty, 8.0)
        # 尾部可能有 CSI（光标显示等）序列，剥净后再判 prompt 文本尾
        prompt_back = _strip_trailing_ansi(data).rstrip().endswith(b">")
        root_alive = pty.get_exit_code() is None
        sort_gone = mgr.get_process_list() == [pty.get_child_pid()]
        marker = b"Control-C" in data or b"^C" in data
        if not (prompt_back and root_alive and sort_gone):
            print(f"FAIL: ctrl+c not effective: prompt_back={prompt_back} "
                  f"root_alive={root_alive} sort_gone={sort_gone} "
                  f"marker={marker} {data[-150:]!r}")
            return 1
        print(f"  ctrl+c ok (sort interrupted, marker={marker})")

        # 6. resize（中断后，避免 conhost 全屏重绘与输入竞态）
        pty.resize(100, 25)
        print("  resize ok (100x25)")

        # resize 触发 conhost 全屏重绘；重绘输出未消费完前发送输入可能被吞，
        # 先 drain 至输出静止再继续
        _read_available(pty, 2.0)

        # 7. exit（正常退出 + wait 非阻塞探测）
        pty.write(b"exit\r\n")
        t0 = time.time()
        while time.time() - t0 < 20.0:
            code = pty.get_exit_code()
            if code is not None:
                break
            time.sleep(0.2)
        if code != 0:
            print(f"FAIL: exit_code expected 0, got {code}")
            return 1
        print(f"  exit ok (exit_code={code})")
        return 0
    finally:
        try:
            pty.close()
        finally:
            mgr.close()


@pytest.mark.skipif(
    sys.platform != "win32", reason="沙箱 ConPTY 仅支持 Windows"
)
@pytest.mark.skipif(not _HAS_SANDBOX, reason="win_sandbox_native 未构建")
def test_sandbox_conpty_full_semantics():
    assert _run() == 0


if __name__ == "__main__":
    sys.exit(_run())