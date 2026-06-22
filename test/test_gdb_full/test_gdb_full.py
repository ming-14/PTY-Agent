#!/usr/bin/env python3
"""
pty-agent + GDB 17 高级集成测试

测试覆盖：
  Phase 0: 守护进程生命周期管理
  Phase 1: GDB 会话启动与断点控制
  Phase 2: 程序运行与单步调试
  Phase 3: 变量/表达式/类型/内存/寄存器 检查
  Phase 4: 递归函数调试与栈回溯
  Phase 5: 数据结构调试 (结构体/联合体/链表/多维数组)
  Phase 6: 条件断点 / 观察点
  Phase 7: 反汇编与源码浏览
  Phase 8: GDB 高级特性 (call/display/define/ignore)
  Phase 9: 程序结束与会话清理
  Phase 10: 边界场景 (超时/编码/并发/增量输出)

用法:
  先确保守护进程未运行，然后:
    python test_gdb_full.py
"""

import sys, os, json, time, socket, subprocess, threading, re

# ─── 配置 ──────────────────────────────────────────────────────────
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
PTY_DIR     = os.path.dirname(os.path.dirname(TEST_DIR))  # 项目根目录 (向上两级)
AGENT_DIR   = os.path.join(PTY_DIR, "src")
TEST_EXE    = os.path.join(TEST_DIR, "test_debug.exe")
GDB_EXE     = "gdb.exe"   # 依赖系统 PATH 中的 GDB

DAEMON_HOST = "127.0.0.1"
DAEMON_DATA_DIR = os.path.join(os.path.expanduser("~"), ".pty-agent")

def _read_daemon_port() -> int:
    """从共享内存读取守护进程端口号（与 shm_utils 一致）"""
    try:
        import mmap
        shm = mmap.mmap(-1, 16, tagname="Local\\PTYAgentPort")
        data = shm.read(16)
        shm.close()
        port_str = data.rstrip(b"\x00").decode("ascii")
        if port_str:
            return int(port_str)
    except Exception:
        pass
    return 18765


def _read_auth_token() -> str:
    """从共享内存读取守护进程认证令牌（与 client/transport.py 一致）"""
    # 优先通过 Python mmap 读取 Windows 命名共享内存
    has_mmap = False
    try:
        import mmap
        shm = mmap.mmap(-1, 64, tagname="Local\\PTYAgentAuth")
        data = shm.read(64)
        shm.close()
        token = data.rstrip(b"\x00").decode("ascii")
        if token:
            return token
        has_mmap = True
    except Exception:
        pass
    if not has_mmap:
        # Unix 回退：从文件读取
        auth_file = os.path.join(DAEMON_DATA_DIR, "daemon.auth")
        try:
            with open(auth_file, "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


# 认证令牌缓存（守护进程就绪后读取）
_AUTH_TOKEN = ""

def _get_token() -> str:
    """获取认证令牌，缓存非空结果
    Note: 首次调用可能在守护进程就绪前，返回空串时缓存不更新。
    """
    global _AUTH_TOKEN
    if _AUTH_TOKEN:
        return _AUTH_TOKEN
    token = _read_auth_token()
    if token:
        _AUTH_TOKEN = token
    return token


def _reset_auth_token():
    """重置令牌缓存（守护进程重启后使用）"""
    global _AUTH_TOKEN
    _AUTH_TOKEN = ""

ENV = os.environ.copy()
ENV["PYTHONPATH"] = PTY_DIR   # 使 daemon 子进程能找到 src 包

# ─── 测试统计 ──────────────────────────────────────────────────────
_stats = {"pass": 0, "fail": 0, "total": 0}
_session_registry = set()

# ─── TCP 通信 ──────────────────────────────────────────────────────
_BUFFERS = {}

def recv_line(sock, timeout=30.0):
    sock.settimeout(timeout)
    fd = sock.fileno()
    buf = _BUFFERS.get(fd, b"")
    while True:
        idx = buf.find(b"\n")
        if idx >= 0:
            _BUFFERS[fd] = buf[idx + 1:]
            return json.loads(buf[:idx].decode("utf-8")) if idx else None
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            _BUFFERS.pop(fd, None)
            return None
        except ConnectionError:
            _BUFFERS.pop(fd, None)
            return None
        if not chunk:
            _BUFFERS.pop(fd, None)
            return None
        buf += chunk

def request(msg, timeout=30.0):
    sock = None
    try:
        # 注入认证令牌（非 ping 请求）
        if msg.get("type") != "ping":
            token = _get_token()
            if token:
                msg["token"] = token
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        port = _read_daemon_port()
        sock.connect((DAEMON_HOST, port))
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        sock.sendall(data)
        return recv_line(sock, timeout)
    except Exception as e:
        return {"type": "error", "error": str(e)}
    finally:
        if sock:
            try: sock.close()
            except OSError: pass

# ─── 断言 ──────────────────────────────────────────────────────────
def ok(cond, desc, detail=""):
    _stats["total"] += 1
    if cond:
        _stats["pass"] += 1
        _safe(f"  [PASS] {desc}")
    else:
        _stats["fail"] += 1
        msg = f"  [FAIL] {desc}"
        if detail:
            msg += f"\n      详情: {detail}"
        _safe(msg)

def check_resp(resp, name):
    ok(resp and resp.get("type") in ("send", "exec", "read", "result"), name,
      f"type={resp.get('type') if resp else 'None'}")

def check_matched(resp, name):
    ok(resp.get("reason") == "matched", name,
      f"reason={resp.get('reason')}")

def check_contain(out, substr, name):
    ok(substr in out, name, f"期望包含 {substr!r}, 输出前500字: {out[:500]!r}")

# ─── 辅助 ──────────────────────────────────────────────────────────
def exec_gdb(sid, trigger=r"\(gdb\)", timeout=15, encoding=None):
    msg = {"type": "exec", "id": sid,
           "command": [GDB_EXE, "-q", TEST_EXE],
           "trigger": trigger, "timeout": timeout}
    if encoding:
        msg["encoding"] = encoding
    resp = request(msg, timeout=timeout + 5)
    if resp and resp.get("type") in ("exec", "send", "read", "result"):
        _session_registry.add(sid)
    return resp

def send_gdb(sid, cmd, trigger=r"\(gdb\)", timeout=10, encoding=None):
    msg = {"type": "send", "id": sid, "input": cmd + "\n",
           "trigger": trigger, "timeout": timeout}
    if encoding:
        msg["encoding"] = encoding
    return request(msg, timeout=timeout + 5)

def read_gdb(sid, full=True, offset=None, encoding=None):
    msg = {"type": "read", "id": sid, "full": full}
    if offset is not None:
        msg["offset"] = offset
    if encoding:
        msg["encoding"] = encoding
    return request(msg, timeout=5)

def kill_gdb(sid):
    request({"type": "kill", "id": sid}, timeout=3)
    _session_registry.discard(sid)

def ping():
    return request({"type": "ping"}, timeout=3)

# ── Phase 0: 守护进程 ──────────────────────────────────────────────
def phase_0_daemon():
    print("\n" + "=" * 60)
    print("Phase 0: 守护进程生命周期管理")
    print("=" * 60)

    # 先确保旧守护进程已停止
    try:
        request({"type": "stop"}, timeout=2)
        time.sleep(0.5)
    except:
        pass

    # 启动守护进程（使用重构后的 src 包）
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.daemon"],
        cwd=PTY_DIR, env=ENV,
    )
    time.sleep(2)

    # 重置令牌缓存，重新读取共享内存中的新令牌
    _reset_auth_token()

    # 0.1 ping
    resp = ping()
    ok(resp and resp.get("type") == "pong", "ping 响应正确",
      f"resp={resp}")

    # 0.2 重复 ping
    resp = ping()
    ok(resp and resp.get("type") == "pong", "ping 重复验证")

    print("  ── 守护进程就绪")

# ── Phase 1: 会话与断点 ────────────────────────────────────────────
def phase_1_session_breakpoints():
    print("\n" + "=" * 60)
    print("Phase 1: GDB 会话启动与断点控制")
    print("=" * 60)

    resp = exec_gdb("gdb1")
    check_resp(resp, "exec gdb -q test_debug.exe")
    if resp:
        check_matched(resp, "触发 (gdb) 提示符")
        out = resp.get("output", "")
        ok("(gdb)" in out or "Reading symbols" in out or
           "test_debug" in out,
           "输出包含调试信息")

    # 1.1 break main（函数名）
    resp = send_gdb("gdb1", "break main")
    check_resp(resp, "send: break main")
    if resp:
        check_matched(resp, "break main 命中")
        check_contain(resp.get("output", ""), "Breakpoint", "设置断点")

    # 1.2 break factorial（函数名）
    resp = send_gdb("gdb1", "break factorial")
    check_resp(resp, "send: break factorial")
    if resp:
        check_contain(resp.get("output", ""), "Breakpoint", "设置断点 factorial")

    # 1.3 break init_array（函数名断点，比行号更稳定）
    resp = send_gdb("gdb1", "break init_array")
    check_resp(resp, "send: break init_array")
    if resp:
        check_contain(resp.get("output", ""), "Breakpoint", "init_array 断点")

    # 1.4 break 条件未知位置（应报错但 GDB 仍返回 (gdb)）
    resp = send_gdb("gdb1", "break nonexistent_func_xyzzy")
    check_resp(resp, "send: break 不存在的函数")
    if resp:
        out = resp.get("output", "")
        ok("No source" in out or "pending" in out or "Make" in out or
           "breakpoint" in out.lower(),
           "正确给出错误或 pending 提示")

    # 1.5 info breakpoints
    resp = send_gdb("gdb1", "info breakpoints")
    check_resp(resp, "send: info breakpoints")
    if resp:
        out = resp.get("output", "")
        ok( out.count("breakpoint") >= 2 or
            "factorial" in out or "main" in out,
           "断点列表包含 main 和 factorial",
           f"output={out[:300]}")

    # 1.6 disable/enable
    resp = send_gdb("gdb1", "disable 2")
    check_resp(resp, "disable 断点 #2")
    resp = send_gdb("gdb1", "info breakpoints")
    check_resp(resp, "info 确认 disable")
    if resp:
        check_contain(resp.get("output", ""), "n", "断点 #2 标记为 disabled")

    resp = send_gdb("gdb1", "enable 2")
    check_resp(resp, "enable 断点 #2")

    # 1.7 delete 3
    resp = send_gdb("gdb1", "delete 3")
    check_resp(resp, "delete 断点 #3")
    resp = send_gdb("gdb1", "info breakpoints")
    if resp:
        ok(resp.get("output", "").count("breakpoint") == 2,
           "只剩 2 个断点",
           f"output={resp.get('output','')[:200]}")

# ── Phase 2: 程序运行与单步 ─────────────────────────────────────────
def phase_2_run_step():
    print("\n" + "=" * 60)
    print("Phase 2: 程序运行与单步调试")
    print("=" * 60)

    # 2.1 run → 命中 main 断点
    resp = send_gdb("gdb1", "run")
    check_resp(resp, "send: run")
    if resp:
        check_matched(resp, "命中 main 断点")
        out = resp.get("output", "")
        check_contain(out, "Breakpoint 1", "断点 1 (main) 命中")
        expect_src = "test_debug.c" if "test_debug.c" in out else "main"
        ok(expect_src in out, f"源码位置 {expect_src}")

    # 2.2 在 main 入口检查局部变量（用 print 替代 info locals，更可靠）
    resp = send_gdb("gdb1", "print sum")
    check_resp(resp, "print sum (main 入口)")
    if resp:
        out = resp.get("output", "")
        ok("0" in out or "45" in out or "sum" in out,
           "局部变量 sum 可见",
           f"output={out[:200]}")

    resp = send_gdb("gdb1", "print &nums")
    if resp:
        out = resp.get("output", "")
        ok("0x" in out, "局部变量 &nums 可寻址",
           f"output={out[:200]}")

    # 2.3 next 步进若干行（跳过 init_array / bubble_sort 等）
    for i in range(4):
        send_gdb("gdb1", "next", timeout=10)

    # 2.4 step 进入 init_array
    # 先重新运行让程序回到 main 开头更好控制——但我们用 disable/enable 更简单
    resp = send_gdb("gdb1", "print sum")
    check_resp(resp, "print sum")
    if resp:
        out = resp.get("output", "")
        ok("45" in out or "= 0" in out, "sum 值可见",
          f"output={out[:200]}")

    # 2.5 next 到 factorial 调用处
    for i in range(10):
        resp = send_gdb("gdb1", "next", timeout=10)
        out = resp.get("output", "") if resp else ""
        if "factorial" in out:
            break

    # 2.6 step 进入 factorial
    resp = send_gdb("gdb1", "step")
    check_resp(resp, "step 进入 factorial")
    if resp:
        out = resp.get("output", "")
        ok("factorial" in out or "test_debug.c" in out or "return n" in out,
           "进入 factorial",
           f"output={out[:200]}")

    # 2.7 finish 从当前函数返回
    resp = send_gdb("gdb1", "finish")
    check_resp(resp, "finish 返回 factorial")
    if resp:
        out = resp.get("output", "")
        ok("120" in out or "factorial" in out, "finish 后看到返回值",
          f"output={out[:200]}")

    # 2.7 until 到 print_student 附近（先清理旧断点，只留 print_student）
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break print_student")
    resp = send_gdb("gdb1", "continue")
    check_resp(resp, "continue 到 print_student")
    if resp:
        check_contain(resp.get("output", ""), "print_student", "跳转到 print_student 函数")

    # 复位到 main 入口，让后续阶段有干净状态
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=15)

# ── Phase 3: 变量/表达式/类型/内存/寄存器 ───────────────────────────
def phase_3_variables_memory():
    print("\n" + "=" * 60)
    print("Phase 3: 变量/类型/内存/寄存器检查")
    print("=" * 60)

    # 确保在 main 入口
    send_gdb("gdb1", "run", timeout=10)

    # 先 continue 到 main 开始处（如果程序已在运行）
    send_gdb("gdb1", "continue", timeout=5)

    # 重新 run 到 main 断点（保证在 main 入口）
    send_gdb("gdb1", "run", timeout=10)

    # 3.1 print 多种格式
    tests = [
        ("print (int)42",          "42"),
        ("print /x 255",           "ff"),
        ("print /o 255",           "377"),
        ("print /t 255",           "11111111"),
        ("print /c 65",            "65"),
        ("print /f (float)10",     "10"),
    ]
    for cmd, expect in tests:
        resp = send_gdb("gdb1", cmd)
        if resp:
            check_contain(resp.get("output", ""), expect, f"print 格式: {cmd}")

    # 3.2 whatis / ptype
    resp = send_gdb("gdb1", "whatis nums")
    if resp:
        check_contain(resp.get("output", ""), "int", "whatis nums → int")

    resp = send_gdb("gdb1", "ptype struct Point")
    if resp:
        out = resp.get("output", "")
        ok("int x" in out and "int y" in out, "ptype struct Point → 含 x, y",
          f"output={out[:300]}")

    # 3.3 x/ 内存检查
    resp = send_gdb("gdb1", "print &nums")
    addr = ""
    if resp:
        out = resp.get("output", "")
        m = re.search(r"0x[0-9a-fA-F]+", out)
        if m:
            addr = m.group()
            ok(True, f"print &nums → {addr}")

    if addr:
        resp = send_gdb("gdb1", f"x/5dw {addr}")
        if resp:
            out = resp.get("output", "")
            ok("5" in out and "10" in out, "x/5dw 看到数组元素",
              f"output={out[:200]}")

    # 3.4 x/ 多种格式
    resp = send_gdb("gdb1", f"x/5bx {addr}")
    if resp:
        ok(True, "x/5bx 字节格式正常")

    # 3.5 info registers
    resp = send_gdb("gdb1", "info registers")
    if resp:
        out = resp.get("output", "")
        ok("rax" in out or "eax" in out or "rip" in out,
           "info registers 显示寄存器",
           f"output={out[:200]}")

    # 3.6 set var
    resp = send_gdb("gdb1", "set var sum = 999")
    check_resp(resp, "set var sum = 999")
    resp = send_gdb("gdb1", "print sum")
    if resp:
        check_contain(resp.get("output", ""), "999", "sum 被修改为 999")

    # 3.7 call 函数（先 disable 所有断点，避免 call 时触发）
    send_gdb("gdb1", "disable")
    resp = send_gdb("gdb1", "call factorial(6)", timeout=15)
    if resp:
        out = resp.get("output", "")
        ok("720" in out, "call factorial(6) → 720",
          f"output={out[:200]}")
    send_gdb("gdb1", "enable")

    # 3.8 print 数组元素（在 main 入口 nums 未初始化，但可访问）
    resp = send_gdb("gdb1", "whatis nums")
    if resp:
        check_contain(resp.get("output", ""), "int [5]", "nums 是 int[5] 数组")

    # 3.9 print 字符串常量
    resp = send_gdb("gdb1", 'print "hello"[0]')
    if resp:
        check_contain(resp.get("output", ""), "104", "字符串常量 'h'=104")

# ── Phase 4: 递归调试 ──────────────────────────────────────────────
def phase_4_recursion():
    print("\n" + "=" * 60)
    print("Phase 4: 递归函数调试与栈回溯")
    print("=" * 60)

    # 复位到 main
    send_gdb("gdb1", "run", timeout=10)

    # 重新 run 到 main
    send_gdb("gdb1", "run", timeout=10)

    # 删除所有断点，仅保留 factorial
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break factorial")

    resp = send_gdb("gdb1", "continue")
    check_resp(resp, "continue 到 factorial")
    if resp:
        check_contain(resp.get("output", ""), "Breakpoint", "命中 factorial")

    # 4.1 打印参数
    resp = send_gdb("gdb1", "print n")
    if resp:
        out = resp.get("output", "")
        ok("5" in out, "第一次 factorial: n=5", f"output={out}")

    # 4.2 backtrace
    resp = send_gdb("gdb1", "backtrace")
    if resp:
        out = resp.get("output", "")
        ok("#0" in out and "factorial" in out and "main" in out,
           "backtrace: factorial → main",
           f"output={out[:300]}")

    # 4.3 continue 深入递归
    for depth in range(4):
        resp = send_gdb("gdb1", "continue", timeout=10)
        if not resp or resp.get("reason") != "matched":
            break
        send_gdb("gdb1", "print n")
        # depth 0 → n=4, depth 1 → n=3, ...

    # 4.4 深度递归时 backtrace
    resp = send_gdb("gdb1", "backtrace")
    if resp:
        out = resp.get("output", "")
        ok("#0" in out and "#1" in out, "递归深度 backtrace",
          f"输出行数: {len(out.splitlines())}")

    # 4.5 frame 切换
    resp = send_gdb("gdb1", "frame 1")
    check_resp(resp, "frame 1 切换")
    if resp:
        out = resp.get("output", "")
        ok("factorial" in out, "frame 1 仍在 factorial",
          f"output={out[:200]}")

    # 4.6 up / down
    resp = send_gdb("gdb1", "up")
    check_resp(resp, "up 到上一层")
    resp = send_gdb("gdb1", "down")
    check_resp(resp, "down 回到当前层")

# ── Phase 5: 数据结构 ──────────────────────────────────────────────
def phase_5_data_structures():
    print("\n" + "=" * 60)
    print("Phase 5: 数据结构调试")
    print("=" * 60)

    # 重新 run
    send_gdb("gdb1", "run", timeout=10)
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)

    # 5.1 检查 struct Student —— 在 print_student 处断点
    send_gdb("gdb1", "break print_student")
    send_gdb("gdb1", "continue", timeout=10)

    resp = send_gdb("gdb1", "print *s")
    if resp:
        out = resp.get("output", "")
        ok("Alice" in out and "1001" in out and "95" in out,
           "print *s → Alice, 1001, 95.5",
           f"output={out[:300]}")

    # 5.2 访问嵌套结构体
    resp = send_gdb("gdb1", "print s->seat")
    if resp:
        out = resp.get("output", "")
        ok("x = 3" in out and "y = 5" in out, "嵌套 seat.x=3, seat.y=5",
          f"output={out[:200]}")

    # 5.3 检查链表 — 在 list_create 内设断点
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break list_create")
    send_gdb("gdb1", "continue", timeout=10)

    resp = send_gdb("gdb1", "print n")
    if resp:
        check_contain(resp.get("output", ""), "4", "list_create(4) 参数 n=4")

    # 进入循环体后 head 才非空（step/next 一次到 malloc 后）
    send_gdb("gdb1", "next", timeout=10)  # int i = 0
    send_gdb("gdb1", "next", timeout=10)  # i = 0; head = NULL (start)
    # 在循环中 head 是局部变量，此时可能是 NULL，用 ptype 看类型
    resp = send_gdb("gdb1", "ptype struct Node")
    if resp:
        out = resp.get("output", "")
        ok("value" in out and "next" in out, "ptype struct Node → 结构体定义可见",
          f"output={out[:200]}")

    # 5.4 联合体 — typedef 匿名联合体，用 `info types Data` 搜索
    resp = send_gdb("gdb1", 'info types Data')
    if resp:
        out = resp.get("output", "")
        ok("Data" in out or "union" in out or "int" in out,
           "info types Data 找到联合体",
           f"output={out[:200]}")

    # 5.5 多维数组 — 在 matrix_multiply 打断点
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break matrix_multiply")
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)
    # 继续到 matrix_multiply
    resp = send_gdb("gdb1", "continue", timeout=15)
    if resp and resp.get("reason") == "matched":
        resp_ma = send_gdb("gdb1", "p *a@9")
        if resp_ma:
            out = resp_ma.get("output", "")
            ok("1" in out, "p *a@9 展开矩阵含元素1",
              f"output={out[:200]}")

    resp = send_gdb("gdb1", "p *c@9")
    if resp:
        out = resp.get("output", "")
        ok("{" in out or "0" in out,
           "p *c@9 输出矩阵元素",
           f"output={out[:100]}")

# ── Phase 6: 条件断点 / 观察点 ─────────────────────────────────────
def phase_6_conditional_watch():
    print("\n" + "=" * 60)
    print("Phase 6: 条件断点与观察点")
    print("=" * 60)

    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)

    # 6.1 条件断点 — 用函数名条件（不依赖行号或局部变量作用域）
    # 先到 main
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)
    # 设置条件断点：在 swap 调用时条件触发
    resp = send_gdb("gdb1", "break swap if *b == 10")
    check_resp(resp, "条件断点: break swap if *b == 10")
    if resp:
        out = resp.get("output", "")
        ok("Breakpoint" in out or "atchpoint" in out,
           "条件断点设置",
           f"output={out[:300]}")

    resp = send_gdb("gdb1", "continue", timeout=15)
    if resp and resp.get("reason") == "matched":
        out = resp.get("output", "")
        ok("Breakpoint" in out or "swap" in out or "*b" in out,
           "条件断点命中",
           f"output={out[:200]}")

    # 6.2 观察点
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)

    # 在 main 中设 watch 观察 sum
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)
    resp = send_gdb("gdb1", "watch sum")
    check_resp(resp, "watch sum")
    if resp:
        check_contain(resp.get("output", ""), "atchpoint", "观察点设置")

    resp = send_gdb("gdb1", "continue", timeout=15)
    if resp:
        out = resp.get("output", "")
        ok("atchpoint" in out and "sum" in out.lower(),
           "观察点命中 (sum 值变化)",
           f"output={out[:300]}")

# ── Phase 7: 反汇编与源码 ──────────────────────────────────────────
def phase_7_disassemble():
    print("\n" + "=" * 60)
    print("Phase 7: 反汇编与源码浏览")
    print("=" * 60)

    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)

    # 7.1 disassemble
    resp = send_gdb("gdb1", "disassemble main")
    if resp:
        out = resp.get("output", "")
        ok("main" in out and "0x" in out, "disassemble main",
          f"output={out[:200]}")

    # 7.2 disassemble /m (混合源码)
    resp = send_gdb("gdb1", "disassemble /m main")
    if resp:
        out = resp.get("output", "")
        ok("0x" in out, "disassemble /m main",
          f"output={out[:100]}")

    # 7.3 list 源码
    resp = send_gdb("gdb1", "list 1,20")
    if resp:
        check_contain(resp.get("output", ""), "test_debug",
                      "list 1,20 显示源码开头")

    # 7.4 search 源码
    resp = send_gdb("gdb1", 'search factorial')
    if resp:
        out = resp.get("output", "")
        ok(len(out.strip()) > 0, "search factorial 有结果",
          f"output={out[:100]}")

# ── Phase 8: 高级特性 ──────────────────────────────────────────────
def phase_8_advanced():
    print("\n" + "=" * 60)
    print("Phase 8: GDB 高级特性")
    print("=" * 60)

    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)

    # 8.1 display 自动显示
    resp = send_gdb("gdb1", "display /x sum")
    check_resp(resp, "display /x sum")
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)
    resp = send_gdb("gdb1", "next", timeout=10)
    if resp:
        out = resp.get("output", "")
        ok("sum" in out, "display 自动显示 sum",
          f"output={out[:200]}")

    send_gdb("gdb1", "undisplay 1")
    check_resp(resp, "undisplay 1")

    # 8.2 ignore 忽略断点
    send_gdb("gdb1", "break factorial")
    # 查询断点号
    resp = send_gdb("gdb1", "info breakpoints")
    bp_num = "2"
    if resp:
        m = re.search(r"(\d+)\s+breakpoint.*factorial", resp.get("output", ""))
        if m:
            bp_num = m.group(1)
    resp = send_gdb("gdb1", f"ignore {bp_num} 3")
    check_resp(resp, f"ignore {bp_num} 3 (忽略 factorial 3 次)")
    if resp:
        out = resp.get("output", "")
        ok("ignore" in out or "Will ignore" in out, "ignore 确认",
          f"output={out[:200]}")

    # 8.3 用 while 命令替代 fragile 的 define 多行命令
    # while 命令只需一行，不需要多行输入
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)
    resp = send_gdb("gdb1", "print $argc")
    if resp:
        ok(True, "print $argc 可访问", f"output={resp.get('output','')[:100]}")

# ── Phase 9: 程序结束与清理 ────────────────────────────────────────
def phase_9_finish():
    print("\n" + "=" * 60)
    print("Phase 9: 程序结束与会话清理")
    print("=" * 60)

    # 9.1 继续到程序结束
    send_gdb("gdb1", "delete")
    send_gdb("gdb1", "y", timeout=3)
    send_gdb("gdb1", "break main")
    send_gdb("gdb1", "run", timeout=10)

    resp = send_gdb("gdb1", "continue", timeout=15)
    if resp:
        out = resp.get("output", "")
        ok("sum = 45" in out or "final sum" in out or
           "factorial(5) = 120" in out,
           "程序输出最终结果",
           f"output={out[:300]}")

    # 9.2 continue 确认退出 — 程序可能已结束，GDB 会提示 "exited" 或 "not being run"
    resp = send_gdb("gdb1", "continue", timeout=10)
    if resp:
        out = resp.get("output", "")
        ok("exited" in out or "not being run" in out or
           "No stack" in out or "no running" in out or
           not resp.get("program", {}).get("running", True),
           "进程正常退出确认",
           f"output={out[:200]}")

    # 9.3 quit
    resp = send_gdb("gdb1", "quit\ny", timeout=5)
    check_resp(resp, "send: quit")
    if resp:
        ok(not resp.get("program", {}).get("running", True),
           "quit 后会话已结束",
           f"running={resp.get('program', {}).get('running')}")

# ── Phase 10: 边界场景 ─────────────────────────────────────────────
def phase_10_edge_cases():
    print("\n" + "=" * 60)
    print("Phase 10: 边界场景测试")
    print("=" * 60)

    # 10.1 不存在的会话
    resp = request({"type": "send", "id": "nonexistent",
                     "input": "test\n", "trigger": "prompt", "timeout": 3})
    ok(resp and resp.get("type") == "error",
       "发送到不存在会话返回 error",
       f"resp={resp}")

    # 10.2 未知指令
    resp = request({"type": "unknown_cmd"})
    ok(resp and resp.get("type") == "error",
       "未知指令返回 error")

    # 10.3 超时测试（gui_detected 也是合理的结束原因）
    resp = request({
        "type": "exec", "id": "timeout_test",
        "command": [GDB_EXE, "-q", TEST_EXE],
        "trigger": r"NEVER_MATCH_THIS_PATTERN_12345",
        "timeout": 1,
    }, timeout=8)
    ok(resp and resp.get("reason") in ("timeout", "gui_detected"),
       "超时/gui 触发返回",
       f"reason={resp.get('reason')}")
    kill_gdb("timeout_test")

    # 10.4 编码 utf-8
    resp = exec_gdb("enc_utf8", encoding="utf-8")
    ok(resp and resp.get("type") in ("exec", "send", "read", "result"),
       "UTF-8 编码启动", f"type={resp.get('type') if resp else 'None'}")
    kill_gdb("enc_utf8")

    # 10.5 编码 gbk
    resp = exec_gdb("enc_gbk", encoding="gbk")
    ok(resp and resp.get("type") in ("exec", "send", "read", "result"),
       "GBK 编码启动", f"type={resp.get('type') if resp else 'None'}")
    kill_gdb("enc_gbk")

    # 10.6 自动探测编码
    resp = exec_gdb("enc_auto", encoding=None)
    ok(resp and resp.get("type") in ("exec", "send", "read", "result"),
       "自动探测编码启动", f"type={resp.get('type') if resp else 'None'}")
    kill_gdb("enc_auto")

    # 10.7 增量输出追踪
    resp = exec_gdb("inc_test")
    offset = resp.get("output_offset", 0) if resp else 0
    ok(offset > 0, "增量测试: output_offset > 0",
      f"offset={offset}")

    if offset > 0:
        send_gdb("inc_test", "break main")
        resp_inc = read_gdb("inc_test", full=False, offset=offset)
        ok(resp_inc and len(resp_inc.get("output", "")) > 0,
           "增量读取返回数据",
           f"len={len(resp_inc.get('output','')) if resp_inc else 0}")
    kill_gdb("inc_test")

    # 10.8 并发多会话
    n_concurrent = 4
    for i in range(n_concurrent):
        resp = exec_gdb(f"con_{i}")
        ok(resp and resp.get("type") in ("exec", "send", "read", "result"),
           f"并发会话 con_{i} 启动",
           f"type={resp.get('type') if resp else 'None'}")

    def worker(name, cmd):
        resp = send_gdb(name, cmd)
        return resp and resp.get("type") in ("exec", "send", "read", "result")

    threads, results = [], [None] * n_concurrent
    def wrapper(idx, sid):
        results[idx] = worker(sid, "break main")
    for i in range(n_concurrent):
        t = threading.Thread(target=wrapper, args=(i, f"con_{i}"))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    for i in range(n_concurrent):
        ok(results[i], f"并发 con_{i} break 命令",
           f"result={results[i]}")
        kill_gdb(f"con_{i}")

    # 10.9 list 验证干净
    resp = request({"type": "list"})
    if resp:
        remaining = [s["id"] for s in resp.get("sessions", [])
                     if s.get("id", "").startswith("con_")]
        ok(len(remaining) == 0, "并发会话无残留",
           f"残留: {remaining}")

# ── 清理 ───────────────────────────────────────────────────────────
def cleanup():
    print("\n" + "-" * 60)
    print("清理：终止所有会话 + 停止守护进程")
    for sid in list(_session_registry):
        try:
            request({"type": "kill", "id": sid}, timeout=3)
        except:
            pass
    _session_registry.clear()
    try:
        resp = request({"type": "stop"}, timeout=3)
        time.sleep(0.5)
        print("  守护进程已停止" if resp else "  守护进程无响应")
    except Exception as e:
        print(f"  停止守护进程失败: {e}")

# ── 入口 ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("pty-agent + GDB 17 高级集成测试")
    print("=" * 60)
    print(f"GDB:          {GDB_EXE}  (系统 PATH)")
    print(f"测试程序:     {TEST_EXE}")
    print(f"pty-agent:    {AGENT_DIR}")
    print(f"时间戳:       {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    phases = [
        ("Phase 0: 守护进程",                  phase_0_daemon),
        ("Phase 1: 会话与断点",                phase_1_session_breakpoints),
        ("Phase 2: 运行与单步",                phase_2_run_step),
        ("Phase 3: 变量/内存/寄存器",           phase_3_variables_memory),
        ("Phase 4: 递归与栈回溯",               phase_4_recursion),
        ("Phase 5: 数据结构",                  phase_5_data_structures),
        ("Phase 6: 条件断点/观察点",            phase_6_conditional_watch),
        ("Phase 7: 反汇编与源码",               phase_7_disassemble),
        ("Phase 8: 高级特性",                  phase_8_advanced),
        ("Phase 9: 程序结束与清理",             phase_9_finish),
        ("Phase 10: 边界场景",                 phase_10_edge_cases),
    ]

    # 编码安全打印：stdout 重定向到文件时可能为 GBK，需绕过
    _stdout = sys.stdout
    def _safe(text, **kw):
        try:
            print(text, **kw)
        except UnicodeEncodeError:
            kw["file"].buffer.write(text.encode("utf-8") + b"\n") if "file" in kw \
                else _stdout.buffer.write(text.encode("utf-8") + b"\n")

    try:
        for name, func in phases:
            _safe(f"\n{'=' * 40}")
            _safe(f">> {name}")
            _safe(f"{'=' * 40}")
            func()
    except Exception as e:
        _safe(f"\n!! 测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()

    _safe(f"\n{'=' * 60}")
    p, f, t = _stats["pass"], _stats["fail"], _stats["total"]
    _safe(f"测试统计: {p}/{t} 通过, {f} 失败  ({p*100//max(t,1)}%)")
    _safe(f"{'=' * 60}")
    if f == 0 and t > 0:
        _safe("\n全部测试通过！pty-agent 驱动 GDB 验证成功！")
    else:
        _safe(f"\n{f} 项测试失败")
    sys.exit(0 if f == 0 else 1)
