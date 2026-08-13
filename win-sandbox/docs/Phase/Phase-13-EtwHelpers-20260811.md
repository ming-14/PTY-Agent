# Phase 13: ETW 回调 + 工具函数 + Python helpers

**Phase 编号**: 13
**Phase 名称**: ETW 行为监控回调、工具函数与 Python 端 helpers
**创建日期**: 2026-08-11
**预计工期**: 2 个工作日
**负责人**: rikka
**状态**: ✅ 已完成
**上游依赖**: Phase 12（已完成，IPC 已删除，代码库唯一 pybind11 形态）
**下游影响**: Phase 14（e2e 迁移需 helpers）、Phase 15（文档）

---

## 1. Phase 目标

### 1.1 总体目标

Phase 12 已删除 IPC，代码库唯一 pybind11 形态。本 Phase 补全三块能力：

1. **ETW 行为监控回调**：`EtwMonitorImpl` 保留 C++ 端（管理员内核 session + 降级轮询），事件通过 pybind11 回调推 Python（`on_behavior_event` / `on_access_denied`）
2. **工具函数**：`contains_access_denied_keyword(data: bytes) -> bool`（复用现有 stderr 扫描逻辑，Python 读到字节后调用判断）
3. **Python 端 helpers**：补充 C++ 库不做的事（wall_clock 定时器、stats 轮询、句柄读写封装、管道 drain）

### 1.2 非目标

- 不迁移 e2e 测试（Phase 14）
- 不构建 wheel（Phase 15）
- 不改 ETW C++ 实现逻辑（`EtwMonitorImpl` / `EventRecordParser` 不动，仅加回调出口）
- 不实现 ConPTY（独立方案，见 `docs/design/SandboxConPty-Terminal-Enhancement-20260811.md`）

---

## 2. 功能需求

### 2.1 功能需求清单

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-13.1 | ETW 行为事件回调 `on_behavior_event` | P0 | 管理员模式下 ETW 事件实时推 Python 回调 |
| FR-13.2 | ETW AccessDenied 回调 `on_access_denied` | P0 | ETW 检测到 STATUS_ACCESS_DENIED 时触发 |
| FR-13.3 | 降级模式（普通用户）行为监控回调 | P1 | 进程/文件/网络轮询事件推 Python |
| FR-13.4 | `contains_access_denied_keyword(data: bytes) -> bool` 工具函数 | P0 | 复用现有 stderr 扫描逻辑，Python 可调 |
| FR-13.5 | Python `helpers.read_pipe(handle, size) -> bytes` | P0 | ctypes ReadFile 封装 |
| FR-13.6 | Python `helpers.write_pipe(handle, data) -> int` | P0 | ctypes WriteFile 封装 |
| FR-13.7 | Python `helpers.wait_process(handle, timeout_ms) -> int` | P0 | ctypes WaitForSingleObject 封装 |
| FR-13.8 | Python `helpers.WallClockTimer` | P0 | threading.Timer 超时调 `proc.terminate` |
| FR-13.9 | Python `helpers.StatsPoller` | P1 | threading.Thread 周期调 `proc.query_accounting` |
| FR-13.10 | Python `helpers.drain_stdout` / `drain_stderr` | P1 | 后台线程读管道 + 回调 |
| FR-13.11 | ETW 配置（`monitoring` 段）通过 `SandboxInstance` config 启用 | P0 | `config={"monitoring": {"etw_enabled": True, ...}}` 生效 |

### 2.2 ETW 回调 payload schema

#### 2.2.1 `on_behavior_event`（ETW 行为事件）

```python
{
    "event_type": "file_access" | "registry_access" | "process_start" | "process_stop" | "tcp_connect" | "udp_send",
    "pid": 1234,
    "path": "C:\\...\\file.txt",       # 事件相关路径
    "operation": "read" | "write" | "create" | "delete",
    "status": "success" | "access_denied",
    "timestamp_ms": 1722112345678,
    "source": "etw" | "degraded",      # ETW 内核 vs 降级轮询
}
```

#### 2.2.2 `on_access_denied`（AccessDenied 专项）

```python
{
    "pid": 1234,
    "path": "C:\\...\\file.txt",
    "operation": "file_access" | "registry_access",
    "source": "etw" | "stderr",        # ETW vs stderr 关键字扫描
    "timestamp_ms": 1722112345678,
}
```

### 2.3 Python helpers API

```python
# python/win_sandbox/helpers.py

# 句柄读写（ctypes 封装，零依赖）
def read_pipe(handle: int, size: int = 65536, timeout_ms: int = -1) -> bytes:
    """ReadFile 匿名管道。返回读取的字节。EOF 时返回 b''。"""

def write_pipe(handle: int, data: bytes, timeout_ms: int = -1) -> int:
    """WriteFile 匿名管道。返回写入字节数。"""

def wait_process(handle: int, timeout_ms: int = -1) -> int:
    """WaitForSingleObject 进程句柄。返回退出码。"""

def close_handle(handle: int) -> None:
    """CloseHandle 封装。"""

# 后台定时器
class WallClockTimer:
    """超时调 proc.terminate。threading.Timer 实现。"""
    def __init__(self, proc, timeout_ms: int, exit_code: int = 1): ...
    def start(self) -> None: ...
    def cancel(self) -> None: ...

class StatsPoller:
    """周期调 proc.query_accounting + 回调。threading.Thread 实现。"""
    def __init__(self, proc, interval_ms: int, callback): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...

# 管道 drain（后台线程读管道）
def drain_stdout(proc, callback, buffer_size: int = 65536) -> threading.Thread:
    """后台线程循环 read_pipe(proc.stdout_handle) → callback(data)。EOF 退出。"""

def drain_stderr(proc, callback, buffer_size: int = 65536) -> threading.Thread:
    """同上，stderr。可内置 contains_access_denied_keyword 扫描。"""
```

---

## 3. 技术设计

### 3.1 ETW 回调绑定

#### 3.1.1 `EtwMonitorImpl` 回调出口

`EtwMonitorImpl` 现有通过 `IEventEmitter` 发 `BehaviorLog` / `AccessDenied` 事件（IPC 形态）。改造为 `std::function` 回调：

```cpp
// src/infra/etw/EtwMonitorImpl.hpp 新增
struct BehaviorEventInfo {
    std::string event_type;
    uint32_t pid;
    std::string path;
    std::string operation;
    std::string status;  // "success" / "access_denied"
    uint64_t timestamp_ms;
    std::string source;  // "etw" / "degraded"
};

std::function<void(const BehaviorEventInfo&)> on_behavior_event;
std::function<void(const BehaviorEventInfo&)> on_access_denied;
```

`EtwMonitorImpl` 内部事件线程触发回调时，pybind11 层注入的 `std::function` 持 GIL 调 Python（与 IOCP 回调同理，见 Phase 11 §3.2.5）。

#### 3.1.2 `Process` 包装层暴露回调 setter

```cpp
// src/bindings/ProcessBinding.cpp
py_process.def_property("on_behavior_event", &PyProcess::get_on_behavior_event, &PyProcess::set_on_behavior_event);
py_process.def_property("on_access_denied", ...);
```

#### 3.1.3 ETW 启用

`SandboxInstance` 构造时根据 `config.monitoring.etw_enabled` 决定是否创建 `EtwMonitorImpl`。`start_process` 时将 `EtwMonitorImpl`（若存在）注入 `StartProcessUseCase`。

### 3.2 `contains_access_denied_keyword` 工具函数

现有 `StartProcessUseCase::ContainsAccessDeniedKeyword`（静态方法）扫描 stderr 关键字。Phase 12 已删除该方法（随 `StartProcessUseCase` IPC 清理）。本 Phase 在 pybind11 层重新暴露：

```cpp
// src/bindings/CallbacksBinding.cpp
m.def("contains_access_denied_keyword", [](py::bytes data) -> bool {
    std::string_view sv(data);
    // 复用原逻辑：扫描 "拒绝访问" / "Access is denied"（大小写不敏感）
    return winsandbox::ContainsAccessDeniedKeywordImpl(sv);
});
```

`ContainsAccessDeniedKeywordImpl` 提取为独立工具函数（`src/adapters/StringUtils.hpp` 或内联在 bindings），不依赖 `StartProcessUseCase`。

### 3.3 Python helpers 实现

`python/win_sandbox/helpers.py` 纯 ctypes，零依赖：

```python
import ctypes
import threading
import time
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ReadFile / WriteFile / WaitForSingleObject / CloseHandle 绑定
# （复用现有 client.py 的 ctypes 绑定模式，Phase 12 已删 client.py，此处重新实现）

def read_pipe(handle: int, size: int = 65536, timeout_ms: int = -1) -> bytes:
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_ulong()
    success = _kernel32.ReadFile(handle, buf, size, ctypes.byref(read), None)
    if not success:
        err = ctypes.get_last_error()
        if err == 109:  # ERROR_BROKEN_PIPE (EOF)
            return b""
        raise OSError(f"ReadFile failed: err={err}")
    return buf.raw[:read.value]

# ... write_pipe / wait_process / close_handle 同理

class WallClockTimer:
    def __init__(self, proc, timeout_ms: int, exit_code: int = 1):
        self._proc = proc
        self._timer = threading.Timer(timeout_ms / 1000, self._fire)
        self._exit_code = exit_code
        self._fired = False

    def _fire(self):
        self._fired = True
        self._proc.terminate(self._exit_code)

    def start(self): self._timer.start()
    def cancel(self): self._timer.cancel()
    @property
    def fired(self): return self._fired

class StatsPoller:
    def __init__(self, proc, interval_ms: int, callback):
        self._proc = proc
        self._interval = interval_ms / 1000
        self._cb = callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            try:
                stats = self._proc.query_accounting()
                self._cb(stats)
            except Exception:
                pass
            self._stop.wait(self._interval)

    def start(self): self._thread.start()
    def stop(self): self._stop.set(); self._thread.join(timeout=5)

def drain_stdout(proc, callback, buffer_size: int = 65536):
    def _loop():
        while True:
            data = read_pipe(proc.stdout_handle, buffer_size)
            if not data:
                break
            callback(data)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t

def drain_stderr(proc, callback, buffer_size: int = 65536):
    import win_sandbox_native
    def _loop():
        while True:
            data = read_pipe(proc.stderr_handle, buffer_size)
            if not data:
                break
            callback(data)
            # 内置 AccessDenied 扫描
            if win_sandbox_native.contains_access_denied_keyword(data):
                proc._on_access_denied_stderr(data)  # 若设置了回调
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
```

---

## 4. 任务拆分

| 任务 | 描述 | 产出 |
|------|------|------|
| T13.1 | `EtwMonitorImpl` 加 `std::function` 回调出口 | `.hpp/.cpp` 改动 |
| T13.2 | `Process` 包装层暴露 `on_behavior_event` / `on_access_denied` setter | bindings 改动 |
| T13.3 | `SandboxInstance` 根据 config 启用 ETW + 注入 usecase | `.hpp/.cpp` 改动 |
| T13.4 | 提取 `ContainsAccessDeniedKeyword` 为独立工具函数 | 新增 `StringUtils.hpp` 或内联 |
| T13.5 | pybind11 绑定 `contains_access_denied_keyword` | bindings 改动 |
| T13.6 | 新增 `python/win_sandbox/helpers.py` | Python 文件 |
| T13.7 | 更新 `python/win_sandbox/__init__.py` 导出 helpers | Python 改动 |
| T13.8 | 编写 ETW 回调测试 `test_native_etw.py` | Python 测试 |
| T13.9 | 编写 helpers 测试 `test_helpers.py` | Python 测试 |
| T13.10 | 验证：构建 + 全部 native 测试通过 | 全绿 |

---

## 5. 验收标准

### 5.1 ETW 回调验收（管理员模式）

`tests/e2e/test_native_etw.py`：启用 ETW + 启动进程访问受保护路径，验证 `on_behavior_event` / `on_access_denied` 回调触发。

### 5.2 工具函数验收

```python
import win_sandbox_native
assert win_sandbox_native.contains_access_denied_keyword(b"Access is denied.\r\n")
assert win_sandbox_native.contains_access_denied_keyword(b"\xe6\x8b\x92\xe7\xbb\x9d\xe8\xae\xbf\xe9\x97\xae")  # "拒绝访问"
assert not win_sandbox_native.contains_access_denied_keyword(b"hello world")
```

### 5.3 helpers 验收

`tests/e2e/test_helpers.py`：
- `read_pipe` / `write_pipe` 读写正确
- `WallClockTimer` 超时触发 terminate
- `StatsPoller` 周期回调
- `drain_stdout` / `drain_stderr` 后台读取

### 5.4 回归

Phase 11/12 的 native 测试全绿。

---

## 6. 风险与处置

| # | 风险 | 处置 |
|---|---|---|
| 1 | ETW 内核 session 与 pybind11 GIL 交互 | ETW 事件线程持 GIL 调回调，回调内不调 C++（同 IOCP 契约） |
| 2 | 降级模式（普通用户）轮询线程生命周期 | `proc.close()` 时 stop 轮询线程；daemon=True 兜底 |
| 3 | `contains_access_denied_keyword` 中文编码 | 输入为 bytes，按字节搜索 UTF-8 编码的"拒绝访问"；与原 C++ 逻辑一致 |
| 4 | helpers ctypes 绑定与已删 client.py 重复 | 重新实现，仅保留 helpers 需要的子集；零依赖原则不变 |

---

## 7. 测试策略

### 7.1 新增测试

- `tests/e2e/test_native_etw.py` — ETW 回调（管理员模式；普通用户降级验证）
- `tests/e2e/test_helpers.py` — Python helpers 单元测试

### 7.2 回归

Phase 11/12 native 测试全绿。

---

## 8. 后续衔接

- Phase 14：19 套 e2e 测试迁移到 pybind11 + helpers
- Phase 15：文档更新
