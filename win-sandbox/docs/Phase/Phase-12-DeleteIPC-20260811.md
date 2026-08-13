# Phase 12: 删除旧 IPC 代码 + Python 客户端清理

**Phase 编号**: 12
**Phase 名称**: 删除旧 IPC 代码与 Python 客户端清理
**创建日期**: 2026-08-11
**预计工期**: 2 个工作日
**负责人**: rikka
**状态**: ✅ 已完成
**上游依赖**: Phase 11（已完成，pybind11 绑定层可用，新形态全流程跑通）
**下游影响**: Phase 14（e2e 迁移，需删除 IPC 专属测试）、Phase 15（文档更新）

---

## 1. Phase 目标

### 1.1 总体目标

Phase 11 已实现 pybind11 绑定层，新形态（in-process 直调）全流程跑通，且 IPC 形态（sandbox.exe）仍保留可用。本 Phase **彻底删除 IPC 形态**，使代码库唯一以 pybind11 库形态存在。

具体目标：

1. **删除 C++ IPC 代码**：
   - `src/main.cpp`（IPC 命令分发入口）
   - `src/infra/ipc/*`（`NamedPipeServerImpl` / `FrameCodec`）
   - `src/infra/process/StreamReader.*`（C++ 端流读取，Python 自己读）
   - `src/infra/stats/StatsCollectorImpl.*`（周期统计，Python 端轮询替代）
   - `src/core/ports/IEventEmitter.hpp` / `IProcessOutputSink.hpp` / `IIpcServer.hpp` / `ICommandHandler.hpp`（IPC 相关端口）
2. **清理 `SandboxInstance` / `StartProcessUseCase` 双形态**：移除 Phase 11 为兼容 IPC 保留的构造重载 / 条件分支
3. **删除 Python IPC 客户端**：
   - `python/win_sandbox/client.py`（同步 IPC 客户端）
   - `python/win_sandbox/async_client.py`（异步 IPC 客户端）
   - `python/win_sandbox/protocol.py`（IPC 帧编解码）
   - `python/win_sandbox/exceptions.py` 中 IPC 专属异常
4. **清理 CMake**：删除 `add_executable(sandbox ...)` 目标，`win_sandbox_native` 成为唯一 C++ 目标
5. **清理 IPC 专属测试**：删除 `test_async_client.py` / `test_multi_client.py` / `test_pipe_dacl.py` / `test_fragmentation.py`（这些测试 IPC 机制本身，新形态无对应概念）

**本 Phase 完成后，代码库唯一形态为 `win_sandbox_native.pyd`，无任何 IPC 残留。**

### 1.2 非目标

- 不迁移非 IPC 专属的 e2e 测试（Phase 14）
- 不实现 ETW 回调 / 工具函数 / Python helpers（Phase 13）
- 不更新文档（Phase 15）
- 不构建 wheel（Phase 15）

---

## 2. 功能需求

### 2.1 功能需求清单

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-12.1 | 删除 `src/main.cpp` | P0 | 文件删除；CMake 不再引用 |
| FR-12.2 | 删除 `src/infra/ipc/` 目录 | P0 | `NamedPipeServerImpl.*` / `FrameCodec.*` 删除 |
| FR-12.3 | 删除 `src/infra/process/StreamReader.*` | P0 | 文件删除；`StartProcessUseCase` 无引用 |
| FR-12.4 | 删除 `src/infra/stats/StatsCollectorImpl.*` | P0 | 文件删除；`SandboxInstance` 无引用 |
| FR-12.5 | 删除 IPC 相关端口接口 | P0 | `IEventEmitter` / `IProcessOutputSink` / `IIpcServer` / `ICommandHandler` 删除 |
| FR-12.6 | 清理 `SandboxInstance` IPC 兼容代码 | P0 | 移除 `IEventEmitter*` 构造重载 / IPC 条件分支 |
| FR-12.7 | 清理 `StartProcessUseCase` IPC 兼容代码 | P0 | 移除 `IEventEmitter` 依赖 / `EmitProcessOutput` / `EmitAccessDenied` 等 |
| FR-12.8 | 删除 Python IPC 客户端 | P0 | `client.py` / `async_client.py` / `protocol.py` 删除 |
| FR-12.9 | 清理 `python/win_sandbox/exceptions.py` | P0 | 移除 `ProtocolError` 等 IPC 专属异常 |
| FR-12.10 | CMake 删除 `sandbox` 目标 | P0 | `add_executable(sandbox ...)` 移除；`win_sandbox_native` 唯一目标 |
| FR-12.11 | 删除 IPC 专属 e2e 测试 | P0 | 4 套件删除（见 2.2） |
| FR-12.12 | 删除 `src/adapters/StartProcessPayloadParser` IPC 专属逻辑 | P1 | 保留 dict→Request 转换，删除 IPC JSON 解析路径 |
| FR-12.13 | 删除 `src/core/entities/IpcMessage.hpp` | P1 | IPC 消息实体不再需要（pybind11 直接传 dict） |
| FR-12.14 | 构建 + 现有 pybind11 测试通过 | P0 | Phase 11 新增的 native 测试全绿 |

### 2.2 删除的 e2e 测试套件

| 套件 | 原因 |
|------|------|
| `test_async_client.py` | 测 `AsyncSandboxClient`（IPC 异步客户端），新形态无此对象 |
| `test_multi_client.py` | 测多客户端（controller + observer）IPC 机制，新形态无此概念 |
| `test_pipe_dacl.py` | 测命名管道 DACL 安全，新形态无命名管道 |
| `test_fragmentation.py` | 测 IPC 消息分片（>16MB 自动分片），新形态无 IPC 帧 |

### 2.3 保留但需改造的文件

| 文件 | 改造 |
|------|------|
| `src/adapters/StartProcessPayloadParser.*` | 删除 IPC JSON 解析，保留 `py::dict` → `StartProcessRequest` 转换（移到 bindings 层或保留为工具） |
| `src/core/entities/IpcMessage.hpp` | 删除（pybind11 直接传 dict，无 IpcMessage 实体） |
| `python/win_sandbox/__init__.py` | 改为导出 `win_sandbox_native` + helpers |
| `python/win_sandbox/exceptions.py` | 保留 `SandboxError` / `SandboxTimeoutError` / `SandboxProcessError`，删除 `ProtocolError` |

---

## 3. 技术设计

### 3.1 删除顺序（依赖安全）

按依赖关系逆序删除，避免中间状态编译错误：

```
1. 删除 main.cpp（依赖 infra/ipc/*）
2. 删除 infra/ipc/*（NamedPipeServerImpl / FrameCodec）
3. 删除 infra/process/StreamReader.*（StartProcessUseCase 引用）
4. 删除 infra/stats/StatsCollectorImpl.*（SandboxInstance 引用）
5. 清理 SandboxInstance（去 IEventEmitter / StatsCollector 依赖）
6. 清理 StartProcessUseCase（去 IEventEmitter / IProcessOutputSink / StreamReader 依赖）
7. 删除 core/ports/IEventEmitter.hpp / IProcessOutputSink.hpp / IIpcServer.hpp / ICommandHandler.hpp
8. 删除 core/entities/IpcMessage.hpp
9. 清理 StartProcessPayloadParser（去 IPC JSON 路径）
10. 更新 CMake（删 sandbox 目标，win_sandbox_native 去掉已删源文件）
11. 删除 Python client.py / async_client.py / protocol.py
12. 清理 exceptions.py
13. 删除 IPC 专属 e2e 测试
14. 更新 __init__.py
```

### 3.2 SandboxInstance 清理

`src/adapters/SandboxInstance.hpp`：
- 删除 `IEventEmitter*` 构造参数（Phase 11 已新增无 emitter 构造，此处删除旧构造）
- 删除 `stats_interval_ms` 参数（StatsCollector 已删，Python 端轮询替代）
- 删除 `ProcessEntry::stats_collector` 字段
- 删除 `stats_interval_ms_` 成员

### 3.3 StartProcessUseCase 清理

`src/core/usecases/StartProcessUseCase.hpp`：
- 删除 `IEventEmitter*` 构造参数
- 删除 `IProcessOutputSink` 继承
- 删除 `stdout_reader_` / `stderr_reader_` 成员
- 删除 `wait_thread_` / `wall_clock_thread_` / `wall_clock_stop_once_`
- 删除 `EmitProcessOutput` / `EmitAccessDenied` / `EmitNetworkBlocked`（IPC 事件发射）
- 删除 `WaitLoop` / `StartWallClockTimer` / `StopWallClockTimer`
- 删除 `ContainsAccessDeniedKeyword`（移到 pybind11 工具函数，Phase 13）

### 3.4 CMake 改造

`src/CMakeLists.txt`：
- 删除 `add_executable(sandbox ...)` 整块
- `pybind11_add_module(win_sandbox_native ...)` 源文件列表移除已删文件：
  - `infra/ipc/FrameCodec.cpp` ❌
  - `infra/ipc/NamedPipeServerImpl.cpp` ❌
  - `infra/process/StreamReader.cpp` ❌
  - `infra/stats/StatsCollectorImpl.cpp` ❌
  - `main.cpp` ❌（本就不在 pybind11 目标）

### 3.5 Python 清理

`python/win_sandbox/__init__.py` 改为：

```python
"""win-sandbox: Windows 进程沙箱隔离（pybind11 in-process 库形态）"""
from __future__ import annotations
import os
import sys

# 加载 pybind11 扩展
_build_dir = os.path.join(os.path.dirname(__file__), "_native")
if os.path.isdir(_build_dir):
    sys.path.insert(0, _build_dir)

from ._native import *  # noqa: F401,F403
from .helpers import *  # noqa: F401,F403  # Phase 13

__version__ = "0.2.0"
```

`python/win_sandbox/exceptions.py` 保留：

```python
class SandboxError(Exception): ...
class SandboxTimeoutError(SandboxError): ...
class SandboxProcessError(SandboxError): ...
# ProtocolError 删除
```

---

## 4. 任务拆分

| 任务 | 描述 | 产出 |
|------|------|------|
| T12.1 | 删除 `src/main.cpp` | 文件删除 |
| T12.2 | 删除 `src/infra/ipc/` 目录 | 目录删除 |
| T12.3 | 删除 `src/infra/process/StreamReader.*` | 文件删除 |
| T12.4 | 删除 `src/infra/stats/` 目录 | 目录删除 |
| T12.5 | 清理 `SandboxInstance`（去 IPC 兼容） | `.hpp/.cpp` 改动 |
| T12.6 | 清理 `StartProcessUseCase`（去 IPC 兼容） | `.hpp/.cpp` 改动 |
| T12.7 | 删除 IPC 端口接口（4 个 .hpp） | 文件删除 |
| T12.8 | 删除 `core/entities/IpcMessage.hpp` | 文件删除 |
| T12.9 | 清理 `StartProcessPayloadParser` | `.hpp/.cpp` 改动 |
| T12.10 | 更新 `src/CMakeLists.txt`（删 sandbox 目标 + 清理源文件列表） | CMake 改动 |
| T12.11 | 删除 Python `client.py` / `async_client.py` / `protocol.py` | 文件删除 |
| T12.12 | 清理 `exceptions.py` + 更新 `__init__.py` | Python 改动 |
| T12.13 | 删除 4 套 IPC 专属 e2e 测试 | 文件删除 |
| T12.14 | 更新 `tests/e2e/run_all_regression.py`（移除已删套件） | Python 改动 |
| T12.15 | 构建 + pybind11 测试验证 | 全绿 |

---

## 5. 验收标准

### 5.1 构建验收

```powershell
cmake --build build
# 仅产出 win_sandbox_native.pyd，无 sandbox.exe
Test-Path build/bin/win_sandbox_native.pyd  # True
Test-Path build/bin/sandbox.exe             # False
```

### 5.2 代码清洁验收

```powershell
# 无 IPC 残留引用
rg "IEventEmitter|IIpcServer|ICommandHandler|IProcessOutputSink|NamedPipeServer|FrameCodec|StreamReader|StatsCollector" src/
# 应无结果（除注释/文档）
```

### 5.3 测试验收

Phase 11 新增的 native 测试全绿：

```powershell
python tests/e2e/test_native_smoke.py
python tests/e2e/test_native_callback.py
python tests/e2e/test_native_handle.py
python tests/e2e/test_native_isolation.py
```

ctest 单元测试通过（移除已删组件对应用例）。

### 5.4 Python 导入验收

```python
import win_sandbox_native
sb = win_sandbox_native.SandboxInstance()
print(sb.capabilities)
# 无 ProtocolError / SandboxClient 等旧符号
```

---

## 6. 风险与处置

| # | 风险 | 处置 |
|---|---|---|
| 1 | 删除顺序不当导致中间编译错误 | 按 3.1 依赖逆序删除；每步编译验证 |
| 2 | `StartProcessUseCase` 深度耦合 IPC（Emit* 方法散布） | Phase 11 已拆分，本 Phase 仅删除死代码；grep 确认无引用 |
| 3 | `IpcMessage` 被非 IPC 代码引用 | grep 确认引用点；pybind11 用 `py::dict` 替代 |
| 4 | ctest 单测引用已删组件 | 同步删除/改造对应单测 |
| 5 | `run_all_regression.py` glob 自动收录已删套件 | 显式排除或删除文件 |
| 6 | blackbox_phase8/9 测试基于 IPC | 归档或删除（Phase 14 处理） |

---

## 7. 测试策略

本 Phase 主要是删除，测试策略为：

1. **编译验证**：每删一组文件，`cmake --build` 确认无编译错误
2. **native 测试回归**：Phase 11 的 4 套 native 测试全绿
3. **ctest 回归**：单元测试通过（同步删除已删组件用例）
4. **代码搜索**：`rg` 确认无 IPC 符号残留

---

## 8. 后续衔接

- Phase 13：ETW 回调 + `contains_access_denied_keyword` 工具函数 + Python helpers（WallClockTimer / StatsPoller / read_pipe / write_pipe）
- Phase 14：剩余 19 套 e2e 测试迁移到 pybind11 直调
- Phase 15：文档更新（ARCHITECTURE/API_REFERENCE/USER_GUIDE/DEPLOYMENT/README 移除 IPC 描述）
