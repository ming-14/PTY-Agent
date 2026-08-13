# Phase 11: C++ 核心去 IPC 依赖 + pybind11 绑定层

**Phase 编号**: 11
**Phase 名称**: C++ 核心去 IPC 依赖与 pybind11 绑定层实现
**创建日期**: 2026-08-11
**预计工期**: 4 个工作日
**负责人**: rikka
**状态**: ✅ 已完成
**上游依赖**: Phase 10（已完成，pybind11 构建骨架可用）
**下游影响**: Phase 12（删除 IPC）、Phase 13（ETW 回调）、Phase 14（e2e 迁移）
**完成日期**: 2026-08-11
**验收结果**: 双产物构建通过（sandbox.exe + win_sandbox_native.pyd）+ 7 冒烟测试通过 + IPC e2e 关键测试通过（smoke + test_resource_quota 6/6）

---

## 1. Phase 目标

### 1.1 总体目标

Phase 10 已搭好 pybind11 构建骨架（空模块可 import）。本 Phase 实现**核心业务绑定**，使 Python 能通过 `win_sandbox_native` 直接启动隔离进程、拿到句柄、等待退出、收 Job 通知回调。

具体目标：

1. **改造 `SandboxInstance`**：去掉 `IEventEmitter*` 构造依赖，改为回调函数注入（`std::function`）
2. **改造 `StartProcessUseCase`**：拆分生命周期——保留隔离准备（AppContainer + EnforcePolicy）+ Launch + AssignProcess + IOCP 通知处理；**删除** C++ 端 StreamReader、wait 线程、wall_clock 线程（Python 自己做）
3. **新增 pybind11 绑定层**（`src/bindings/`）：
   - `SandboxInstance` 包装（构造、`start_process`、`shutdown`、`capabilities`、`list_processes`）
   - `Process` 包装（句柄属性、`wait`/`terminate`/`signal`/`query_*`/`close`、回调 setter）
   - 配置/枚举转换（`SandboxConfig` / `ResourceQuota` / `IsolationPolicy` ↔ Python dict）
   - 回调桥接（`std::function` ↔ Python callable，GIL 管理）
4. **句柄传递**：Launch 后 stdin/stdout/stderr 句柄所有权转 Python（HANDLE 值直接共享，in-process 无需 DuplicateHandle）
5. **GIL 管理**：IOCP 线程回调 Python 时持 GIL；长时间 C++ 操作释放 GIL

**本 Phase 完成后，Python 可用 pybind11 直调完成「启动隔离进程 + 读写句柄 + 等退出 + 收 Job 通知」全流程**。IPC 形态（sandbox.exe）仍保留可用（Phase 12 才删除）。

### 1.2 非目标

- 不删除 IPC 代码（main.cpp / infra/ipc/* 保留，Phase 12 删）
- 不迁移 e2e 测试（Phase 14）
- 不实现 ETW 回调（Phase 13）
- 不实现 `contains_access_denied_keyword` 工具函数（Phase 13）
- 不实现 Python 端 helpers（WallClockTimer / StatsPoller / read_pipe / write_pipe，Phase 13）
- 不构建 wheel（Phase 15）
- 不改 StatsCollector（本 Phase 暂保留 C++ 端实现，但 pybind11 不暴露周期上报；Phase 13 起 Python 端轮询替代）

---

## 2. 功能需求

### 2.1 功能需求清单

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-11.1 | `SandboxInstance` 去 `IEventEmitter` 依赖 | P0 | 构造不再要求 `IEventEmitter*`；回调通过 `std::function` 注入 |
| FR-11.2 | `StartProcessUseCase` 拆分：删 StreamReader/wait/wall_clock 线程 | P0 | Launch 后直接返回句柄，不启动 C++ 端读线程/等线程/定时器 |
| FR-11.3 | pybind11 `SandboxInstance` 包装 | P0 | `sb = win_sandbox_native.SandboxInstance(config=...)` 可创建；`sb.capabilities` 返回 CapabilityReport |
| FR-11.4 | pybind11 `Process` 包装 + 句柄属性 | P0 | `proc = sb.start_process(...)` 返回 Process 对象；`proc.stdin_handle/stdout_handle/stderr_handle/process_handle` 为 int |
| FR-11.5 | `proc.wait()` 等退出 | P0 | 返回 `(exit_code, exit_reason, resource_usage)` 三元组 |
| FR-11.6 | `proc.terminate` / `proc.signal` / `proc.close_stdin` | P0 | 三个方法行为与现有 IPC 一致 |
| FR-11.7 | `proc.query_accounting` / `query_peak_memory` / `query_process_list` / `query_process_exit_code` | P0 | 四个查询方法返回正确结果 |
| FR-11.8 | Job 通知回调（`on_resource_limit` / `on_job_process_started` / `on_job_process_exited`） | P0 | IOCP 线程持 GIL 调 Python 回调，无死锁 |
| FR-11.9 | `sb.shutdown` / `sb.list_processes` | P0 | 沙箱级管理方法可用 |
| FR-11.10 | GIL 管理：长时间操作释放 GIL | P0 | `start_process` / `wait` / `terminate` 期间其他 Python 线程可运行 |
| FR-11.11 | sandbox.exe 仍可构建运行 | P0 | 现有 e2e 全量通过（IPC 形态未破坏） |

### 2.2 目标 API（Python 侧）

```python
import win_sandbox_native

# 创建沙箱实例
sb = win_sandbox_native.SandboxInstance(
    config: dict | str | None = None,  # dict / JSON 路径 / None(默认)
    log_level: str = "info",
)

# 属性
sb.capabilities  # dict（CapabilityReport 序列化）

# 启动进程
proc = sb.start_process(
    command_line: str,
    working_dir: str | None = None,
    env_vars: dict[str, str] | None = None,
    inherit_env: bool = True,
    quota: dict | None = None,          # ResourceQuota 字段
    isolation_policy: dict | None = None,  # IsolationPolicy 字段
    interactive: bool = False,
    stream_buffer_size: int = 0,        # 保留字段（Python 自己读，但 Launch 仍创建管道）
) -> win_sandbox_native.Process

# Process 对象
proc.process_id: int
proc.pid: int
proc.process_handle: int          # HANDLE（Python WaitForSingleObject）
proc.stdin_handle: int | None     # HANDLE（Python WriteFile）；None if not interactive
proc.stdout_handle: int           # HANDLE（Python ReadFile）
proc.stderr_handle: int           # HANDLE（Python ReadFile）

# 回调（setter，Python callable）
proc.on_resource_limit = lambda info: ...
proc.on_job_process_started = lambda info: ...
proc.on_job_process_exited = lambda info: ...

# 方法
proc.wait(timeout_ms: int = -1) -> tuple[int, str, dict]  # (exit_code, exit_reason, resource_usage)
proc.terminate(exit_code: int = 1) -> None
proc.signal(sig: str = "ctrl_break") -> None   # "ctrl_break" | "kill"
proc.close_stdin() -> None
proc.query_accounting() -> dict
proc.query_peak_memory() -> int
proc.query_process_list() -> list[int]
proc.query_process_exit_code(pid: int) -> int
proc.close() -> None             # 释放 C++ 端资源（Job/AppContainer/句柄）

# 沙箱级
sb.list_processes() -> list[dict]
sb.shutdown() -> None
```

### 2.3 回调 payload schema

#### 2.3.1 `on_resource_limit`（Job 资源限制命中）

```python
{
    "type": "cpu_limit" | "memory_limit" | "process_count_limit" | "cpu_timeout",
    "pid": 1234,
    "timestamp_ms": 1722112345678,
}
```

#### 2.3.2 `on_job_process_started`（Job 内子/孙进程创建）

```python
{
    "pid": 5678,
    "process_name": "cl.exe",
    "process_path": "C:\\...\\cl.exe",
    "parent_pid": 1234,  # best-effort，可能省略
    "timestamp_ms": 1722112345678,
}
```

#### 2.3.3 `on_job_process_exited`（Job 内子/孙进程退出）

```python
{
    "pid": 5678,
    "exit_kind": "normal" | "abnormal" | "unknown",
    "exit_code": 0,  # exit_kind="unknown" 时省略
    "timestamp_ms": 1722112345678,
}
```

---

## 3. 技术设计

### 3.1 C++ 核心改造

#### 3.1.1 `SandboxInstance` 改造（`src/adapters/SandboxInstance.hpp/.cpp`）

**去掉** `IEventEmitter*` 构造参数。改为回调注入：

```cpp
// 回调类型定义（新增头文件 src/adapters/Callbacks.hpp）
struct ResourceLimitInfo {
    std::string type;       // "cpu_limit" / "memory_limit" / ...
    uint32_t pid;
    uint64_t timestamp_ms;
};
struct JobProcessStartedInfo {
    uint32_t pid;
    std::string process_name;
    std::string process_path;
    std::optional<uint32_t> parent_pid;
    uint64_t timestamp_ms;
};
struct JobProcessExitedInfo {
    uint32_t pid;
    std::string exit_kind;  // "normal" / "abnormal" / "unknown"
    std::optional<int32_t> exit_code;
    uint64_t timestamp_ms;
};

// SandboxInstance 构造改为
SandboxInstance(std::shared_ptr<ILogger> logger,
                uint32_t stats_interval_ms = 0,
                ISilo* silo = nullptr,
                IGlobalQuotaManager* global_quota = nullptr);
```

回调不在 `SandboxInstance` 层，而在 `Process`（per-process）层。`SandboxInstance::StartProcess` 返回 `ProcessEntry` 裸指针（或 `std::shared_ptr<ProcessEntry>`），由 pybind11 `Process` 包装层注入回调。

#### 3.1.2 `StartProcessUseCase` 拆分（`src/core/usecases/StartProcessUseCase.hpp/.cpp`）

**保留**：
- `PrepareAppContainer`（AppContainer + EnforcePolicy）
- `Execute` 前半段：构建 LaunchRequest → Launch → AssignProcess → 发 ProcessStarted 回调
- `OnNotification`（IOCP 通知翻译为回调）
- `Terminate` / `SignalProcess` / `WriteStdin` / `CloseStdinWrite`
- `CloseProcessHandle`

**删除**：
- `StreamReader` 启动（`stdout_reader_` / `stderr_reader_`）
- `wait_thread_`（WaitLoop）
- `wall_clock_thread_`（StartWallClockTimer / StopWallClockTimer）
- `IProcessOutputSink` 继承（不再有 C++ 端流读取）
- `EmitProcessOutput` / `EmitAccessDenied`（stderr 扫描移到 Python）

**改造**：
- `Execute` 返回 `LaunchResult`（含句柄）而非仅 `SandboxedProcess`，句柄传给 Python
- 析构改为显式 `Close()` 方法（Python `proc.close()` 触发），析构兜底调 `Close()`
- `OnNotification` 中 `TerminateAllOnLimit` 保留（C++ 端必须杀进程），但事件改为调回调而非 `IEventEmitter`

新增接口：

```cpp
// 改造后的 Execute（返回句柄）
struct ExecuteResult {
    SandboxedProcess process;
    void* process_handle;    // 所有权转调用方（Python）
    void* stdin_write;       // 所有权转调用方（interactive=true 时；否则 nullptr）
    void* stdout_read;       // 所有权转调用方
    void* stderr_read;       // 所有权转调用方
};
Result<ExecuteResult> Execute(const StartProcessRequest& req);

// 显式清理（Python proc.close() 调用）
void Close();

// 回调注入（pybind11 层设置）
std::function<void(const ResourceLimitInfo&)> on_resource_limit;
std::function<void(const JobProcessStartedInfo&)> on_job_process_started;
std::function<void(const JobProcessExitedInfo&)> on_job_process_exited;
```

#### 3.1.3 句柄所有权约定

| 句柄 | 所有权 | 清理时机 |
|------|--------|----------|
| `process_handle` | **共享**：C++ 端 `StartProcessUseCase` 持有（IOCP 查询用）+ Python 持有（wait 用） | `proc.close()` 时 C++ 端 CloseHandle；Python 端自己 CloseHandle |
| `stdin_write` | **Python 拥有**（interactive=true） | Python `proc.close_stdin()` 或 `proc.close()` |
| `stdout_read` | **Python 拥有** | Python `proc.close()` |
| `stderr_read` | **Python 拥有** | Python `proc.close()` |
| `thread_handle` | C++ 端立即 CloseHandle（现有行为） | Execute 内 |

> **关键**：in-process 形态下 HANDLE 值在 Python 解释器进程内直接有效，无需 DuplicateHandle。Python 拿到 int 值后 `ctypes.windll.kernel32.ReadFile(handle, ...)` 直接操作。

### 3.2 pybind11 绑定层（`src/bindings/`）

#### 3.2.1 `module.cpp` — 模块入口

```cpp
PYBIND11_MODULE(win_sandbox_native, m) {
    m.doc() = "win-sandbox native extension";
    bindings::RegisterConfig(m);       // 配置/枚举
    bindings::RegisterSandboxInstance(m);  // SandboxInstance
    bindings::RegisterProcess(m);      // Process
    bindings::RegisterCallbacks(m);    // 回调类型
}
```

#### 3.2.2 `ConfigBinding.cpp` — 配置转换

`py::dict` ↔ `SandboxConfig` / `ResourceQuota` / `IsolationPolicy` / `FileSystemConfig` / `NetworkRule` 转换。枚举（`FileSystemMode` / `NetworkPolicy` / `ProcessSignal` / `ExitReason`）绑定为 Python str。

#### 3.2.3 `SandboxInstanceBinding.cpp` — SandboxInstance 包装

```cpp
class PySandboxInstance {
    std::shared_ptr<winsandbox::SandboxInstance> instance_;
    winsandbox::CapabilityReport capabilities_;
public:
    PySandboxInstance(py::object config, std::string log_level);
    py::dict capabilities() const;
    py::object start_process(/* 参数见 2.2 */);
    std::vector<py::dict> list_processes() const;
    void shutdown();
};
```

`start_process` 内部：
1. `py::dict` → `StartProcessRequest`（复用 `StartProcessPayloadParser` 逻辑）
2. `py::gil_scoped_release` 释放 GIL
3. `instance_->StartProcess(req)` → 拿到 `ProcessEntry`
4. `py::gil_scoped_acquire` 重获 GIL
5. 构造 `PyProcess` 包装 `ProcessEntry`，返回

#### 3.2.4 `ProcessBinding.cpp` — Process 包装

```cpp
class PyProcess {
    std::shared_ptr<winsandbox::StartProcessUseCase> usecase_;
    winsandbox::ExecuteResult exec_result_;
    // 回调（Python callable，py::function 持有引用）
    py::function on_resource_limit_;
    py::function on_job_process_started_;
    py::function on_job_process_exited_;
public:
    // 句柄属性
    int process_id() const;
    int pid() const;
    int process_handle() const;     // 返回 HANDLE 值
    py::object stdin_handle() const;  // int 或 None
    int stdout_handle() const;
    int stderr_handle() const;

    // 回调 setter
    void set_on_resource_limit(py::function f);
    // ...

    // 方法
    py::tuple wait(int64_t timeout_ms);  // (exit_code, exit_reason, resource_usage)
    void terminate(uint32_t exit_code);
    void signal(std::string sig);
    void close_stdin();
    py::dict query_accounting();
    uint64_t query_peak_memory();
    std::vector<uint32_t> query_process_list();
    uint32_t query_process_exit_code(uint32_t pid);
    void close();
};
```

#### 3.2.5 `CallbacksBinding.cpp` — 回调桥接与 GIL 管理

**核心难点**：IOCP 线程在 C++ 端跑，回调 Python 时需持 GIL。

```cpp
// IOCP 线程调 usecase 的 on_resource_limit（std::function）
// pybind11 层注入的 std::function 实现：
usecase->on_resource_limit = [py_cb = this->on_resource_limit_](const ResourceLimitInfo& info) {
    py::gil_scoped_acquire gil;  // 持 GIL
    py_cb(info_to_dict(info));   // 调 Python 回调
};
```

**回调契约**（文档化，防死锁）：
- 回调内 Python **禁止**调 C++ 方法（如 `proc.terminate`）
- 回调内只做：记录日志、设标志位、入队列
- 实际终止由 C++ 端 `TerminateAllOnLimit` 已完成（回调只是通知）
- Python 需要终止时在回调外（主线程）调 `proc.terminate`

### 3.3 GIL 管理策略

| 操作 | GIL | 原因 |
|------|-----|------|
| `start_process`（含 Launch/CreateProcess） | 释放 | 耗时 10-100ms，让其他 Python 线程跑 |
| `wait`（WaitForSingleObject） | 释放 | 长时间阻塞 |
| `terminate`（TerminateAll） | 释放 | 耗时 |
| `query_*` | 释放 | 可能涉及系统调用 |
| IOCP 回调 Python | 获取 | 调 Python callable 必须 GIL |
| ETW 回调 Python | 获取 | 同上 |
| 构造/属性访问 | 持有 | 快速操作 |

**死锁防护**：
- IOCP 线程持 GIL 调 Python 回调 → 回调内不调 C++（不释放/重获 GIL）
- Python 主线程调 `proc.wait` 释放 GIL → IOCP 线程可获 GIL 调回调 → 无环
- `proc.close()` 需 join IOCP 线程 → 必须在持 GIL 时调（Python 主线程），IOCP 线程此时在等 GIL → close 先 stop IOCP（不持 GIL）→ join → 安全

### 3.4 架构决策：新增 Native 类（方案 B）

**决策**：不改造现有 `SandboxInstance`/`StartProcessUseCase`（避免双形态兼容违反
AGENTS.md「避免兼容方案」规则），而是新增 pybind11 专用实现：

- `NativeSandboxedProcess`（`src/core/usecases/`）：去 IPC 的进程用例，复用
  `StartProcessUseCase` 的隔离准备 + Launch + Assign + IOCP 通知逻辑，删除
  StreamReader / wait 线程 / wall_clock 线程 / IEventEmitter / IProcessOutputSink
- `NativeSandboxInstance`（`src/adapters/`）：pybind11 专用多进程管理器，用
  `NativeSandboxedProcess` 替代 `StartProcessUseCase`，无 IEventEmitter / StatsCollector

**过渡期代价**：`PrepareAppContainer` / `OnNotification` / `TerminateAllOnLimit` 等逻辑
在 `NativeSandboxedProcess` 和 `StartProcessUseCase` 间重复。Phase 12 删除
`StartProcessUseCase` / `SandboxInstance` 后重复消失。

**优势**：
- 新代码从一开始就干净无 IPC 耦葛（无兼容包袱）
- 不动现有 IPC 代码，现有 e2e 全量通过（IPC 形态未破坏）
- Phase 12 一次性删旧，无需清理条件分支

**文件清单**（新增）：
- `src/core/entities/Callbacks.hpp` — 回调 payload 类型
- `src/core/usecases/NativeSandboxedProcess.hpp/.cpp` — 去 IPC 进程用例
- `src/adapters/NativeSandboxInstance.hpp/.cpp` — pybind11 多进程管理器
- `src/bindings/BindingCommon.hpp` — 共享辅助（json 转换、Result 解包、dict 转换）
- `src/bindings/ConfigBinding.hpp` — 配置转换（header-only）
- `src/bindings/ProcessBinding.hpp/.cpp` — PyProcess 包装 + 回调桥接
- `src/bindings/SandboxInstanceBinding.hpp/.cpp` — PySandboxInstance 包装
- `src/bindings/CallbacksBinding.hpp` — 回调绑定占位（Phase 13 扩展）
- `src/bindings/module.cpp` — 注册入口（更新）
- `src/CMakeLists.txt` — win_sandbox_native 加 native 专属源文件（更新）
- `tests/e2e/test_native_smoke.py` — 冒烟测试

---

## 4. 任务拆分

| 任务 | 描述 | 产出 |
|------|------|------|
| T11.1 | 新增 `src/adapters/Callbacks.hpp`（回调类型定义） | 回调结构体 |
| T11.2 | 改造 `SandboxInstance`：新增无 `IEventEmitter` 构造重载 | `.hpp/.cpp` 改动 |
| T11.3 | 改造 `StartProcessUseCase`：拆分 Execute，删 StreamReader/wait/wall_clock | `.hpp/.cpp` 改动 |
| T11.4 | `StartProcessUseCase::OnNotification` 改回调调用 | `.cpp` 改动 |
| T11.5 | 新增 `src/bindings/ConfigBinding.cpp`（配置转换） | 绑定代码 |
| T11.6 | 新增 `src/bindings/SandboxInstanceBinding.cpp` | 绑定代码 |
| T11.7 | 新增 `src/bindings/ProcessBinding.cpp` | 绑定代码 |
| T11.8 | 新增 `src/bindings/CallbacksBinding.cpp`（GIL 管理） | 绑定代码 |
| T11.9 | 更新 `src/bindings/module.cpp` 注册所有绑定 | `.cpp` 改动 |
| T11.10 | 更新 `src/CMakeLists.txt`：win_sandbox_native 含 bindings 源文件 | CMake 改动 |
| T11.11 | 编写冒烟测试 `tests/e2e/test_native_smoke.py` | Python 测试 |
| T11.12 | 验证：构建 + import + 冒烟 + 现有 e2e 回归 | 全绿 |

---

## 5. 验收标准

### 5.1 构建验收

```powershell
cmake --build build
# 产出 win_sandbox_native.pyd + sandbox.exe
```

### 5.2 pybind11 冒烟验收

`tests/e2e/test_native_smoke.py`：

```python
import sys, ctypes
sys.path.insert(0, r"build/bin")
import win_sandbox_native

sb = win_sandbox_native.SandboxInstance(config=None, log_level="info")
print("capabilities:", sb.capabilities)

proc = sb.start_process(
    command_line="cmd.exe /c echo hello from native",
    quota={"memory_mb": 256, "wall_clock_timeout_ms": 10000},
)
print("pid:", proc.pid, "stdout_handle:", proc.stdout_handle)

# Python 自己读 stdout
kernel32 = ctypes.windll.kernel32
buf = ctypes.create_string_buffer(65536)
read = ctypes.c_ulong()
kernel32.ReadFile(proc.stdout_handle, buf, 65536, ctypes.byref(read), None)
print("stdout:", buf.raw[:read.value])

exit_code, reason, usage = proc.wait(timeout_ms=10000)
print(f"exit: {exit_code}, reason: {reason}")
proc.close()
sb.shutdown()
```

预期输出 `hello from native`，exit_code=0。

### 5.3 回调验收

`tests/e2e/test_native_callback.py`：启动 CPU 密集进程 + `cpu_ms=500`，验证 `on_resource_limit` 回调被触发。

### 5.4 回归验收

现有 23 套件 e2e + ctest 14 项全量通过（sandbox.exe / IPC 形态未破坏）。

---

## 6. 风险与处置

| # | 风险 | 处置 |
|---|---|---|
| 1 | **GIL 死锁**：IOCP 线程持 GIL 回调，Python 回调内调 C++ | 回调契约文档化（3.2.5）；回调内只读 info + 入队列；C++ 端 TerminateAll 已在回调前完成 |
| 2 | **句柄生命周期**：Python 持 HANDLE，C++ 析构提前 CloseHandle | 所有权明确（3.1.3）；`proc.close()` 显式清理；Python `__del__` 兜底调 close |
| 3 | **IOCP 线程与解释器关闭**：Python 退出时 IOCP 线程仍跑 | `PyProcess.__del__` 调 close → join IOCP；pybind11 注册 atexit |
| 4 | **SandboxInstance 双形态兼容**：IPC + pybind11 共存 | 构造重载区分；Phase 12 删 IPC 时移除 |
| 5 | **StartProcessUseCase 拆分破坏现有 e2e** | 拆分时保留 IPC 形态的 Execute 路径（内部条件分支）；Phase 12 删 IPC 时清理 |
| 6 | **pybind11 py::function 生命周期**：回调对象被 GC | `PyProcess` 持 `py::function` 成员，引用计数保持；`proc.close()` 后清空 |

---

## 7. 测试策略

### 7.1 新增测试

- `tests/e2e/test_native_smoke.py` — 基础冒烟（启动 + 读 stdout + wait）
- `tests/e2e/test_native_callback.py` — Job 通知回调（CPU 超限触发 on_resource_limit）
- `tests/e2e/test_native_handle.py` — 句柄读写（Python ReadFile/WriteFile 原始字节）
- `tests/e2e/test_native_isolation.py` — 隔离语义（AppContainer + 文件系统，复用现有测试逻辑）

### 7.2 回归

现有 23 套件 e2e 全量通过（验证 IPC 形态未破坏）。

---

## 8. 后续衔接

- Phase 12：删除 IPC 代码（main.cpp / infra/ipc/* / StreamReader / StatsCollector / Python client.py 等），清理 `SandboxInstance` 双形态
- Phase 13：ETW 回调绑定 + `contains_access_denied_keyword` 工具函数 + Python helpers
- Phase 14：23 套件 e2e 全部迁移到 pybind11 直调
