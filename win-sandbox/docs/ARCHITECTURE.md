# win-sandbox 架构与技术原理

| 项 | 内容 |
|---|---|
| 文档类型 | 架构与技术原理文档（现行唯一设计文档） |
| 适用范围 | win-sandbox C++ 核心 + pybind11 Python 绑定 |
| 对应源码 | `src/`（core / adapters / infra / pybind11 绑定） |
| 状态 | 现行有效，随源码演进同步更新 |

> 本文档为架构权威参考；接口细节以 `src/` 源码为准。

---

## 1. 概述

win-sandbox 是一个 Windows 原生沙箱库：以 **Job Object + Low IL token** 为隔离技术栈（可选叠加 Server Silo），通过 pybind11 in-process 库（`win_sandbox_native.pyd`）被 Python 直接 `import` 调用。沙箱对被隔离进程施加资源配额、文件系统访问控制（全盘只读 + 自动可写区）与网络策略，并通过 pybind11 回调向 Python 推送 stdout/stderr 句柄、资源统计与监控事件。

典型场景：

- OJ 评测：限 CPU/内存/时间，超限立即终止
- 交互式 REPL：长跑进程，持续 stdin 写入 + 输出回调
- 样本分析：更强的隔离（Silo）+ 行为监控（ETW）
- CI 构建：一次性进程，全盘只读 + 可写区

约束前提（已实现）：

- 隔离强度上限为用户态可达上限，不引入内核驱动（minifilter）
- 部署形态为 in-process 库：Python `import win_sandbox` 加载 `win_sandbox_native.pyd`，无子进程、无 IPC
- Python ↔ C++ 通过 pybind11 直调，stdin/stdout/stderr 管道句柄以 `int` 暴露给 Python 自行 `ReadFile`/`WriteFile`
- Job IOCP / ETW 事件通过 pybind11 回调推 Python（dict payload）
- wall_clock 定时器与 StatsCollector 轮询在 Python 端实现

## 2. 总体架构

严格遵循干净架构（洋葱模型），依赖只允许从外层指向内层：

```
┌─────────────────────────────────────────────────────────────┐
│  Python Bindings（pybind11 绑定层）  src/pybind11/            │
│  win_sandbox_native module                                    │
│  SandboxInstance / SandboxProcess / 管道 helper / 工具函数    │
├─────────────────────────────────────────────────────────────┤
│  Frameworks & Drivers（框架层）  src/infra/                   │
│  JobObjectImpl / ProcessLauncherImpl / TokenIsolatorImpl      │
│  WriteAreaImpl / WfpEngineImpl / EtwMonitorImpl               │
│  GlobalQuotaManagerImpl / SiloImpl / Logger                  │
│  StartupCleanup                                               │
├─────────────────────────────────────────────────────────────┤
│  Interface Adapters（接口适配器）  src/adapters/              │
│  SandboxInstance（多进程管理器）/ ConfigLoader               │
│  PermissionDetector / StartProcessPayloadParser              │
├─────────────────────────────────────────────────────────────┤
│  Use Cases（用例层）  src/core/usecases/                     │
│  NativeSandboxedProcess（进程生命周期：token + 可写区 + 启动） │
├─────────────────────────────────────────────────────────────┤
│  Entities（实体层）+ Ports（端口接口）  src/core/entities/    │
│  SandboxedProcess / StartProcessRequest / ResourceQuota      │
│  IsolationPolicy / JobNotification / Result                   │
│  IJobObject / IProcessLauncher / ITokenIsolator / ...        │
└─────────────────────────────────────────────────────────────┘
```

分层规则：

- **实体层**：纯领域对象与规则，不依赖 `windows.h` 与第三方库（`Result`、`ResourceQuota`、`IsolationPolicy` 等）
- **用例层**：应用特定业务逻辑，只依赖实体与端口接口，不触碰任何 Windows API
- **适配器层**：组装用例与框架实现（`SandboxInstance` 为每个进程装配 Job/Launcher/TokenIsolator/WriteArea 等资源）
- **框架层**：具体技术实现，全部以 `*Impl` 命名并实现端口接口
- **pybind11 绑定层**：暴露 C++ 对象与方法给 Python，转换 dict ↔ struct，注册回调

端口清单（`src/core/ports/`）：

| 端口 | 实现（infra/） | 职责 |
|---|---|---|
| `IJobObject` | `job/JobObjectImpl` | Job 创建/资源限制/级联/会计查询/IOCP 通知/SetUiLimits |
| `IProcessLauncher` | `process/ProcessLauncherImpl` | CreateProcessAsUserW（隔离 token）、管道、stdin、信号、Terminate |
| `ITokenIsolator` | `token/TokenIsolatorImpl` | Low IL 隔离 token 派生（DuplicateTokenEx + SetTokenInformation） |
| `IWriteArea` | `writearea/WriteAreaImpl` | 可写区创建/打 Low 标签/Teardown（%TEMP% 重定向目标） |
| `IWfpEngine` | `wfp/WfpEngineImpl` | SOCKS5 代理 + WFP 网络白名单拦截 |
| `IEtwMonitor` | `etw/EtwMonitorImpl` | ETW 行为监控 |
| `IGlobalQuotaManager` | `globalquota/GlobalQuotaManagerImpl` | 跨进程共享资源池 |
| `ISilo` | `silo/SiloImpl` | Job 就地升级为 Server Silo |
| `ILogger` | `logging/Logger` | spdlog 封装 |
| `IJobNotificationSink` | usecase 实现 | Job 通知回调 |
| `IProcessOutputSink` | usecase 实现 | 输出流回调 |
| `IConfigLoader` | `adapters/ConfigLoader` | JSON 配置加载 |

## 3. 核心领域模型

### 3.1 沙箱单元：per-process 模型

每个被隔离进程是一个独立沙箱单元（区别于"多进程共享一个沙箱"）：

- `SandboxInstance` 持有一个 `processes_` 映射：`process_id（沙箱内部 ID）→ ProcessEntry`
- 每个 `ProcessEntry` 独占一份 Job Object、Launcher、TokenIsolator、WriteArea 等资源（RAII，析构即清理）
- 每个进程有独立的 Job（per-process Job），配额按进程独立计算

### 3.2 资源配额 `ResourceQuota`

单位约定：时间 ms、内存 MB。

| 字段 | 实现机制 |
|---|---|
| `memory_mb` | Job `PROCESS_MEMORY_LIMIT` + `JOB_MEMORY_LIMIT` |
| `cpu_rate_percent` | Job CPU Rate Control（`RATE_CONTROL_ENABLE`，窗口内节流） |
| `cpu_ms` | Job CPU 时间限制（`END_OF_PROCESS_TIME` / `END_OF_JOB_TIME`） |
| `process_count_limit` | Job `ACTIVE_PROCESS_LIMIT` |
| `wall_clock_timeout_ms` | Python 端 `WallClockTimer`（非 Job 机制，见 6.4） |

限制项设置自适应系统版本：不可用的项（如低版本系统不支持 CPU Rate）记 warn 跳过，不返回错误（`JobSetLimitFailed` 除外）。

### 3.3 隔离策略 `IsolationPolicy`

文件系统隔离为 Low IL token 的固有语义（无配置项），网络与剪贴板可配置：

| 字段 | 取值 | 说明 |
|---|---|---|
| `net_policy` | `Unrestricted`（默认）/ `Allowlist` | Unrestricted = 用户 token 天然全通；Allowlist = SOCKS5 代理 + WFP 白名单 |
| `net_allowlist` | `NetworkRule[]` | 白名单规则（仅 `Allowlist` 生效），`{ip, port, protocol}` |
| `clipboard_isolate` | bool（默认 false） | Job UI 限制：剪贴板/全局原子表/系统参数 |

### 3.4 文件系统隔离（Low IL 固有语义）

Low Integrity（S-1-16-4096）token 的 `NO_WRITE_UP` 强制：

| 能力 | 机制 |
|---|---|
| 全盘只读 | IL=Low 进程写任何 Medium(默认) 对象被拒（完整性强制，无需改 ACL） |
| 全盘可读可执行 | 默认无 `NO_READ_UP`，保留用户 SID/组，用户可读文件均可读可执行。**例外**：宿主目录带显式受限 DACL（受控文件夹/安全工具注入的 AppContainer ACE 等）时，Low IL 的读/执行同样被拒（DACL 检查先于 IL 检查）——沙箱内要访问的文件应放普通 ACL 目录 |
| 可写区 | 每进程 `%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable`，打 Low 标签 + 当前用户 (OI)(CI)F，沙箱进程 `%TEMP%`/`%TMP%` 重定向到此 |
| 工作目录 | 请求方未传 working_dir 时默认落到可写区（可读可写） |

`WriteArea::Teardown()` 在进程退出时递归删除；删除失败仅记 warn，由 `StartupCleanup` 启动期兜底扫描清理孤儿会话目录（按 `<os-pid>-<process_id>` 前缀与当前进程 os-pid 不匹配判定）。

### 3.5 回调接口与事件 payload

pybind11 in-process 形态下，事件通过 `SandboxProcess` 的 setter 回调推 Python。回调签名为 `Callable[[dict], None]`，payload 为 dict（pybind11 自动转换 `nlohmann::json` ↔ Python `dict`）。

| 回调 setter | 触发时机 | payload 字段 |
|---|---|---|
| `on_resource_limit` | Job 资源限制命中 | `process_id` / `pid` / `limit_type` / `notification_type` / `limit` / `value` |
| `on_job_process_started` | Job 内子/孙进程创建 | `process_id` / `pid` / `process_name` / `process_path` / `parent_pid?` / `timestamp_ms` |
| `on_job_process_exited` | Job 内子/孙进程退出 | `process_id` / `pid` / `exit_kind` / `exit_code?` / `timestamp_ms` |
| `on_behavior_event` | ETW 行为事件批量上报 | `events`（list[dict]） |
| `on_access_denied` | stderr 命中 AccessDenied 关键字 / ETW access_denied | `pid` / `path` / `operation` / `source` |

stdout/stderr 不走回调，而是通过管道句柄（`proc.stdout_handle` / `proc.stderr_handle`）由 Python 自行 `win_sandbox.read_pipe` 读取，或用 `win_sandbox.drain_stdout` / `drain_stderr` helper 起后台线程排空。

### 3.6 进程状态与退出原因

`ProcessState`：`Pending` → `Running` → `Exited`（自然退出）/ `Terminated`（被沙箱终止）

`ExitReason`：`NormalExit` / `KilledByCpuLimit` / `KilledByMemoryLimit` / `KilledByProcessLimit` / `KilledByTimeout`（墙钟超时）/ `KilledByUser`（手动 Terminate）/ `PipeClosed` / `Unknown`

`proc.wait()` 返回三元组 `(exit_code: int, exit_reason: str, resource_usage: dict)`。

### 3.7 错误码体系（`ErrorCode`）

按模块分组：`Job*`（创建/限制/分配/IOCP）、`Process*`（启动/管道/等待/信号）、`Token*`（隔离 token 派生）、`WriteArea*`（可写区创建/清理）、`Config*`、`Stats*`、`Etw*`、`Silo*`、`GlobalQuota*`、`InvalidArgument` / `InternalError`。所有错误以 `Result<T>` 传递（错误码 + 人类可读消息），pybind11 绑定层将错误转为 Python 异常。

## 4. 进程生命周期

### 4.1 启动流程（`SandboxInstance::StartProcess` → `NativeSandboxedProcess::Execute`）

```
1. 分配 process_id（沙箱内部 ID），填入请求
2. 创建 ProcessEntry（RAII）：
   - JobObjectImpl + ProcessLauncherImpl + TokenIsolatorImpl + WriteAreaImpl
   - 条件创建：net_policy=Allowlist → WfpEngineImpl
3. JobObject::Create（句柄 + kill-on-close 标志）
4. 全局配额前置申请（memory_mb + cpu_rate + 1 进程），超限拒绝启动
5. Server Silo 就地升级（可用时；失败降级为 Job + Low IL，记 warn）
6. Job::SetResourceLimits（自适应版本：不可用项 warn 跳过）
7. Job::RegisterNotificationSink（必须早于 Execute，防丢退出通知）
8. Execute（usecase 内部）：
   a. TokenIsolator::Prepare（DuplicateTokenEx → SetTokenInformation IL=Low）
   b. WriteArea::Create（可写区 + Low 标签）
   c. env 注入：TEMP/TMP = 可写区；working_dir 为空 → 可写区
   d. net_policy=Allowlist → WfpEngine Open + RegisterConnectFilter + 注入
      socks5://127.0.0.1:<port> 到 ALL_PROXY/HTTP_PROXY/HTTPS_PROXY
   e. ProcessLauncher::Launch（CreateProcessAsUserW(隔离 token)）：
      - stdio：stdin 为命名管道（OVERLAPPED 写端，WriteStdin 超时 30s + CancelIoEx 取消），
        stdout/stderr 为匿名管道；子进程端句柄通过 PROC_THREAD_ATTRIBUTE_HANDLE_LIST
        白名单继承（bInheritHandles=TRUE + 列表外句柄不继承，实测确认严格白名单语义）
      - ConPTY 模式：PSEUDOCONSOLE 属性 + STARTF_USESTDHANDLES（空句柄，必须置位，
        CreateProcessAsUserW 下不置位则属性被静默忽略）
   f. Job::AssignProcess —— 失败（如进程已属其他 Job，WinError 5）
      则立即 Terminate + 关闭全部句柄，返回错误
   g. 启动 wait 线程（WaitForExit INFINITE）
   h. 启动 wall_clock 定时器（Python 端 WallClockTimer，若设置配额）
   i. 先返回 SandboxProcess 对象（含句柄），再处理 stdin_data
      （关键顺序：大块 stdin 写入慢，Python 必须先拿到对象防超时）
   j. stdin 处理：interactive → 保留句柄待 Python write_pipe；
      非 interactive → 一次性写入 stdin_data 后关闭（子进程 ReadFile 立即 EOF）
9. 成功后插入 processes_ 映射（锁内二次校验 shutting_down，防 TOCTOU）；
   注册 ETW pid→usecase 路由；构造返回 handle（usecase shared_ptr 拷贝，锁外使用安全）
10. 入口自动 CleanupFinished：清理已退出进程条目（防 processes_ 无限增长/配额占用）
```

异常路径要点：

- **Execute 失败**：先 `Job::Shutdown()` 停通知线程再让 entry 析构，避免 IOCP 回调访问悬垂 sink（见 7.9）
- **shutting_down 期间的新请求**：拒绝，并立即 Terminate 已启动的进程

### 4.2 运行期

- **stdin**：Python 调 `win_sandbox.write_pipe(proc.stdin_handle, data)` 写入保留句柄
- **stdout/stderr**：Python 调 `win_sandbox.read_pipe(proc.stdout_handle)` 读取，或用 `drain_stdout` / `drain_stderr` helper 起后台线程排空；stderr 内容可由 Python 用 `contains_access_denied_keyword` 扫描 AccessDenied 关键字
- **信号**：`proc.signal("ctrl_break")` → `GenerateConsoleCtrlEvent`（模拟 Ctrl+C）
- **Job 通知**：IOCP 线程回调 usecase 的 `OnNotification`（见 7.2），转为 pybind11 回调推 Python

### 4.3 退出流程（`WaitLoop`）

```
WaitForExit(INFINITE) 返回
→ 立即 disarm wall_clock 定时器（Python 端 WallClockTimer.cancel）
→ 读取原子退出原因 pending_exit_reason
→ 更新 exit_code / exit_reason / exit_time / state
→ 关闭 stdin 写句柄
→ Job::QueryAccounting + QueryPeakMemory（峰值内存单独查询）
→ 返回 (exit_code, exit_reason, resource_usage) 三元组
→ 原子关闭进程句柄 → finished = true
```

### 4.4 强制终止与超时

- **手动终止**：`proc.terminate(exit_code)` → `Job::TerminateAll(exit_code)`（KILL_ON_JOB 语义，确保 Job 内子进程全灭，stdout 写端全部关闭，读端才会 EOF）
- **wall_clock 超时**：Python 端 `WallClockTimer` 上下文管理器超时回调 `proc.terminate(1)`。退出原因与手动终止区分
- **信号退出**（Ctrl+C）为请求式退出：wait 线程先等进程自行退出，再兜底 TerminateAll

## 5. 关键机制与技术原理

### 5.1 Job Object 资源限制

- **CPU 速率控制**（`cpu_rate_percent`）：`SetInformationJobObject(JobObjectCpuRateControlInformation)`，RATE_CONTROL 窗口节流，不触发通知、不杀进程，是"平滑限制"
- **CPU 时间**（`cpu_ms`）与**内存**（`memory_mb`）为"硬限制"：触发对应 Job 通知
- **进程数**（`process_count_limit`）：`ACTIVE_PROCESS_LIMIT`，超限在 CreateProcess 阶段被拒绝（WinError 1816），非运行期违规

### 5.2 Job 通知语义（`OnNotification`，IOCP 线程）

Windows 语义：Job 限制通知到达后**只发通知、不自动终止进程**。因此：

| 通知 | 处理 | 退出原因 |
|---|---|---|
| `EndOfJobTime` / `EndOfProcessTime` | 触发 on_resource_limit 回调 + TerminateAll | `KilledByCpuLimit` |
| `ProcessMemoryLimit` / `JobMemoryLimit` | 触发 on_resource_limit 回调 + TerminateAll | `KilledByMemoryLimit` |
| `ActiveProcessLimit` | 仅触发 on_resource_limit 回调，**不** TerminateAll | — |
| `ProcessExit*` / `NewProcess` / `ActiveProcessEmpty` | 仅日志（退出由 wait 线程处理） | — |

`NewProcess` / `ProcessExitNormal` / `ProcessExitAbnormal` / `ProcessExit` 四类通知除日志外，还由 `NativeSandboxedProcess::OnNotification` 透传为 `on_job_process_started` / `on_job_process_exited` 回调（主进程 pid 跳过，避免与 `wait()` 返回重复；`NewProcess` 时经 Toolhelp 尽力填充 `parent_pid`）。

**退出通知去重**：崩溃路径（`DIE_ON_UNHANDLED_EXCEPTION` 生效时）同一进程会先发 `ABNORMAL_EXIT_PROCESS`（msg=8）再发 `EXIT_PROCESS`（msg=7）；IOCP 线程在翻译前用 `exited_pids_` 集合去重，同一 pid 的退出通知仅投递一次（重复消息跳过，不进入退出码查询路径）。

**终止语义**：限额通知（EndOfJobTime/内存）后必须立即 TerminateAll——只发通知不杀进程，超限进程会照常跑完（配额形同虚设）。`ActiveProcessLimit` 例外：它是创建时拒绝语义，Job 内既有进程未违规，不应被终止。

### 5.3 Low IL token 隔离

- `TokenIsolatorImpl`：`OpenProcessToken(当前进程)` → `DuplicateTokenEx(MAXIMUM_ALLOWED, TokenPrimary)` → `SetTokenInformation(TokenIntegrityLevel, S-1-16-4096)`，返回 primary token 供 `CreateProcessAsUserW` 使用
- **plain 单路径**：不 CreateRestrictedToken——restricted token 由非管理员宿主启动必 err=1314；隔离 token 特权集 = 宿主镜像（非管理员仅 5 个无害特权），隔离核心全部来自 IL 完整性强制
- **零特权可用**：token 派生与打标签（SetNamedSecurityInfo(SI_LABEL)）均不需要管理员
- `WriteAreaImpl`：创建会话目录（`%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable`）→ `SetNamedSecurityInfo(LABEL_SECURITY_INFORMATION)` 打 Low 标签 + 追加当前用户 `(OI)(CI)F` → Teardown 递归删除（失败 warn + StartupCleanup 兜底）
- **单向墙**：完整性只防"低写高"；宿主 Medium 写可写区放行（高写低默认规则），为主机侧清理所需，威胁模型已接受
- 剪贴板隔离：`clipboard_isolate=true` → `SetUiLimits(true)`（Job UI 限制，复用现有实现）

### 5.4 Server Silo（条件启用）

- 配置 `silo.enabled=true` 且平台支持（Windows Server / 管理员）时，把 per-process Job 就地升级为 Server Silo（`ISilo::ElevateJob`），获得注册表/命名空间级更强隔离
- 升级失败（`SiloUnavailable` 等）→ 降级继续用 Job + Low IL 隔离，记 warn，不影响启动

### 5.5 全局资源配额

- 配置 `global_quota.enabled=true` 时启用跨进程共享资源池（命名共享内存 + 命名 Mutex）
- 启动进程前**前置申请**（`Acquire`：memory + cpu_rate + 进程数），超限拒绝启动（`GlobalQuotaExceeded`）；进程退出时归还（ProcessEntry 析构）
- 防止多沙箱实例叠加突破单机资源上限
- 池结构含 **32 个实例槽**（token + pid + 心跳时间戳 + 占用计数）；实例崩溃/强杀未归还时，其他实例在 Acquire/Query 时发现心跳超时（60s）即回收其占用（`ReclaimStaleSlotsLocked`），防额度永久泄漏
- 共享内存命名：`pool_name` + 当前用户 SID（DACL 仅授予当前用户）；`CreateFileMapping` 已存在（`ERROR_ALREADY_EXISTS`）时合法复用而非错误

### 5.6 网络拦截（WFP + SOCKS5）

- 仅 `net_policy=Allowlist` 时创建 `WfpEngineImpl`（Open 失败记 Warn 降级：allowlist 不生效，网络不受限，语义等同 unrestricted）；`Unrestricted` 不做任何网络控制（用户 token 天然全通）
- Allowlist 模式：本地 SOCKS5 代理（127.0.0.1:随机端口），按 `net_allowlist` 规则转发/拒绝 HTTP/HTTPS 流量（`socks5://127.0.0.1:<port>` 注入 `ALL_PROXY`/`HTTP_PROXY`/`HTTPS_PROXY`）；非 HTTP 流量（原始 TCP/UDP）不受代理控制；被拦截连接触发 `on_behavior_event` 回调（`NetworkBlocked` 类型）
- **监听时机**：`Open()` 内即完成 WSAStartup + socket/bind(port 0)/listen/getsockname——端口在返回给上层前已确定，消除"先拿端口后监听"的 TOCTOU 窗口
- **连接处理**：每连接一个工作线程（上限 16，超出拒绝并关连接）；上游 connect 非阻塞 + select 10s 超时（上游不可达不挂死工作线程）；IPv6 目标（ATYP=0x04）直接拒绝（不支持）
- **注册防护**：`RegisterConnectFilter` 重复注册返回错误（同一 WFP 引擎不叠加两层过滤）

### 5.7 行为监控（ETW）

- 配置 `monitoring.etw_enabled=true` 时启用：内核会话 + 用户态 provider，`RingBuffer` 缓存事件记录，解析为 `BehaviorEvent` 后批量触发 `on_behavior_event` 回调
- **多实例并存**：回调经 `EVENT_TRACE_LOGFILEW.Context`（`record->UserContext`）路由到具体实例，无进程级静态单例、无全局锁——同一进程多沙箱实例各自独立采集
- **会话命名**：用户态 session 名带实例唯一后缀（`<session>-<pid>-<uid>`，保留 `win-sandbox-etw-` 前缀供 StartupCleanup 识别），多实例不撞名；`NT Kernel Logger` 为系统单例——`StartTraceW` 返回 `ERROR_ALREADY_EXISTS`（已被他人启用）时**不消费、不 STOP、不接管**，置句柄 0 跳过该 session；`StopSession` 仅停止本实例创建成功的 session
- **pid 过滤**：`monitoring.filter_pids` 非空时仅采集指定 pid 的事件（其余进程事件丢弃），降低高负载环境噪声
- **RingBuffer 满**：丢弃新事件并计数（`gap_count` 上报），不覆盖未消费事件
- **schema 缓存**：manifest-based provider 的 TDH schema 按事件 Id 负缓存，避免每事件重复 `TdhGetEventInformation`
- **丢失检测**：`seq` 跳变统计为 `gap_detected` 事件（`gap_count` 字段）

### 5.8 输出管道与 Python 端读取

- 默认缓冲 64KB，`stream_buffer_size` 可覆盖（大块 stdout 测试用；上限 64MB）
- stdout/stderr 管道句柄以 `int` 暴露给 Python（`proc.stdout_handle` / `proc.stderr_handle`），Python 用 `win_sandbox.read_pipe(handle, size)` 自行 `ReadFile`，或用 `win_sandbox.drain_stdout(proc, callback)` / `drain_stderr(proc, callback)` 起后台线程排空（回调签名 `callback(data: bytes) -> None`）
- stdin 写端为**命名管道 + OVERLAPPED**：`WriteStdin` 30s 超时（子进程不读时不会无限阻塞调用线程），进程被杀时 `CancelIoEx` 解除挂起写入；管道名含宿主 pid + 进程级全局原子序号（多 launcher 实例并发启动不撞名）
- 大 stdin_data 场景的关键顺序：先返回 SandboxProcess 对象再写 stdin（见 4.1 步骤 8f）

### 5.9 竞态防护

| 竞态 | 防护 |
|---|---|
| Execute 失败后 entry 析构顺序 usecase→job，usecase 析构 TerminateAll 触发 IOCP 回调悬垂 sink | 失败路径先 `Job::Shutdown()` 停通知线程 |
| wait 线程与 Terminate/Signal 竞争关闭句柄 | 进程句柄原子 load/store，wait 线程独占使用，退出时原子关闭 |
| ShutdownAll 已开始仍插入新进程（"clear 后插入残留"） | `shutting_down_` 标志：锁内二次校验，拒绝新进程并立即终止 |
| info 级日志回显完整命令行（参数可能含口令/令牌） | info 只记可执行路径摘要（RedactCommandLine），完整命令行留 debug 级 |
| 回调执行与 SetOn*/ClearAllCallbacks 并发（回调中途被清空/替换） | 回调注册表用 `cb_mutex_`：invoke 时锁内拷贝 `std::function` 副本，锁外执行；ClearAllCallbacks 持 GIL 清空 |
| shutdown 后 Python 侧 proc 继续 wait/close（资源已释放） | usecase 依赖全部 `shared_ptr` 注入；Python 侧 `PyProcess` 持有 usecase shared_ptr，`FindByProcessId` 返回 shared_ptr，shutdown 不会悬垂 |
| 并发多实例启动 stdin 命名管道同名 | 进程级全局静态原子序号（非 per-launcher 成员），并发启动名称唯一 |

## 6. 线程模型

| 线程 | 归属 | 职责 |
|---|---|---|
| Python 主线程 | Python 调用方 | 创建沙箱、启动进程、读写管道、注册回调 |
| Job IOCP 线程 | JobObjectImpl | 回收 Job 通知 → 回调 usecase::OnNotification → pybind11 回调推 Python |
| wait 线程（每进程） | NativeSandboxedProcess | WaitForExit、退出账务、填 resource_usage |
| ETW consumer 线程（每 session） | EtwMonitorImpl | ProcessTrace 消费 + 回调路由（经 record->UserContext 到本实例） |
| ETW 分发线程（可选） | EtwMonitorImpl | 批量触发 on_behavior_event 回调 |
| SOCKS5 连接线程（allowlist，每连接，上限 16） | WfpEngineImpl | 代理转发/拒绝（上游 connect 10s 超时） |
| drain_stdout/drain_stderr 线程（可选） | Python helper | 后台排空 stdout/stderr 管道 |
| WallClockTimer 线程（可选） | Python 端 | 墙钟超时回调 proc.terminate |
| StatsPoller 线程（可选） | Python 端 | 周期查询 proc.query_accounting |

> pybind11 回调在 IOCP / ETW 分发线程中执行，Python 端回调实现须注意线程安全（GIL 由 pybind11 自动获取）。

## 7. pybind11 绑定与回调架构

- `win_sandbox_native` 模块由 pybind11 编译，暴露 `SandboxInstance` / `SandboxProcess` 类、管道 helper 函数、工具函数
- `SandboxInstance` 持有 C++ `SandboxInstance` 实例（`std::shared_ptr`），方法直调（无 IPC、无序列化）
- `SandboxProcess` 持有 `ProcessEntry` 引用，属性（`process_id` / `pid` / `process_handle` / `stdin_handle` / `stdout_handle` / `stderr_handle`）以 `int` 暴露句柄
- 回调 setter（`on_resource_limit` / `on_job_process_started` / `on_job_process_exited` / `on_behavior_event` / `on_access_denied`）注册 Python callable，C++ 事件触发时 pybind11 自动获取 GIL 调用
- payload 为 `nlohmann::json` → Python `dict` 自动转换（pybind11 内置）
- 管道 helper（`read_pipe` / `write_pipe` / `close_handle` / `wait_process`）直接调用 Win32 `ReadFile` / `WriteFile` / `CloseHandle` / `WaitForSingleObject`
- `drain_stdout` / `drain_stderr` 起后台 `threading.Thread` 循环 `read_pipe`，回调 `callback(data: bytes)`
- `WallClockTimer` / `StatsPoller` 为 Python 端实现的上下文管理器
- 沙箱关闭：`sb.shutdown()` 终止全部进程、清理资源；Python 进程退出时 RAII 析构自动清理

## 8. 配置体系（`SandboxConfig`）

| 段 | 字段 | 默认 |
|---|---|---|
| `logging` | level / dir / retention_days | info / `%LOCALAPPDATA%\win-sandbox\logs`（缺失回退 `%TEMP%\win-sandbox-logs`）/ 7 天 |
| `monitoring` | etw_enabled + EtwConfig（ring_buffer_size / dispatch_batch_size / dispatch_timeout_ms / stats_interval_ms / filter_pids） | false |
| `default_quota` | ResourceQuota（start_process 未指定时回退） | 内置默认 |
| `isolation` | IsolationPolicy（start_process 未指定时回退） | net_policy=Unrestricted + clipboard_isolate=false |
| `silo` | enabled | false |
| `global_quota` | GlobalQuotaConfig | 禁用 |

顶层段白名单：`logging` / `default_quota` / `isolation` / `monitoring` / `silo` / `global_quota`（严格模式，未知段/字段拒绝）。

配置文件加载（`ConfigLoader`）：解析 JSON、展开环境变量（`%LOCALAPPDATA%` 等）、schema 校验（整型回绕/越界拒绝，size 字段 2^40 上界）；无配置文件时 `BuildDefault()`。`log_level` 参数显式传入时覆盖配置文件级别；`SandboxInstance` 构造时同步 `Logger::Configure()` 让配置的 dir/level/retention 生效。

## 9. 部署形态

```
pip install win-sandbox
```

- wheel 内嵌 `win_sandbox_native.pyd`（pybind11 扩展模块，静态链接 spdlog）
- `import win_sandbox` 自动加载 pyd，无子进程、无 IPC、无序列化
- 构建命令：仓库根目录 `.\BUILD.ps1` + `pip install .`（见 [DEPLOYMENT.md](DEPLOYMENT.md)）
- Python 客户端：`win_sandbox` 包（见 `docs/API_REFERENCE.md`）

## 10. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/ARCHITECTURE.md`（本文档） | 架构与技术原理（权威） |
| `docs/API_REFERENCE.md` | Python API 参考 |
| `docs/USER_GUIDE.md` | 用户使用指南 |
| `docs/DEPLOYMENT.md` | 部署与权限要求 |
| `docs/Lessons-Learned.md` | 经验教训 |
