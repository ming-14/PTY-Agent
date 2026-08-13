# Phase 9: 进程树 IPC 能力扩展

**Phase 编号**: 9
**Phase 名称**: 进程树管理 IPC 能力扩展
**创建日期**: 2026-08-10
**预计工期**: 3 个工作日
**负责人**: rikka
**状态**: ✅ 已完成（2026-08-10 实施完毕，全量回归 23/23 + ctest 14/14）
**上游依赖**: Phase 8（已完成，Job 进程树管理能力齐备）
**下游影响**: 无（纯 IPC 面补全，不改变既有协议帧格式/命令/事件命名风格/调用惯例）

---

## 1. Phase 目标

### 1.1 总体目标

Phase 8 已在 C++ 层（`IJobObject` / `JobObjectImpl`）补齐完整的 Job 进程树管理能力：进程归组、进程列表查询、任意 PID 退出码查询、IOCP 实时通知（新进程加入 / 正常退出 / 异常退出 / 资源限制触发）、崩溃静默。但其中**部分能力未通过 IPC 暴露给客户端，或仅暴露主进程视角**：

| 能力 | C++ 层 | IPC 层（现状） |
|------|--------|----------------|
| Job 内子/孙进程创建实时通知 | ✅ IOCP NewProcess | ❌ `StartProcessUseCase::OnNotification` 仅记日志，未下发 |
| Job 内子/孙进程退出实时通知 | ✅ IOCP ProcessExitNormal / ProcessExitAbnormal | ❌ 同上，仅记日志 |
| 任意 PID 退出码查询 | ✅ `QueryProcessExitCode(pid)` | ❌ 无对应命令，仅 `process_exited` 携带主进程退出码 |
| 任意 PID 进程路径查询 | ✅ `QueryProcessPath(pid)` | ❌ 仅 `process_started` 携带主进程路径 |

本 Phase 补全进程树管理的 IPC 面，使外部客户端能**完整观测与管理 Job 内全部进程（含子/孙进程）**：

1. **扩展点 1（必需）**：Job 内进程生命周期实时事件透传 —— 下发 `job_process_started` / `job_process_exited` 事件
2. **扩展点 2（必需）**：任意 PID 退出码查询 —— 新增 `query_process_exit_code` 命令 → `process_exit_code` 定向响应

### 1.2 非目标

- **不做扩展点 3（attach_process，外部进程加入 Job）**：需求原文标注"可选，取决于集成方式"。本期集成方均通过 `start_process` 创建进程，无外部进程 attach 场景。此模式复杂度高（PID attach 有 PID 复用竞态；handle attach 需跨进程 DuplicateHandle）且无 AppContainer 隔离（进程未在创建时派生低箱 token），易被误用为"已隔离"。待集成方确有需要时单独立项评估。
- 不改变现有 IPC 帧协议、命令/事件命名风格、调用惯例
- 不修改既有事件 schema（`process_started` / `process_exited` / `process_list` 等字段保持不变）
- 不引入跨进程句柄传递
- 不涉及跨平台兼容性（仅 Windows）

---

## 2. 功能需求

### 2.1 功能需求清单

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-9.1 | Job 内子/孙进程创建实时通知透传（`job_process_started`） | P0 | 子进程加入 Job 时客户端实时收到事件，字段完整 |
| FR-9.2 | Job 内子/孙进程退出实时通知透传（`job_process_exited`） | P0 | 子进程退出时客户端实时收到事件，exit_kind 分类正确 |
| FR-9.3 | 主进程事件不重复下发 | P0 | 主进程只走既有 `process_started` / `process_exited`，无对应 `job_process_*` 重复事件 |
| FR-9.4 | 同一 PID 退出事件去重 | P0 | 崩溃路径（先 ABNORMAL_EXIT 再 EXIT）同一 PID 仅下发一次 |
| FR-9.5 | `query_process_exit_code` 命令查询任意 PID 退出码 | P0 | 运行中返回 STILL_ACTIVE(259) + is_active=true；已退出返回最终退出码 + is_active=false |
| FR-9.6 | `job_process_started` 携带父进程 PID | P1 | parent_pid 字段尽力填充（父进程已退出则省略） |
| FR-9.7 | 退出码查询失败兜底仍下发退出事件 | P1 | 兜底类型 ProcessExit（读码失败）也下发 `job_process_exited`，exit_kind="unknown"、省略 exit_code |

### 2.2 事件 schema（沿用现有 process_* / job_* 命名风格）

#### 2.2.1 `job_process_started`（Job 内新进程加入，广播）

```json
{
  "process_id": 1,
  "pid": 5678,
  "process_name": "cl.exe",
  "process_path": "C:\\...\\cl.exe",
  "parent_pid": 1234,
  "timestamp_ms": 1722112345678
}
```

- `process_id`：沙箱内部 ID（与现有事件一致，标识所属 Job）
- `pid`：新加入进程的 OS PID
- `process_name` / `process_path`：复用 `JobNotification` 已填充的字段（Phase 8 起 NewProcess 通知携带）
- `parent_pid`：父进程 PID（best-effort，若可获取；父进程已退出则省略）
- 不携带 `command_line`（子进程命令行需额外查询，非本 Phase 范围）

#### 2.2.2 `job_process_exited`（Job 内进程退出，广播）

```json
{
  "process_id": 1,
  "pid": 5678,
  "exit_code": 0,
  "exit_kind": "normal",
  "timestamp_ms": 1722112345678
}
```

- `exit_kind`：`"normal"`（ProcessExitNormal，退出码 0）/ `"abnormal"`（ProcessExitAbnormal，退出码非 0 含崩溃）/ `"unknown"`（兜底 ProcessExit，退出码查询失败时，此时**省略 `exit_code` 字段**，不写 null）
- `exit_code`：复用 `ReadExitCodeSettled` 已读取的值（`notif.exit_code`）；`exit_kind="unknown"` 时该字段**不存在**，客户端须用 `payload.get("exit_code")` 访问而非 `payload["exit_code"]`

#### 2.2.3 `query_process_exit_code`（命令，payload schema）

```json
{ "process_id": 1, "pid": 5678 }
```

#### 2.2.4 `process_exit_code`（命令的定向响应事件）

```json
{
  "process_id": 1,
  "pid": 5678,
  "exit_code": 0,
  "is_active": false
}
```

- `is_active`：`true` 表示进程仍在运行（`exit_code` 为 STILL_ACTIVE=259）；`false` 表示已退出
- **固有歧义**：进程恰好以退出码 259 正常退出时 `is_active` 误判为 true——这是 Win32 `GetExitCodeProcess` 的既有约定（win-sandbox 既有退出码判断同此），非本 Phase 引入；实践中以 259 为退出码的程序罕见，可接受
- 错误情况（`process_not_found` / `query_failed`）沿用现有 `error` 事件风格

### 2.3 接口需求

#### 2.3.1 IPC 消息类型扩展（`src/core/entities/IpcMessage.hpp`）

新增枚举值与字符串映射（4 项，保持命名风格）：

| 枚举 | 字符串 | 方向 |
|------|--------|------|
| `QueryProcessExitCode` | `query_process_exit_code` | 命令（Python → 沙箱） |
| `JobProcessStarted` | `job_process_started` | 事件（沙箱 → Python，广播） |
| `JobProcessExited` | `job_process_exited` | 事件（沙箱 → Python，广播） |
| `ProcessExitCode` | `process_exit_code` | 事件（沙箱 → Python，定向响应） |

#### 2.3.2 `JobNotification` 实体扩展（`src/core/entities/JobNotification.hpp`）

新增字段：

```cpp
struct JobNotification {
    // ... 既有字段不变 ...
    std::optional<uint32_t> parent_pid;   // NEW_PROCESS 时尽力填充（Toolhelp 快照）；父进程已退出则省略
};
```

#### 2.3.3 `SandboxInstance` 接口扩展（`src/adapters/SandboxInstance.hpp/.cpp`）

```cpp
// Phase 9：查询指定 process_id 对应 Job 内任意 PID 的退出码
// 进程不存在返回 ProcessNotFound；查询失败（OpenProcess/GetExitCodeProcess 失败）返回 JobQueryFailed
Result<uint32_t> QueryProcessExitCode(uint32_t process_id, uint32_t pid) const;
```

#### 2.3.4 Python 客户端 API（`python/win_sandbox/client.py` / `async_client.py`）

```python
# SandboxClient（同步）
def send_query_process_exit_code(
    self,
    process_id: int,
    pid: int,
    *,
    request_id: Optional[str] = None,
    timeout: float = 5.0,
) -> str: ...

# AsyncSandboxClient（异步，签名一致）
async def send_query_process_exit_code(...) -> str: ...
```

实现风格与 `send_query_process_list` 完全一致。

---

## 3. 技术设计

### 3.1 架构设计

遵循干净架构，改动全部落在既有分层内，不新增层/模块：

```
┌─────────────────────────────────────────────────────────┐
│ Python 客户端 (protocol.py / client.py / async_client.py)│
│   新增事件常量 + send_query_process_exit_code            │
└──────────────────┬──────────────────────────────────────┘
                   │ 命名管道 (JSON 帧协议，不变)
┌──────────────────┴──────────────────────────────────────┐
│ main.cpp IpcCommandHandler                               │
│   新增 case QueryProcessExitCode（定向响应 process_exit_code）│
├─────────────────────────────────────────────────────────┤
│ SandboxInstance (adapters)                               │
│   新增 QueryProcessExitCode(process_id, pid) 路由         │
├─────────────────────────────────────────────────────────┤
│ StartProcessUseCase (usecases)                           │
│   OnNotification 三分支透传 → EmitJobProcessStarted/Exited │
├─────────────────────────────────────────────────────────┤
│ JobObjectImpl (infra)                                    │
│   IocpLoop NEW_PROCESS 填充 parent_pid（Toolhelp）        │
└─────────────────────────────────────────────────────────┘
```

### 3.2 扩展点 1：生命周期事件透传

#### 3.2.1 通知吞没点（现状）

`StartProcessUseCase::OnNotification`（`src/core/usecases/StartProcessUseCase.cpp:922`）是 IOCP 通知的唯一消费入口。当前 `NewProcess` / `ProcessExit` / `ProcessExitNormal` / `ProcessExitAbnormal` 四个分支仅 `LogDebug` / `LogWarn`，事件被吞没（`ProcessExit` 为 Phase 8 兜底类型：退出码查询失败时的退出行兜底，同样只记日志）。

#### 3.2.2 修改方案

在三个分支中，除现有日志外，通过 `event_emitter_->Emit`（广播语义，与 `process_started` 等一致）下发事件：

| 通知分支 | 下发事件 | exit_kind |
|----------|----------|-----------|
| `NewProcess` | `job_process_started` | — |
| `ProcessExitNormal` | `job_process_exited` | `"normal"` |
| `ProcessExitAbnormal` | `job_process_exited` | `"abnormal"` |
| `ProcessExit`（兜底，Phase 9 扩展） | `job_process_exited` | `"unknown"`（省略 exit_code 字段，不写 null） |

**主进程跳过**：`notif.pid == process_.pid` 时全部跳过。主进程的 `process_started` / `process_exited` 由 usecase 在启动/退出路径直接下发（`EmitProcessStarted` / wait 线程 `EmitProcessExited`），Job 通知里的主进程 PID 跳过以避免重复。注意：主进程被 `AssignProcess` 加入 Job 时同样会触发一次 `JOB_OBJECT_MSG_NEW_PROCESS` 通知。

**去重**：复用 `JobObjectImpl` 既有 `exited_pids_` 机制（`JobObjectImpl.cpp:849-861`）——崩溃路径（DIE_ON_UNHANDLED_EXCEPTION）会先发 ABNORMAL_EXIT（msg=8）再发 EXIT（msg=7），IOCP 线程已保证同一 PID 的退出通知只投递一次；`exited_pids_` 也在 NEW_PROCESS 时按 pid 清除（处理 PID 复用，`JobObjectImpl.cpp:867`）。OnNotification 层无需新增去重。

**线程安全**：`OnNotification` 由 IOCP 线程调用，`event_emitter_`（PipeEventEmitter → `BroadcastEvent`）内部保证线程安全，与 wait 线程 / StreamReader 读线程并发调用无新增竞态。

**新增私有方法**（`StartProcessUseCase.hpp/.cpp`）：

```cpp
// 下发 job_process_started（Job 内新进程加入，广播）
void EmitJobProcessStarted(const JobNotification& notif);
// 下发 job_process_exited（Job 内进程退出，广播）
// exit_kind: "normal" | "abnormal" | "unknown"（unknown 时省略 exit_code）
void EmitJobProcessExited(const JobNotification& notif, std::string_view exit_kind);
```

#### 3.2.3 parent_pid 获取

`JobNotification` 当前无 `parent_pid` 字段。在 `JobObjectImpl::IocpLoop` 的 NEW_PROCESS 处理处（已有 `QueryProcessPath` 查询，`JobObjectImpl.cpp:865-882`）一并填充：

- 实现：`CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS)` + `Process32First/Next` 查找 pid 的 `th32ParentProcessID`（**文档化 Win32 API**，避免使用未文档化的 NtQueryInformationProcess）
- best-effort：查询失败（如父进程已随创建者退出、快照失败）仅 Warn 日志，不阻塞通知投递，`parent_pid` 保持 `std::nullopt`
- 性能：每 NEW_PROCESS 一次进程快照（O(n)，n 为系统进程数），与既有 `QueryProcessPath` 同量级，IOCP 线程执行，可接受

### 3.3 扩展点 2：query_process_exit_code 命令

#### 3.3.1 调用链

```
Python 客户端 send_query_process_exit_code(process_id, pid)
    ↓ IPC（帧协议不变）
main.cpp IpcCommandHandler::HandleCommand case QueryProcessExitCode
    ├─ payload 校验失败 → SendError("invalid_payload")
    ├─ instance_->QueryProcessExitCode(process_id, pid) 失败
    │    ├─ ProcessNotFound → SendError("process_not_found")
    │    └─ JobQueryFailed → SendError("query_failed")
    └─ 成功 → SendEvent(ProcessExitCode, {process_id, pid, exit_code, is_active})
```

- `SandboxInstance::QueryProcessExitCode`：per-process Job 模式下按 `process_id` 找到 entry，路由到 `entry->job->QueryProcessExitCode(pid)`（`JobObjectImpl.cpp:588` 已有实现：OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess；运行中返回 STILL_ACTIVE=259）
- `is_active`：`exit_code == 259`（STILL_ACTIVE）时 `true`，否则 `false`。由命令处理处计算（main.cpp），JobObjectImpl 返回原始退出码
- **错误码语义**：`process_id` 不存在（entry 已清理或从未存在）→ `process_not_found`；`pid` 对应进程不存在/权限不足 → `query_failed`（沿用 QueryProcessList 的既有区分）
- **Job 归属校验（黑盒复核 B5 修复，2026-08-10）**：`QueryProcessExitCode` 先校验 pid 属于本 Job（曾见 pid 集合，含已退出进程；NEW_PROCESS 通知尚未处理的竞态窗口用 Job 活进程列表兜底），不属于则直接返回 `ProcessNotFound` → `process_not_found`。拒绝跨 sandbox 实例的 pid 探测，且对"从未在本 Job 出现的 pid"（如系统 pid）返回语义更准确的 `process_not_found` 而非 `query_failed`

#### 3.3.2 边界场景

| 场景 | 行为 |
|------|------|
| 进程仍在运行 | `exit_code=259, is_active=true` |
| 进程已退出（entry 仍在，CleanupFinished 未清理） | `exit_code=<最终退出码>, is_active=false` |
| 进程已退出且 entry 已被 CleanupFinished 清理 | `process_not_found`（客户端可通过 `query_process_list` 兜底确认） |
| pid 不属于本 Job（跨实例探测 / 从未出现的 pid，黑盒复核 B5 修复） | `process_not_found` |
| pid 在本 Job 但进程对象已回收（如已退出子进程，B2 场景） | `query_failed` |
| 缺 process_id / pid 字段或类型错误（含 float 等非整数，黑盒复核 F9 修复） | `invalid_payload` |

### 3.4 Python 侧设计

- `protocol.py`：`MessageType` 类新增 4 常量（`QUERY_PROCESS_EXIT_CODE` / `JOB_PROCESS_STARTED` / `JOB_PROCESS_EXITED` / `PROCESS_EXIT_CODE`）。客户端接收分发**无需改动**（`_recv_queue` 已按 type 透传，`recv_message` / `collect_events_until_exit` 均按 type 分发）
- `client.py`：`send_query_process_exit_code(process_id, pid, *, request_id=None, timeout=5.0) -> str`，风格与 `send_query_process_list`（client.py:661）完全一致（request_id 自动生成前缀 `qpec` 或类似、`send_message` 发送、返回 rid 供关联定向响应）
- `async_client.py`：对应 async 版本
- 事件消费：客户端通过 `recv_message` / 事件队列自行按 `type` + `request_id` 关联（`process_exit_code` 为定向响应，仅发起命令的客户端收到）

### 3.5 错误处理

| 错误 | 位置 | 处理 |
|------|------|------|
| 事件发送失败（管道断连/写失败） | EmitJobProcessStarted/Exited | 与既有 Emit* 一致：Error/Warn 日志，不重试 |
| parent_pid 查询失败 | JobObjectImpl IocpLoop | Warn 日志，字段省略，不阻塞通知 |
| 退出码读取失败 | TranslateMessage（既有） | 兜底 `ProcessExit` 类型 → `job_process_exited` exit_kind="unknown" |
| 命令查询失败 | main.cpp | `process_not_found` / `query_failed` / `invalid_payload`（现有 error 事件风格） |

### 3.6 性能与安全

- 事件透传仅新增 JSON 组装 + 一次 BroadcastEvent，IOCP 线程路径无新增系统调用（parent_pid 除外）
- parent_pid 查询频率 = NEW_PROCESS 频率（进程创建时一次），单次 O(系统进程数) 快照，量级与既有 QueryProcessPath 一致
- 不新增任何客户端输入驱动的危险操作：命令仅查询（OpenProcess 以 `PROCESS_QUERY_LIMITED_INFORMATION` 打开），不注入、不写、不跨用户
- 事件仅广播（不携带命令行等敏感数据），无新增数据外泄面

---

## 4. 实施计划

### 4.1 任务分解

| 任务编号 | 任务描述 | 预计工时 | 依赖 | 负责人 |
|----------|----------|----------|------|--------|
| T-9.1 | IPC 消息类型扩展（IpcMessage.hpp 4 项） | 0.25d | - | rikka |
| T-9.2 | JobNotification 增加 parent_pid 字段 | 0.25d | - | rikka |
| T-9.3 | JobObjectImpl IocpLoop 填充 parent_pid（Toolhelp） | 0.5d | T-9.2 | rikka |
| T-9.4 | StartProcessUseCase OnNotification 透传 + Emit* 方法 | 0.5d | T-9.1 | rikka |
| T-9.5 | SandboxInstance::QueryProcessExitCode 路由 | 0.25d | - | rikka |
| T-9.6 | main.cpp 命令分发 case + is_active 计算 | 0.5d | T-9.1, T-9.5 | rikka |
| T-9.7 | Python protocol.py 常量 + 同步/异步客户端方法 | 0.5d | T-9.1 | rikka |
| T-9.8 | e2e 测试套件 test_process_tree.py | 1d | T-9.4~T-9.7 | rikka |
| T-9.9 | 文档更新（API_REFERENCE / USER_GUIDE / README / memory） | 0.5d | T-9.4~T-9.7 | rikka |
| T-9.10 | 全量回归（22 套件 + ctest 14 项） | 0.5d | T-9.8 | rikka |
| **总计** | | **3d** | | |

### 4.2 详细实施步骤

#### T-9.1: IPC 消息类型扩展

**文件**: `src/core/entities/IpcMessage.hpp`

**步骤**:
1. `MessageType` 枚举：命令区新增 `QueryProcessExitCode`；事件区新增 `JobProcessStarted` / `JobProcessExited` / `ProcessExitCode`
2. `NLOHMANN_JSON_SERIALIZE_ENUM` 映射表新增 4 项（注意 `Unknown` 必须保持在映射表首个，不可动）

**验收**: 编译通过；双端字符串一致

#### T-9.2: JobNotification 增加 parent_pid

**文件**: `src/core/entities/JobNotification.hpp`

**步骤**:
1. `JobNotification` 新增 `std::optional<uint32_t> parent_pid;`
2. 头文件注释更新（NEW_PROCESS 时尽力填充；父进程已退出则省略）

**验收**: 编译通过；既有构造点（聚合初始化）不受影响（optional 有默认值）

#### T-9.3: JobObjectImpl 填充 parent_pid

**文件**: `src/infra/job/JobObjectImpl.cpp`

**步骤**:
1. IocpLoop 的 NEW_PROCESS 分支（`JobObjectImpl.cpp:865-882`，与 QueryProcessPath 并列）：
   - `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)` → `Process32First/Next` 遍历，匹配 `th32ProcessID == pid` 时取 `th32ParentProcessID` 填入 `notif.parent_pid`
   - 未命中/快照失败：Warn 日志，保持 `std::nullopt`
   - 快照句柄 RAII 关闭（wil 或 CloseHandle）

**验收**: 子进程通知带 parent_pid；父进程已退出的极端场景不阻塞投递

#### T-9.4: StartProcessUseCase OnNotification 透传

**文件**: `src/core/usecases/StartProcessUseCase.hpp/.cpp`

**步骤**:
1. `.hpp` 新增私有方法声明：`EmitJobProcessStarted(const JobNotification&)` / `EmitJobProcessExited(const JobNotification&, std::string_view exit_kind)`
2. `.cpp` 实现两方法（payload 组装与 `EmitProcessStarted` 风格一致；`timestamp_ms` 用 `notif.timestamp_ms`；失败记 Error 日志）
3. `OnNotification` 四个分支改造：
   - `NewProcess`：`notif.pid != process_.pid` → `EmitJobProcessStarted`；保留原 Debug 日志
   - `ProcessExitNormal`：非主 pid → `EmitJobProcessExited(notif, "normal")`
   - `ProcessExitAbnormal`：非主 pid → `EmitJobProcessExited(notif, "abnormal")`
   - `ProcessExit`（兜底）：非主 pid → `EmitJobProcessExited(notif, "unknown")`（exit_code 字段不写入 JSON——该类型退出码本就不可得；C++ 侧 `notif.exit_code` 为 `nullopt`，序列化时省略该 key）
4. exit_kind 用常量字符串，与 `ExitKindToString`（StartProcessUseCase.cpp:1194）的 `"normal"/"abnormal"` 值对齐

**验收**: 编译通过；子进程事件实时下发；主进程 pid 零重复

#### T-9.5: SandboxInstance::QueryProcessExitCode

**文件**: `src/adapters/SandboxInstance.hpp/.cpp`

**步骤**:
1. `.hpp` 声明 `Result<uint32_t> QueryProcessExitCode(uint32_t process_id, uint32_t pid) const;`
2. `.cpp` 实现（仿 `QueryProcessList`，SandboxInstance.cpp:346）：
   - `shared_lock` 查 entry；不存在 → `ProcessNotFound`
   - `entry->job->QueryProcessExitCode(pid)` 透传结果

**验收**: process_id 不存在 → ProcessNotFound；正常/异常 → 退出码

#### T-9.6: main.cpp 命令分发

**文件**: `src/main.cpp`

**步骤**:
1. `HandleCommand` switch 新增 `case MessageType::QueryProcessExitCode`（仿 QueryProcessList 分支，main.cpp:554）：
   - `ExtractProcessId(cmd.payload)` 校验 process_id；新增 `pid` 字段校验（数值 > 0，缺/非法 → `invalid_payload`）
   - `instance_->QueryProcessExitCode(process_id, pid)`：
     - `ProcessNotFound` → `SendError("process_not_found")`
     - `JobQueryFailed` → `SendError("query_failed")`
   - 成功：`server_->SendEvent(client_id, msg)` 定向发送 `ProcessExitCode`：
     ```
     payload = {"process_id", "pid", "exit_code", "is_active"}
     is_active = (exit_code == 259)  // STILL_ACTIVE
     ```
2. 文件头注释（main.cpp:23-31 命令分发清单）补 `QueryProcessExitCode`

**验收**: 三种错误码路径 + 成功路径均正确

#### T-9.7: Python 协议与客户端

**文件**: `python/win_sandbox/protocol.py` / `client.py` / `async_client.py`

**步骤**:
1. `protocol.py` `MessageType` 新增 4 常量（与 C++ 字符串一致，注释标注 Phase 9）
2. `client.py` 新增 `send_query_process_exit_code`（仿 `send_query_process_list`，client.py:661；request_id 前缀建议 `qpec`）
3. `async_client.py` 新增对应 async 版本（仿 async_client.py:387）

**验收**: 双端类型常量对齐；同步/异步方法可用

#### T-9.8: e2e 测试套件

**文件**: `tests/e2e/test_process_tree.py`（新建；`run_all_regression.py` 按 `test_*.py` glob 自动收录，无需注册）

**用例设计**（详见 §5.2）：

| 用例 | 验证点 |
|------|--------|
| T9-1 子进程创建事件 | `cmd /c ping` 场景，收到 ≥1 个 `job_process_started`（pid ≠ 主 pid），process_name/process_path/parent_pid 非空 |
| T9-2 主进程不重复 | 只收 `process_started`，无主 pid 的 `job_process_started`；退出时无主 pid 的 `job_process_exited` |
| T9-3 子进程正常退出 | `job_process_exited` exit_kind="normal"、exit_code=0 |
| T9-4 子进程异常退出 | 子 cmd `exit 7` → exit_kind="abnormal"、exit_code=7 |
| T9-5 崩溃路径去重 | 子进程跑 crash_dummy（复用已有 `tests/unit/crash_dummy.cpp`，构建目标 `tests/CMakeLists.txt:505`，定位方式同 `test_job_enhancement.py:60`）→ 同一 pid 仅一次 `job_process_exited`，exit_kind="abnormal"、exit_code=0xC0000005 |
| T9-6 退出码查询（运行中） | is_active=true、exit_code=259 |
| T9-7 退出码查询（已退出） | 主进程退出事件到达后**立即**查询 → is_active=false、exit_code 与 ProcessExited 一致（实施发现：已退出进程对象存活窗口 ≥100ms，之后 OpenProcess 失败；测试带短重试兜底竞态） |
| T9-8 退出码查询错误路径 | pid 不存在 → error `query_failed`；process_id 不存在 → `process_not_found`；缺 pid 字段 → `invalid_payload` |
| T9-9 异步客户端 | AsyncSandboxClient `send_query_process_exit_code` 全链路 |

#### T-9.9: 文档更新

**文件**:
- `docs/API_REFERENCE.md`：§5 命令表补 `query_process_exit_code`；§6 事件表补 `job_process_started` / `job_process_exited` / `process_exit_code` 三个 schema（§6.11 之后新增编号）；§3.3 新增 `send_query_process_exit_code` API；§4 异步对照补 async 版本
- `docs/USER_GUIDE.md`：进程管理章节补命令用法与事件说明（含 is_active / exit_kind 语义、error 码）
- `README.md`：IPC 协议命令/事件列表、测试套件表（22 → 23 套件）
- `docs/memory/`：新增本 Phase 记忆条目（决策点：attach 不做、unknown 兜底、parent_pid 方案）
- 本 Phase 文档状态更新为已完成

#### T-9.10: 全量回归

**命令**:
```powershell
cmake --build build
python tests/e2e/test_process_tree.py
python tests/e2e/run_all_regression.py
ctest --test-dir build -C Debug
```

**验收**: 新套件 9/9 PASS；既有 22 套件 + ctest 14 项零回归

---

## 5. 测试策略

### 5.1 测试分层

| 层 | 载体 | 覆盖 |
|----|------|------|
| e2e（主） | `tests/e2e/test_process_tree.py`（新） | 完整 IPC 链路上的事件透传与命令查询 |
| 回归 | `run_all_regression.py` + ctest | 既有功能零回归 |
| 单元（C++） | 既有 verify_t*.cpp 模式 | 本次新增逻辑主体在 usecase 事件组装与 JobObjectImpl 路径，e2e 可覆盖；如验证需要可补充 verify 用例（可选） |

### 5.2 e2e 用例明细

见 §4.2 T-9.8 表格。要点：

- 子进程载体：`cmd.exe /c ping -n 6 127.0.0.1 >nul`（长跑，便于观察创建事件）与 `cmd.exe /c exit 7`（异常退出）
- 事件收集：客户端从 `_recv_queue` 轮询（沿用 test_job_enhancement.py 的 `_wait_process_exited` 等辅助模式），按 type + pid 断言
- 去重用例：复用已有 crash_dummy.exe（`tests/unit/crash_dummy.cpp`，构建后与 sandbox.exe 同目录），定位方式同 `test_job_enhancement.py:60`（`_CRASH_DUMMY = _DEFAULT_EXE.parent / "crash_dummy.exe"`），作为子进程启动，断言崩溃退出码 0xC0000005 且 `job_process_exited` 仅一条
- 每个用例独立启动 sandbox.exe（隔离环境），finally 中 `_shutdown` 清理

### 5.3 回归标准

- `run_all_regression.py` 23/23（22 既有 + 1 新增）
- `ctest --test-dir build -C Debug` 14/14
- 旧构建基线不适用（本 Phase 为纯新增，无既有行为修改）

---

## 6. 风险管理

### 6.1 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| parent_pid 查询竞态（父进程已退出） | 中 | 低 | optional 字段省略，best-effort 语义，不阻塞投递 |
| 崩溃路径退出事件时序（ABNORMAL_EXIT 先于 EXIT） | 低 | 低 | 复用既有 exited_pids_ 去重，e2e T9-5 专项验证 |
| 主进程 NEW_PROCESS 通知与 ProcessStarted 顺序 | 低 | 低 | 主 pid 直接跳过（无论先后），不依赖时序 |
| 事件洪泛（子进程频繁创建） | 低 | 低 | 广播路径已有背压/丢弃机制（既有 BroadcastEvent），量级与 ProcessOutput 一致 |
| PID 复用导致 parent_pid 错配 | 低 | 低 | parent_pid 仅用于观测，不驱动安全决策；NEW_PROCESS 已按 pid 清退出记录 |
| Toolhelp32Snapshot 全系统进程快照开销 | 低 | 低 | 每次 NEW_PROCESS 一次 O(系统进程数) 快照，命中即停；量级与既有 QueryProcessPath 同级，IOCP 线程执行不阻塞主路径；高频 spawn 场景可观测但可接受 |

### 6.2 进度风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 工期估算偏差 | 中 | 低 | 预留 20% 缓冲；本 Phase 无新模块，改动点明确 |
| e2e 用例对子进程时序敏感（间歇性） | 中 | 中 | 事件收集带超时轮询 + 复跑验证；子进程载体选稳定命令（ping 长跑） |

### 6.3 质量风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 既有 22 套件回归 | 低 | 高 | 全量回归 + ctest；事件为纯新增，不改既有字段 |
| 文档与实现不一致 | 低 | 中 | 文档随实现同步更新（T-9.9），Phase 完成时核对 |

---

## 7. 验收标准

### 7.1 功能验收

- [x] FR-9.1: `job_process_started` 实时下发，字段完整（e2e T9-1）
- [x] FR-9.2: `job_process_exited` 实时下发，exit_kind 分类正确（e2e T9-3/T9-4）
- [x] FR-9.3: 主进程零重复事件（e2e T9-2）
- [x] FR-9.4: 崩溃路径同 pid 仅一次退出事件（e2e T9-5）
- [x] FR-9.5: `query_process_exit_code` 全路径正确（e2e T9-6/T9-7/T9-8）
- [x] FR-9.6: `job_process_started` 携带 parent_pid（e2e T9-1）
- [x] FR-9.7: 兜底 ProcessExit 也下发 exit_kind="unknown"（代码审查 + 行为核对：`ProcessExit` 分支 `EmitJobProcessExited(notif, "unknown")`，`notif.exit_code` 为 nullopt 时序列化省略该 key）

### 7.2 质量验收

- [x] 新 e2e 套件 9/9 PASS
- [x] 既有 22 套件全量回归 PASS（23/23）+ ctest 14/14 PASS
- [x] 代码符合 win-sandbox 编码规范（注释/日志齐备，无兼容接口残留）

### 7.3 文档验收

- [x] API_REFERENCE / USER_GUIDE / README 同步更新
- [x] docs/memory 记录决策点与实施发现（2026-08-10.md）
- [x] 本 Phase 文档状态更新为已完成

### 7.4 向后兼容性验收

- [x] 既有命令/事件 schema 零改动（纯新增）
- [x] 既有 Python 客户端（未升级）与新版 sandbox.exe 互通：`IpcMessage.from_dict`（`python/win_sandbox/protocol.py:117`）仅校验 `type`/`version` 字段存在、不校验 type 枚举值，未知 type（如 `job_process_started`）成功解析后入 `_recv_queue`，旧客户端 `recv_message` / `collect_events_until_exit` 按 type 分发时无 handler 即忽略，不抛异常
- [x] 既有 C++ 单元验证程序编译/运行不受影响（JobNotification 新增字段有默认值；verify_t11/t14-t18/t21-t28 全部编译通过并运行 PASS）

---

## 8. 后续工作

### 8.1 attach_process（扩展点 3，评估后立项）

**目标**: 支持外部已创建进程（客户端持有进程句柄/PID）加入 Job 统一管理

**前置条件**: 集成方确认需要自行创建进程、仅将进程树管理委派给 win-sandbox

**要点**:
- 新增 IPC 命令 `attach_process`（按 PID attach 起步，OpenProcess 重新获取句柄；handle 传递需评估 DuplicateHandle 跨进程复杂度）
- 此模式不派生 AppContainer token，仅提供 Job 进程树管理 + 资源限制 + 通知能力，需明确文档警告
- PID 复用竞态防护（attach 后通过 NEW_PROCESS 通知比对进程创建时间戳）

### 8.2 job_process_started 增强字段（可选）

- 携带 `command_line`（子进程命令行）：需在 NEW_PROCESS 时额外查询（Toolhelp 或 PEB 读取），代价较高，按需评估
- 孙进程树深度的信息（仅 parent_pid 可推，不直接提供）

### 8.3 进程树快照命令（可选）

- `query_process_tree`：一次返回 Job 内完整进程树（pid + parent_pid + path + state），供客户端重建树结构；现阶段可基于 `query_process_list` + 事件流组合实现

---

## 9. 参考资料

### 9.1 内部文档

- `docs/Phase/Phase-8-JobEnhancement-20260808.md` - Phase 8 实现记录（Job 进程树能力来源）
- `docs/API_REFERENCE.md` - IPC 协议与 Python API 契约（本 Phase 更新对象）
- `docs/ARCHITECTURE.md` - 架构文档
- `docs/memory/Lessons-Learned.md` - 踩坑记录（事件/去重/竞态相关教训）

### 9.2 外部文档

- Microsoft Docs: Job Objects（JOB_OBJECT_MSG_NEW_PROCESS / EXIT_PROCESS / ABNORMAL_EXIT_PROCESS）
- Microsoft Docs: CreateToolhelp32Snapshot / Process32First / Process32Next（parent_pid 获取）
- Microsoft Docs: GetExitCodeProcess / STILL_ACTIVE

### 9.3 相关代码

- win-sandbox: `src/core/usecases/StartProcessUseCase.cpp`（OnNotification，通知吞没点）
- win-sandbox: `src/core/ports/IJobObject.hpp`（QueryProcessExitCode / QueryProcessPath）
- win-sandbox: `src/infra/job/JobObjectImpl.cpp`（IocpLoop / TranslateMessage / exited_pids_）
- win-sandbox: `src/main.cpp`（命令分发，QueryProcessList 分支为模板）
- win-sandbox: `python/win_sandbox/client.py`（send_query_process_list 为模板）

---

## 10. 附录

### 10.1 术语表

| 术语 | 说明 |
|------|------|
| Job 进程树 | Job 内主进程及其未 breakaway 逃逸的子/孙进程 |
| exit_kind | 退出分类：normal（码 0）/ abnormal（码非 0 含崩溃 NTSTATUS）/ unknown（读码失败） |
| is_active | 进程是否仍在运行（exit_code == STILL_ACTIVE(259)） |
| STILL_ACTIVE | Windows 常量 259，GetExitCodeProcess 对运行中进程的返回值 |
| 广播事件 | 发给所有已连接客户端（process_started 等既有事件同语义） |
| 定向响应 | 仅发给发起命令的客户端（process_list 等既有响应同语义） |

### 10.2 决策记录

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 扩展点 3 attach_process | **本期不做** | 需求标注可选；无实际集成场景；PID attach 竞态 + 无 AppContainer 隔离易误用（已与用户对齐，2026-08-10） |
| ProcessExit 兜底分支 | **下发** job_process_exited，exit_kind="unknown" | 保证事件流完整：客户端至少感知进程退出；exit_code 不可得则省略（已与用户对齐，2026-08-10） |
| parent_pid 获取位置 | JobObjectImpl IocpLoop + Toolhelp32Snapshot | 与既有 QueryProcessPath 同处、同频；文档化 API；core 实体仅加 optional 字段 |
| 去重 | 复用既有 exited_pids_，OnNotification 层不新增 | 去重已由 IOCP 线程保证（含 PID 复用处理），避免双层去重的维护复杂度 |

### 10.3 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
|------|------|----------|------|
| v1.0 | 2026-08-10 | 初始版本（计划稿） | rikka |
| v1.1 | 2026-08-10 | 实施完成。全部任务 T-9.1~T-9.10 落地：事件透传 + 退出码查询 + e2e 9/9 + 全量回归 23/23 + ctest 14/14。实施中发现并记录：① conhost.exe 会进入 Job 并产生 job_process_* 事件（e2e 断言须按 process_name 过滤）；② 已退出进程的退出码查询窗口（对象存活 ≥100ms，须退出事件后立即查询）；③ T9-7 用例改为"主进程退出后立即查询" | rikka |
| v1.2 | 2026-08-10 | 黑盒复核修复：① F9——`query_process_exit_code` 的 pid/process_id 类型校验由 `is_number` 收紧为 `is_number_integer`（float 静默截断 1234.5→1234 的契约违背，main.cpp 两处）；② B5——`JobObjectImpl::QueryProcessExitCode` 增加 Job 归属校验（曾见 pid 集合 + 活列表兜底），跨实例探测/未知 pid 返回 process_not_found（安全语义）；③ A1a 深层嵌套"漏报"确认为黑盒命令构造误报（`\"` 非 cmd 转义引号），修正命令后 3 层嵌套 4 层进程树完整上报无遗漏；④ e2e 新增 T9-10（类型校验）/ T9-11（跨实例隔离），8a 期望更新（pid 不属于 Job → process_not_found），套件 9 → 11 用例 | rikka |

---

**Phase 状态**: ✅ 已完成
**最后更新**: 2026-08-10
**下次评审**: 无（已完成）
