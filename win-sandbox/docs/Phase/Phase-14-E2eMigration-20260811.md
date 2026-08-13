# Phase 14: e2e 测试迁移

**Phase 编号**: 14
**Phase 名称**: e2e 测试套件迁移到 pybind11 直调形态
**创建日期**: 2026-08-11
**预计工期**: 3 个工作日
**负责人**: rikka
**状态**: ✅ 已完成
**上游依赖**: Phase 13（已完成，helpers + ETW 回调齐备）
**下游影响**: Phase 15（全量回归）

---

## 1. Phase 目标

### 1.1 总体目标

Phase 12 已删除 4 套 IPC 专属测试，Phase 11/13 新增 6 套 native 测试。本 Phase 将**剩余 19 套 e2e 测试**从 IPC 形态（`SandboxClient` + `send_*` + `collect_events`）迁移到 pybind11 直调形态（`SandboxInstance` + `start_process` + 句柄读写 + `wait` + 回调）。

迁移后全部 e2e 测试基于 pybind11，无任何 IPC 拗留，验证新形态功能完整性与隔离语义正确性。

### 1.2 非目标

- 不改 C++ 核心 / bindings / helpers（Phase 11/13 产出）
- 不构建 wheel（Phase 15）
- 不更新文档（Phase 15）
- 不迁移 blackbox_phase8/9（基于 IPC 的黑盒脚本，归档处理）

---

## 2. 功能需求

### 2.1 迁移清单

| 套件 | 用例数 | 迁移要点 | 优先级 |
|------|--------|----------|--------|
| `smoke.py` | — | 基础冒烟 → `SandboxInstance` 构造/销毁 | P0 |
| `test_appcontainer.py` | 5 | AppContainer 隔离验证，stdout 读取改 helpers | P0 |
| `test_filesystem.py` | 7 | 4 种 fs 模式 + 退出策略，文件操作验证 | P0 |
| `test_resource_quota.py` | 6 | 内存/CPU/进程数限制，wall_clock 改 helpers.WallClockTimer | P0 |
| `test_network.py` | 5 | 4 种网络策略，出站连接验证 | P0 |
| `test_network_allowlist.py` | 4 | SOCKS5 代理 allowlist | P1 |
| `test_write_stdin.py` | 6 | stdin 交互写入，改 helpers.write_pipe | P0 |
| `test_signal.py` | 5 | CtrlBreak / Kill 信号 | P0 |
| `test_multiprocess.py` | 6 | 多进程并行托管 | P1 |
| `test_behavior_log.py` | 5 | ETW 行为事件，改 on_behavior_event 回调 | P1 |
| `test_degraded_monitor.py` | 6 | 降级监控（进程/文件/网络轮询） | P1 |
| `test_etw_admin.py` | 8 | 管理员模式真 ETW（需管理员） | P2 |
| `test_cleanup.py` | 2 | 拋留清理验证 | P1 |
| `test_permission_matrix.py` | 2 | Admin / StandardUser 权限矩阵 | P1 |
| `test_oj_scenario.py` | 4 | OJ 场景模拟 | P1 |
| `test_scenario_c_sample.py` | 1 | 样本分析场景 | P2 |
| `test_scenario_d_ci.py` | 1 | CI 多实例并行 | P2 |
| `test_global_quota.py` | 5 | 全局配额跨实例共享 | P1 |
| `test_silo.py` | 4 | Server Silo 更强隔离 | P2 |
| `test_job_enhancement.py` | 6 | Job 功能增强（Phase 8） | P1 |
| `test_process_tree.py` | 11 | 进程树 IPC（Phase 9）→ 回调验证 | P0 |

**总计：19 套件迁移**（Phase 12 已删 4 套 IPC 专属，Phase 11/13 已新增 6 套 native）

### 2.2 迁移模式

#### 2.2.1 通用迁移模式

```python
# ===== 旧（IPC 形态）=====
from win_sandbox import SandboxClient

client = SandboxClient(exe_path=r"build/bin/sandbox.exe", pipe_name=r"\\.\pipe\test")
client.start()
client.wait_ready()

client.send_start_process("cmd.exe /c echo hello", quota={"memory_mb": 256})
events = client.collect_events_until_exit(timeout=10.0)
for e in events:
    if e.type == "process_output":
        print(e.payload.get("data", ""), end="")
    elif e.type == "process_exited":
        print(f"exit: {e.payload['exit_code']}")

client.send_shutdown()
client.wait_exit()
client.close()

# ===== 新（pybind11 直调）=====
import sys; sys.path.insert(0, r"build/bin")
import win_sandbox_native
from win_sandbox import helpers

sb = win_sandbox_native.SandboxInstance(log_level="info")

proc = sb.start_process(
    command_line="cmd.exe /c echo hello",
    quota={"memory_mb": 256},
)

# 自己读 stdout
out = b""
while True:
    data = helpers.read_pipe(proc.stdout_handle, 65536)
    if not data:
        break
    out += data
print(out.decode("utf-8", errors="replace"), end="")

exit_code, reason, usage = proc.wait(timeout_ms=10000)
print(f"exit: {exit_code}")

proc.close()
sb.shutdown()
```

#### 2.2.2 回调迁移模式

```python
# ===== 旧（IPC 事件流）=====
events = client.collect_events_until_exit(timeout=30.0)
for e in events:
    if e.type == "resource_limit_hit":
        handle_limit(e.payload)
    elif e.type == "job_process_started":
        handle_child_start(e.payload)

# ===== 新（pybind11 回调）=====
limit_events = []
child_starts = []

proc.on_resource_limit = lambda info: limit_events.append(info)
proc.on_job_process_started = lambda info: child_starts.append(info)

# ... 读 stdout + wait ...
proc.wait(timeout_ms=30000)

# 断言回调
assert len(limit_events) > 0
assert len(child_starts) == expected_children
```

#### 2.2.3 wall_clock 迁移

```python
# ===== 旧（IPC 配额 wall_clock_timeout_ms）=====
client.send_start_process("python -c 'while True: pass'",
    quota={"wall_clock_timeout_ms": 5000})

# ===== 新（Python helpers.WallClockTimer）=====
proc = sb.start_process("python -c 'while True: pass'", quota={"memory_mb": 256})
timer = helpers.WallClockTimer(proc, timeout_ms=5000, exit_code=1)
timer.start()
exit_code, reason, usage = proc.wait(timeout_ms=10000)
timer.cancel()
assert reason == "wall_clock_timeout"  # 或 "killed_by_user"
```

### 2.3 blackbox_phase8/9 处置

`tests/e2e/blackbox_phase8/` / `blackbox_phase9/` 基于 IPC 黑盒测试脚本。本 Phase **归档**（移到 `tests/e2e/archive/`），不迁移。新形态的黑盒测试待后续按需编写。

---

## 3. 技术设计

### 3.1 测试辅助函数

新增 `tests/e2e/_native_helpers.py`（测试共用工具）：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "build", "bin"))

import win_sandbox_native
from win_sandbox import helpers

def make_sandbox(**kwargs):
    """创建默认 SandboxInstance。"""
    return win_sandbox_native.SandboxInstance(log_level="debug", **kwargs)

def run_and_capture(command_line, **kwargs):
    """启动进程 + 读全部 stdout + wait，返回 (exit_code, stdout_bytes, reason, usage)。"""
    sb = make_sandbox()
    proc = sb.start_process(command_line=command_line, **kwargs)
    out = b""
    while True:
        data = helpers.read_pipe(proc.stdout_handle, 65536)
        if not data:
            break
        out += data
    exit_code, reason, usage = proc.wait(timeout_ms=30000)
    proc.close()
    sb.shutdown()
    return exit_code, out, reason, usage

def drain_async(proc, stream="stdout"):
    """后台 drain stdout/stderr，返回 (thread, collector_list)。"""
    collected = []
    handle = proc.stdout_handle if stream == "stdout" else proc.stderr_handle
    def _loop():
        while True:
            data = helpers.read_pipe(handle, 65536)
            if not data:
                break
            collected.append(data)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t, collected
```

### 3.2 迁移顺序（按依赖与风险）

1. **基础**：`smoke.py`（验证基础流程）
2. **隔离核心**：`test_appcontainer.py` → `test_filesystem.py` → `test_network.py` → `test_network_allowlist.py`
3. **资源控制**：`test_resource_quota.py` → `test_job_enhancement.py`
4. **进程控制**：`test_write_stdin.py` → `test_signal.py` → `test_multiprocess.py`
5. **进程树**：`test_process_tree.py`（回调验证）
6. **监控**：`test_behavior_log.py` → `test_degraded_monitor.py` → `test_etw_admin.py`
7. **全局能力**：`test_global_quota.py` → `test_silo.py`
8. **场景**：`test_oj_scenario.py` → `test_scenario_c_sample.py` → `test_scenario_d_ci.py`
9. **清理/权限**：`test_cleanup.py` → `test_permission_matrix.py`

每套件迁移后立即运行验证，全绿再迁移下一套。

### 3.3 `run_all_regression.py` 更新

```python
# tests/e2e/run_all_regression.py
# glob 自动收录 test_*.py，排除 test_etw_admin.py（需管理员）
# Phase 12 已删 4 套 IPC 专属，本 Phase 迁移剩余 19 套
# 新增 6 套 native（Phase 11/13）已收录
```

---

## 4. 任务拆分

| 任务 | 描述 | 产出 |
|------|------|------|
| T14.1 | 新增 `tests/e2e/_native_helpers.py` | 测试辅助 |
| T14.2 | 迁移 `smoke.py` | 改写 |
| T14.3 | 迁移 `test_appcontainer.py`（5 用例） | 改写 |
| T14.4 | 迁移 `test_filesystem.py`（7 用例） | 改写 |
| T14.5 | 迁移 `test_network.py`（5 用例） | 改写 |
| T14.6 | 迁移 `test_network_allowlist.py`（4 用例） | 改写 |
| T14.7 | 迁移 `test_resource_quota.py`（6 用例） | 改写 |
| T14.8 | 迁移 `test_job_enhancement.py`（6 用例） | 改写 |
| T14.9 | 迁移 `test_write_stdin.py`（6 用例） | 改写 |
| T14.10 | 迁移 `test_signal.py`（5 用例） | 改写 |
| T14.11 | 迁移 `test_multiprocess.py`（6 用例） | 改写 |
| T14.12 | 迁移 `test_process_tree.py`（11 用例） | 改写 |
| T14.13 | 迁移 `test_behavior_log.py`（5 用例） | 改写 |
| T14.14 | 迁移 `test_degraded_monitor.py`（6 用例） | 改写 |
| T14.15 | 迁移 `test_etw_admin.py`（8 用例） | 改写 |
| T14.16 | 迁移 `test_cleanup.py`（2 用例） | 改写 |
| T14.17 | 迁移 `test_permission_matrix.py`（2 用例） | 改写 |
| T14.18 | 迁移 `test_oj_scenario.py`（4 用例） | 改写 |
| T14.19 | 迁移 `test_scenario_c_sample.py`（1 用例） | 改写 |
| T14.20 | 迁移 `test_scenario_d_ci.py`（1 用例） | 改写 |
| T14.21 | 迁移 `test_global_quota.py`（5 用例） | 改写 |
| T14.22 | 迁移 `test_silo.py`（4 用例） | 改写 |
| T14.23 | 归档 `blackbox_phase8/` / `blackbox_phase9/` → `tests/e2e/archive/` | 移动 |
| T14.24 | 更新 `run_all_regression.py` | 改写 |
| T14.25 | 全量回归 | 25 套全绿 |

---

## 5. 验收标准

### 5.1 全量回归

```powershell
python tests/e2e/run_all_regression.py
# 预期：25 套全绿（19 迁移 + 6 native）
# 排除 test_etw_admin.py（需管理员）
```

### 5.2 用例数对齐

迁移前后用例数一致（除明确删除的 IPC 专属用例）：

| 套件 | 原用例 | 新用例 | 备注 |
|------|--------|--------|------|
| 19 套迁移套件 | 99 | 99 | 一一对应 |
| 6 套 native（Phase 11/13） | — | ~15 | 新增 |
| 4 套 IPC 专属（Phase 12 删） | 18 | 0 | 删除 |
| **合计** | 117 | ~114 | -18 删 +15 新 |

### 5.3 隔离语义验证

迁移后测试仍验证相同隔离语义：
- AppContainer 默认拒绝文件/注册表/网络
- 文件系统 4 种模式
- 网络 5 种策略
- Job 资源限制（CPU/内存/进程数/超时）
- 进程树事件
- ETW 行为监控

---

## 6. 风险与处置

| # | 风险 | 处置 |
|---|---|---|
| 1 | 迁移后用例行为偏差（时序/句柄/回调） | 每套件迁移后立即对比原用例断言；保留原断言语义 |
| 2 | 回调时序与 IPC 事件流不同 | IPC 事件流有序到达；回调可能并发。测试用列表收集 + 排序断言 |
| 3 | `read_pipe` 阻塞（子进程不退出） | 设 timeout + 超时 fail；或用 `drain_async` 后台读 |
| 4 | wall_clock 精度差异（Python Timer vs C++ 线程） | 容忍 ±100ms 误差；断言用 reason 而非精确时间 |
| 5 | ETW 管理员测试在非管理员环境跳过 | 保留 `@pytest.mark.skipif(not is_admin)` |
| 6 | blackbox 脚本归档后丢失覆盖 | 归档不删；新形态黑盒测试待后续 |

---

## 7. 测试策略

本 Phase 本身是测试迁移，策略为：

1. **逐套迁移 + 验证**：每套件迁移后单独运行，全绿再迁移下一套
2. **全量回归**：全部迁移完成后 `run_all_regression.py` 一次全绿
3. **隔离语义对照**：迁移前后断言语义一致，仅改调用形式

---

## 8. 后续衔接

- Phase 15：wheel 打包 + 全量回归 + 文档更新
