# Windows 沙箱高层设计文档（HLD）

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| 文档类型 | HLD（高层设计） |
| 项目名称 | win-sandbox |
| 版本 | v0.1 |
| 作者 | rikka |
| 创建日期 | 2026-07-26 |
| 状态 | Implemented (Phase 0-7)，全部完成（2026-08-06） |
| 上游文档 | PRD-WindowsSandbox-JobAppContainer-202607261420.md |
| 下游文档 | LLD-01 / LLD-04（已完成）、TDD（已完成） |

---

## 2. 背景与目标

### 2.1 背景

PRD 已确认采用 Job Object + AppContainer 组合实现 Windows 沙箱，叠加 ETW/WFP 用户态监控，通过命名管道被 Python 编排。本 HLD 在 PRD 基础上完成：

- 系统分层与依赖关系
- 模块分解与职责边界
- 关键接口的高层定义
- 数据流与线程模型
- 部署形态与启动流程

### 2.2 设计目标

| 目标 | 说明 |
|------|------|
| 干净架构 | 依赖只能外→内，业务逻辑不依赖 Windows API 或第三方库 |
| 可测试 | 核心用例通过接口注入依赖，可在不调 Win32 的情况下单测 |
| 可扩展 | 新隔离技术（如 Server Silo）可作为新适配器插入，不污染用例 |
| 单进程多角色 | 沙箱进程同时承载 IPC 服务端、监控消费者、进程托管器 |
| 失败安全 | 任何组件异常都不能让被隔离进程逃逸 Job |

### 2.3 设计原则

1. 依赖倒置：用例层定义所需接口（端口），框架层提供实现（适配器）。
2. 单一职责：每个模块只做一件事，Job 归 Job、AppContainer 归 AppContainer。
3. 显式错误传播：HRESULT/NTSTATUS 不在用例层泄漏，框架层翻译为领域错误码。
4. 零降级：按 AGENTS.md，不做兼容/降级；功能不可用即明确失败并报告。
5. 资源 RAII：所有 Win32 句柄用 WIL 包装，禁止裸 CloseHandle。

---

## 3. 系统架构图

### 3.1 整体上下文

```
Python 编排进程
    │  (subprocess.Popen)
    ▼
sandbox.exe (C++ 沙箱进程, 单实例)
    │
    ├─ 命名管道服务端  ←──┐
    │                     │
    ├─ 用例编排层          │ (IPC 帧: 长度前缀+JSON)
    │   ├─ 启动/停止       │
    │   ├─ 进程管理        │
    │   ├─ 监控聚合        │
    │   └─ 策略执行        │
    │                     │
    ├─ Windows 适配层      │
    │   ├─ Job Object      │
    │   ├─ AppContainer    │
    │   ├─ ETW 会话        │
    │   ├─ WFP 引擎        │
    │   ├─ 文件系统重定向  │
    │   └─ 命名管道         │
    │                     │
    ▼                     │
Windows Kernel
    ├─ Process/Job subsystem
    ├─ AppContainer (integrity check)
    ├─ ETW providers
    ├─ WFP layers
    └─ NTFS
```

### 3.2 干净架构分层（洋葱模型）

```
┌─────────────────────────────────────────────────────────┐
│ 4. 框架与驱动层 (Frameworks & Drivers)                   │
│   main.cpp / spdlog / nlohmann::json / WIL              │
│   JobObjectImpl / AppContainerImpl / EtwSessionImpl     │
│   WfpEngineImpl / NamedPipeServerImpl / ProcessLauncher │
├─────────────────────────────────────────────────────────┤
│ 3. 接口适配器层 (Interface Adapters)                     │
│   IpcMessageAdapter / ConfigLoader / EventSerializer    │
│   PolicyRepository / Win32ErrorHandler                  │
├─────────────────────────────────────────────────────────┤
│ 2. 用例层 (Use Cases)                                    │
│   StartSandbox / StopSandbox / StartProcess             │
│   ManageProcess / MonitorBehavior / EnforcePolicy       │
│   ReportStats / ShutdownAll                             │
├─────────────────────────────────────────────────────────┤
│ 1. 实体层 (Entities)                                     │
│   SandboxInstance / SandboxedProcess / ResourceQuota    │
│   IsolationPolicy / BehaviorEvent / IpcMessage          │
└─────────────────────────────────────────────────────────┘

依赖方向: 外层 ──► 内层  (内层不依赖外层)
```

### 3.3 进程拓扑

```
┌─ Python 进程 ──────────────────────────────────────┐
│  sandbox.Client  ──(命名管道)──┐                   │
└─────────────────────────────────│───────────────────┘
                                  │
┌─ sandbox.exe (沙箱进程) ────────▼───────────────────┐
│  Main ─► IpcServer ─► UseCaseOrchestrator           │
│                          │                          │
│         ┌────────────────┼────────────────┐         │
│         ▼                ▼                ▼         │
│   JobObject         AppContainer      EtwMonitor     │
│   (holds N procs)   (token/sid)       (events)      │
│         │                │                │         │
│         ▼                ▼                ▼         │
│   ┌─ Sandboxed Process #1 ────────────────────┐     │
│   │  cmd.exe (AppContainer token, in Job)     │     │
│   └───────────────────────────────────────────┘     │
│   ┌─ Sandboxed Process #2 ────────────────────┐     │
│   │  python.exe (AppContainer token, in Job)  │     │
│   └───────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────┘
```

---

## 4. 模块分解

### 4.1 实体层（Entities）— src/core/entities/

| 实体 | 职责 | 关键字段 |
|------|------|----------|
| SandboxInstance | 沙箱实例聚合根 | instance_id、permission_mode、state、started_at |
| SandboxedProcess | 被隔离进程聚合 | pid、sandbox_pid、command_line、exit_code、io_channels |
| ResourceQuota | 资源配额值对象 | cpu_ms、memory_mb、io_rate、max_processes |
| IsolationPolicy | 隔离策略值对象 | fs_mode、net_policy、capabilities、path_rules |
| BehaviorEvent | 行为事件值对象 | type、pid、timestamp、payload、seq |
| IpcMessage | IPC 消息值对象 | type、request_id、payload |
| CapabilityReport | 当前能力集快照 | effective_permissions、enabled_features、disabled_features |

约束：实体层无外部依赖，纯 C++ 标准库。

### 4.2 用例层（Use Cases）— src/core/usecases/

| 用例 | 输入 | 输出 | 依赖接口（端口） |
|------|------|------|------------------|
| StartSandbox | SandboxConfig | SandboxInstance | IJobObject、IAppContainer、IIpcServer、IPermissionDetector |
| StopSandbox | instance_id | void | IJobObject、IAppContainer、IIpcServer、IEtwMonitor、IWfpEngine |
| StartProcess | StartProcessRequest | SandboxedProcess | IProcessLauncher、IJobObject、IFileSystemIsolator |
| ManageProcess | WriteStdin/Signal | void | IProcessLauncher |
| MonitorBehavior | 订阅请求 | BehaviorEvent 流 | IEtwMonitor、IWfpEngine、IStatsCollector |
| EnforcePolicy | IsolationPolicy | void | IJobObject、IAppContainer、IFileSystemIsolator、INetworkIsolator |
| ReportStats | 触发（定时/退出） | StatsReport | IStatsCollector、IJobObject |
| HandleIpcCommand | IpcMessage | IpcMessage | 调度到上述用例 |

约束：用例层只依赖实体与接口（端口），不依赖 Windows API 或第三方库。

### 4.3 接口适配器层（Interface Adapters）— src/adapters/

| 适配器 | 职责 |
|--------|------|
| IpcMessageAdapter | 命名管道字节流 ↔ IpcMessage 序列化/反序列化（JSON） |
| ConfigLoader | config.json → SandboxConfig 领域对象，含 schema 校验 |
| EventSerializer | BehaviorEvent → IPC 事件消息 |
| PolicyRepository | 持久化与查询当前 IsolationPolicy |
| Win32ErrorTranslator | HRESULT/NTSTATUS → 领域 ErrorCode |
| PermissionDetector | 检测 TokenElevation，输出 PermissionMode 与 CapabilityReport |

### 4.4 框架与驱动层（Frameworks & Drivers）— src/infra/

#### 4.4.1 Windows API 封装模块

| 模块 | 文件 | 实现接口 | 关键 Win32 API |
|------|------|----------|----------------|
| Job Object | infra/job/JobObjectImpl.cpp | IJobObject | CreateJobObject、SetInformationJobObject、AssignProcessToJobObject、TerminateJobObject |
| AppContainer | infra/appcontainer/AppContainerImpl.cpp | IAppContainer | CreateAppContainerToken、DeriveAppContainerSidFromAppContainerName、GrantSidAccess |
| Process Launcher | infra/process/ProcessLauncherImpl.cpp | IProcessLauncher | CreateProcessAsUserW、CreatePipe、ReadFile/WriteFile |
| ETW Session | infra/etw/EtwSessionImpl.cpp | IEtwMonitor | StartTrace、EnableTraceEx2、OpenTrace/ProcessTrace、EVENT_RECORD 回调 |
| WFP Engine | infra/wfp/WfpEngineImpl.cpp | IWfpEngine | FwpmEngineOpen0、FwpmFilterAdd0、FwpmCalloutAdd0（用户态 ALE 层） |
| File System Isolator | infra/fs/FileSystemIsolatorImpl.cpp | IFileSystemIsolator | GrantSidAccess、NTFS Junction、CopyFile、zip 归档 |
| Named Pipe Server | infra/ipc/NamedPipeServerImpl.cpp | IIpcServer | CreateNamedPipeW、ConnectNamedPipe、ReadFile/WriteFile、ImpersonateClient |
| Stats Collector | infra/stats/StatsCollectorImpl.cpp | IStatsCollector | QueryInformationJobObject（会计信息） |

#### 4.4.2 入口与第三方

| 模块 | 文件 | 职责 |
|------|------|------|
| main.cpp | src/main.cpp | 解析命令行、加载配置、组装依赖、启动 IpcServer |
| Logging | infra/logging/Logger.cpp | spdlog 封装，初始化日志文件与级别 |
| JsonCodec | infra/json/JsonCodec.cpp | nlohmann::json 封装 |

---

## 5. 核心技术选型与理由

### 5.1 选型总表

| 决策点 | 选型 | 理由 | 拒绝的备选 |
|--------|------|------|------------|
| 隔离组合 | Job + AppContainer | Job 管资源、AppContainer 管访问，互补覆盖 PRD 全部隔离维度 | 单 Job（不防文件访问）、单 AppContainer（不限 CPU/内存）、Hyper-V（启动慢） |
| 进程创建 | CreateProcessAsUserW + AppContainer Token | AppContainer 必须用受限 Token 创建进程 | CreateProcessW（无法指定 AppContainer Token） |
| Job 通知 | I/O 完成端口（IOCP）绑定 Job | 异步接收 JOB_OBJECT_MSG_*，避免轮询 | 轮询 QueryInformationJobObject（延迟高） |
| AppContainer SID | DeriveAppContainerSidFromAppContainerName + 唯一实例后缀 | 保证多实例不冲突 | 硬编码 SID（不可多实例） |
| 文件系统写隔离 | NTFS Junction + 路径白名单授予 | 用户态可达，无需驱动；Junction 对被隔离程序透明 | minifilter 驱动（需签名，超出用户态边界）；纯复制（无重定向语义） |
| 网络拦截 | WFP 用户态 ALE callout（FWPM_LAYER_ALE_AUTH_CONNECT_V4/V6） | 用户态可注册，无需驱动签名 | LSP（已废弃）、TDI（已废弃）、TUN/TAP（用户态但需虚拟网卡） |
| 行为监控 | ETW 内核会话 + ETW 用户态 provider | 内核态事件权威且低开销 | API hook（仅本进程，无法跨进程）、minifilter（需驱动） |
| IPC 通道 | 命名管道 + 长度前缀 + JSON | Windows 原生、安全描述符可控、跨语言友好 | 本地 TCP（占端口、防火墙干扰）、stdin/stdout JSON（双向能力弱） |
| 序列化 | nlohmann::json | header-only、API 友好、调试可读 | protobuf（需 codegen，过度工程）、MessagePack（二进制不可读） |
| 日志 | spdlog | 异步、结构化、性能高、Header-mostly | glog（同步阻塞）、自研（违反 AGENTS.md"可用第三方库"原则） |
| Win32 封装 | WIL | 微软官方、header-only、RAII 句柄 | 自研 wil（重复造轮子） |
| 构建 | CMake ≥ 3.20 | 现代 C++ 标准、跨 IDE | MSBuild vcxproj（绑定 VS）、裸 cl（无依赖管理） |

### 5.2 关键技术决策详述

#### 5.2.1 为什么用 Job + AppContainer 而非 Server Silo

| 维度 | Job + AppContainer | Server Silo |
|------|--------------------|-------------|
| 隔离强度 | 中高（资源 + 访问控制） | 高（进程/文件/注册表全隔离） |
| 内核交互 | 纯用户态 | 需 NtCreateJobObject + Silo 特权，部分 API 未文档化 |
| 部署门槛 | 无驱动 | 需 Signer 角色，签名要求高 |
| 兼容性 | Win8+ | Win10 1709+ 且部分功能受限 |
| 决策 | 采用（满足 PRD"用户态上限"） | 列为 Phase 2 候选（TBD，见 PRD §9.2） |

#### 5.2.2 AppContainer Token 创建链路

```
DeriveAppContainerSidFromAppContainerName(L"win-sandbox-<id>")
        │
        ▼
CreateAppContainerProfile(moniker, display_name, description, capabilities, ...)
        │  返回 SID + Profile 路径
        ▼
CreateAppContainerToken(sid, ...)  → 受限 Token
        │
        ▼
GrantSidAccess(sid, path, access_mask)  ← 为每个白名单路径授予权限
        │
        ▼
CreateProcessAsUserW(token, ..., STARTUPINFO)  ← 启动被隔离进程
```

#### 5.2.3 ETW 会话架构

```
StartTrace(session_handle, "win-sandbox-etw-<id>")
        │
        ▼
EnableTraceEx2(session_handle, GUID, ...)  ← 启用多个 provider:
        │   ├─ NT Kernel Logger (Process/Thread/Image/File/Registry)
        │   ├─ Microsoft-Windows-Kernel-Network (TcpIp/UdpIp)
        │   └─ Microsoft-Windows-Kernel-File
        ▼
OpenTrace(&log_file)  → real-time session
        ▼
ProcessTrace(&handle, 1, ...)  ← 阻塞线程,EventRecordCallback 收事件
        │
        ▼
EventRecordCallback(EVENT_RECORD*)  → 解析 → BehaviorEvent → 环形缓冲 → IPC 发送
```

---

## 6. 数据流与调用链

### 6.1 启动流程（Python 发起）

```
Python: subprocess.Popen(["sandbox.exe", "--pipe", "\\\\.\\pipe\\win-sandbox-1",
                          "--config", "config.json"])
   │
   ▼
sandbox.exe main()
   ├─ ParseCommandLine()
   ├─ ConfigLoader::Load(config_path) → SandboxConfig
   ├─ PermissionDetector::Detect() → PermissionMode, CapabilityReport
   ├─ Logger::Init(config.logging)
   ├─ 组装依赖:
   │     JobObjectImpl, AppContainerImpl, NamedPipeServerImpl,
   │     EtwSessionImpl, WfpEngineImpl, ...
   ├─ StartSandboxUseCase::Execute(config)
   │     ├─ JobObjectImpl::Create()  → Job handle (kill-on-close)
   │     ├─ AppContainerImpl::CreateProfile(sid)  → AppContainer token
   │     ├─ FileSystemIsolatorImpl::SetupTempWorkspace()
   │     ├─ EtwSessionImpl::Start()
   │     ├─ WfpEngineImpl::RegisterFilters()  (if network policy != full)
   │     └─ 发送 Ready 事件
   └─ IpcServer::Run()  ← 阻塞,开始接受命令
```

### 6.2 启动被隔离进程（StartProcess 命令）

```
Python ──► WriteStdin 帧 ──► NamedPipeServer
                                  │
                                  ▼
                          IpcMessageAdapter::Deserialize
                                  │
                                  ▼
                       HandleIpcCommandUseCase
                                  │
                                  ▼
                       StartProcessUseCase::Execute(req)
                                  │
              ┌───────────────────┼────────────────────┐
              ▼                   ▼                    ▼
   EnforcePolicy        ProcessLauncher::Create     JobObject::Assign
   (授予白名单路径)      (AppContainer Token)        (加入 Job)
                                  │
                                  ▼
                       启动 stdout/stderr 读线程
                                  │
                                  ▼
                       发送 ProcessStarted 事件
                                  │
                                  ▼
                       (异步) stdout 数据 ─► ProcessOutput 事件 ─► Python
```

### 6.3 行为事件流（ETW → Python）

```
Windows Kernel ──► ETW Session ──► ProcessTrace 线程
                                        │
                                        ▼
                                EventRecordCallback
                                        │
                                        ▼
                                解析为 BehaviorEvent
                                        │
                                        ▼
                                EventRingBuffer (无锁 SPSC)
                                        │
                                        ▼
                                EventSerializer (→ IpcMessage)
                                        │
                                        ▼
                                IpcServer ──► Python
```

### 6.4 关闭流程（Shutdown 命令）

```
Python ──► Shutdown 帧
              │
              ▼
   StopSandboxUseCase::Execute()
       ├─ TerminateJobObject()  ← 级联杀死所有被隔离进程
       ├─ EtwSession::Stop()
       ├─ WfpEngine::UnregisterFilters()
       ├─ FileSystemIsolator::Teardown() (archive/discard 临时区)
       ├─ AppContainer::DeleteProfile()
       ├─ 发送 ShutdownComplete 事件
       └─ IpcServer::Close() → main 退出
```

### 6.5 异常路径：沙箱进程被 taskkill /F

```
Python ──► taskkill /F /PID <sandbox>
              │
              ▼
   sandbox.exe 进程句柄关闭
              │
              ▼
   Job Object 句柄计数归零 → 内核自动
       TerminateJobObject()  ← 所有被隔离进程级联终止
              │
              ▼
   AppContainer Profile 残留 (下次启动清理或手工清理)
```

---

## 7. 关键接口定义（高层）

> 接口在用例层定义（端口），框架层提供实现（适配器）。完整签名见 LLD。

### 7.1 IJobObject

```cpp
class IJobObject {
public:
    virtual Result<void> Create() = 0;
    virtual Result<void> AssignProcess(HANDLE process) = 0;
    virtual Result<void> SetResourceLimits(const ResourceQuota&) = 0;
    virtual Result<void> SetUiLimits(bool no_ui) = 0;
    virtual Result<void> TerminateAll(ExitCode) = 0;
    virtual Result<JobAccountingInfo> QueryAccounting() const = 0;
    virtual Result<void> RegisterCompletionPort(IJobNotificationSink&) = 0;
};
```

### 7.2 IAppContainer

```cpp
class IAppContainer {
public:
    virtual Result<AppContainerProfile> CreateProfile(
        const std::wstring& moniker,
        const std::vector<Capability>& capabilities) = 0;
    virtual Result<TokenHandle> CreateToken(const AppContainerProfile&) = 0;
    virtual Result<void> GrantPathAccess(
        const AppContainerProfile&,
        const std::wstring& path,
        AccessMask) = 0;
    virtual Result<void> DeleteProfile(const AppContainerProfile&) = 0;
};
```

### 7.3 IProcessLauncher

```cpp
class IProcessLauncher {
public:
    virtual Result<SandboxedProcess> Launch(
        const std::wstring& command_line,
        const std::wstring& working_dir,
        const EnvironmentBlock& env,
        TokenHandle appcontainer_token,
        bool interactive) = 0;
    virtual Result<void> WriteStdin(Pid, bytes_view) = 0;
    virtual Result<void> Signal(Pid, SignalType) = 0;
    virtual Result<void> Terminate(Pid, ExitCode) = 0;
};
```

### 7.4 IIpcServer

```cpp
class IIpcServer {
public:
    virtual Result<void> Start(const std::wstring& pipe_name) = 0;
    virtual Result<void> SendEvent(const IpcMessage&) = 0;
    virtual Result<void> BroadcastEvent(const IpcMessage&) = 0;
    virtual void SetCommandHandler(ICommandHandler&) = 0;
    virtual Result<void> Shutdown() = 0;
};
```

### 7.5 IEtwMonitor

```cpp
class IEtwMonitor {
public:
    virtual Result<void> Start(const EtwConfig&) = 0;
    virtual Result<void> Subscribe(IBehaviorEventSink&) = 0;
    virtual Result<void> Stop() = 0;
};
```

### 7.6 IWfpEngine

> **⚠️ 实现状态：未实现。** Phase 5 放弃 WFP 方案，改用 AppContainer capability 实现网络隔离。此接口保留为设计参考，如后续需要细粒度网络白名单（IP/port 级别）可重新引入。

```cpp
class IWfpEngine {
public:
    virtual Result<void> Open() = 0;
    virtual Result<void> RegisterConnectFilter(
        const std::vector<NetworkRule>& allowlist,
        IBlockedConnectionSink&) = 0;
    virtual Result<void> UnregisterAll() = 0;
    virtual Result<void> Close() = 0;
};
```

### 7.7 IFileSystemIsolator

```cpp
class IFileSystemIsolator {
public:
    virtual Result<void> Setup(
        const FileSystemConfig&,
        const AppContainerProfile&) = 0;
    virtual Result<std::wstring> TranslatePath(const std::wstring& virtual_path) = 0;
    virtual Result<void> Archive(const std::wstring& dest_zip) = 0;
    virtual Result<void> Discard() = 0;
    virtual Result<void> Teardown() = 0;
};
```

### 7.8 IStatsCollector

```cpp
class IStatsCollector {
public:
    virtual Result<void> Start(std::chrono::milliseconds interval) = 0;
    virtual Result<StatsReport> Snapshot() const = 0;
    virtual Result<void> Stop() = 0;
};
```

---

## 8. 线程模型

### 8.1 线程清单

| 线程 | 数量 | 职责 | 阻塞点 |
|------|------|------|--------|
| Main | 1 | 启动、组装依赖、IpcServer.Run | IpcServer 阻塞 |
| IPC Read | N（每客户端 1） | 读取命令帧 | ReadFile |
| IPC Write | 1（共享） | 串行化事件发送，避免交错 | WriteFile |
| Job IOCP Wait | 1 | 接收 Job 通知（内存超限、超时、退出） | GetQueuedCompletionStatus |
| ETW Process | 1 | ProcessTrace 阻塞，回调吐事件 | ProcessTrace |
| ETW Dispatch | 1 | 从环形缓冲取事件派发到 IPC | 条件变量 |
| Stats Timer | 1 | 周期查询 Job 会计，发 StatsReport | WaitForSingleObject 定时 |
| Process IO Reader | 2M（M 个进程，每个 stdout/stderr 各 1） | 读被隔离进程输出 | ReadFile |

### 8.2 同步策略

| 资源 | 同步原语 |
|------|----------|
| 被隔离进程表（pid → SandboxedProcess） | std::shared_mutex 读写锁 |
| IPC 写串行化 | std::mutex |
| ETW 事件环形缓冲 | 无锁 SPSC 队列（单生产者=ETW 回调线程，单消费者=Dispatch 线程） |
| 行为事件订阅者列表 | std::shared_mutex |
| Job 句柄 | WIL unique_handle，无需额外同步 |

### 8.3 关闭顺序

1. 停止接受新 IPC 连接。
2. TerminateJobObject 杀所有被隔离进程。
3. 等 Process IO Reader 线程读到 EOF 退出。
4. EtwSession::Stop（让 ProcessTrace 返回）。
5. WfpEngine::UnregisterAll。
6. FileSystemIsolator::Teardown（archive/discard）。
7. AppContainer::DeleteProfile。
8. 发 ShutdownComplete，关闭 IPC。
9. main 返回。

---

## 9. 非功能设计

### 9.1 可用性

- 沙箱崩溃：Job 的 kill-on-close 保证被隔离进程不逃逸。
- ETW 会话泄漏：StopSandbox 必调 ControlTrace(EVENT_TRACE_CONTROL_STOP)；进程异常退出时由下次启动扫描清理。
- AppContainer Profile 残留：启动时扫描 %LOCALAPPDATA%\Packages\win-sandbox-*，清理无主实例。

### 9.2 可扩展性

| 扩展点 | 方式 |
|--------|------|
| 新隔离技术（如 Server Silo） | 新增 ISiloIsolator 接口与实现，用例组合调用 |
| 新 IPC 协议（如本地 socket） | 新增 IStreamIpcServer 实现 |
| 新事件源（如 API hook） | 实现 IBehaviorEventSink，注入 MonitorBehaviorUseCase |
| Python 包升级 | IPC 协议带 version 字段，向前兼容检测 |

### 9.3 安全设计

| 威胁 | 缓解 |
|------|------|
| Python 端被攻陷注入恶意命令 | 所有 IPC 命令做 schema 校验；command_line 不允许超出 execute_paths 白名单 |
| 被隔离程序逃逸 Job | breakaway_ok=false；子进程默认加入 Job |
| 被隔离程序通过命名管道逃逸 | AppContainer 默认拒所有 \\.\pipe\；仅授予沙箱内部管道 |
| 沙箱进程被注入 DLL | （可选）ProcessSignaturePolicy 仅允许微软签名 DLL 加载 |
| 配置文件被篡改 | 配置 schema 严格校验，越权配置直接拒绝启动 |
| 命名管道被其他用户连接 | DACL 限制为当前用户 SID |

### 9.4 可观测性（沙箱自身）

- spdlog 日志：trace/debug/info/warn/error，按天滚动，保留 7 天。
- 关键日志点：启动参数、权限检测、Job/AppContainer 创建、IPC 命令摘要、错误 HRESULT。
- 自身崩溃：SetUnhandledExceptionFilter 写 minidump 到日志目录。

---

## 10. 部署架构

### 10.1 单机部署

```
C:\Program Files\win-sandbox\
    ├─ bin\
    │   ├─ sandbox.exe              # 沙箱主进程
    │   └─ win_sandbox_postmortem.exe  # 崩溃后清理工具(可选)
    ├─ lib\
    │   └─ (无,静态链接)
    ├─ config\
    │   └─ default.json             # 默认配置模板
    ├─ logs\                        # 运行日志
    └─ symbols\                     # PDB(开发期)

Python 端:
    site-packages\win_sandbox\
        ├─ __init__.py
        ├─ client.py                # SandboxClient
        ├─ protocol.py              # IPC 帧/消息定义
        └─ exceptions.py
```

### 10.2 启动方式

| 方式 | 命令 | 适用 |
|------|------|------|
| Python 子进程 | subprocess.Popen([...]) | 主推，每个 Python 进程对应一个沙箱 |
| 命令行直接启动 | sandbox.exe --pipe ... --config ... | 调试 |
| 服务化 | Windows Service 包装（Phase 2） | 多 Python 客户端共享 |

### 10.3 配置加载优先级

1. 命令行 --config <path> 指定的文件（最高）。
2. %LOCALAPPDATA%\win-sandbox\config.json（用户级默认）。
3. C:\Program Files\win-sandbox\config\default.json（系统级默认）。
4. 内置硬编码默认（最低，仅在以上都缺失时）。

---

## 11. 风险与开放决策

### 11.1 设计风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| ETW ProcessTrace 单线程瓶颈 | 高频事件下 dispatch 滞后 | 多 session 分流（内核 / 网络 / 文件分三 session）+ 无锁队列 |
| AppContainer Profile 创建慢（首次） | 启动延迟超 1s | Profile 复用：同 instance_id 重启跳过创建 |
| WFP 用户态 callout 阻塞网络 | 网络延迟飙升 | callout 设置短超时（默认 100ms），超时按策略放行/拒绝 |
| 多个被隔离进程 stdout 阻塞读线程 | 进程挂起导致读线程不退出 | 异步 ReadFileEx + 超时检测 |

### 11.2 开放决策（待 TDD 决议）

| ID | 议题 | 候选 | 决策时机 |
|----|------|------|----------|
| O1 | 路径重定向是否引入 minifilter | 用户态 Junction（M4） / minifilter（Phase 2） | M4 评审 |
| O2 | Server Silo 作为更强隔离层 | 不引入 / Phase 2 引入 | M7 后评估 |
| O3 | 是否提供沙箱服务化模式 | 子进程模式 / Windows Service 模式 | 多客户端需求出现时 |
| O4 | IPC 协议是否引入 schema registry | 硬编码消息类型 / 动态 schema | M3 评审 |
| O5 | Python 包发布形式 | wheel / 单文件 / 源码 | M8 |

---

## 12. 附录

### 12.1 目录结构（建议）

```
win-sandbox/
├─ CMakeLists.txt
├─ cmake/                      # CMake 辅助脚本
├─ third_party/
│   ├─ wil/                    # git submodule
│   ├─ nlohmann_json/          # git submodule
│   └─ spdlog/                 # git submodule
├─ include/                    # 公共头(对外暴露的最小接口)
├─ src/
│   ├─ main.cpp
│   ├─ core/                   # 实体 + 用例(无外部依赖)
│   │   ├─ entities/
│   │   ├─ usecases/
│   │   └─ ports/              # 接口定义(IJobObject 等)
│   ├─ adapters/               # 适配器
│   └─ infra/                  # 框架与驱动层
│       ├─ job/
│       ├─ appcontainer/
│       ├─ process/
│       ├─ etw/
│       ├─ wfp/
│       ├─ fs/
│       ├─ ipc/
│       ├─ stats/
│       ├─ logging/
│       └─ json/
├─ python/
│   └─ win_sandbox/
├─ tests/
│   ├─ unit/
│   ├─ integration/
│   └─ e2e/
├─ docs/
│   ├─ PRD-WindowsSandbox-JobAppContainer-*.md
│   ├─ HLD-WindowsSandbox-Architecture-*.md  ← 本文档
│   ├─ LLD-01-JobObject-*.md / LLD-04-IPC-*.md  (已完成)
│   ├─ TDD-WindowsSandbox-Decisions-*.md  (已完成)
│   ├─ Phase-0..7-*.md  (全部完成)
│   └─ USER_GUIDE / API_REFERENCE / DEPLOYMENT / TROUBLESHOOTING  (已完成)
└─ AGENTS.md
```

### 12.2 CMake 工程结构（高层）

```cmake
cmake_minimum_required(VERSION 3.20)
project(win-sandbox CXX)
set(CMAKE_CXX_STANDARD 17)

# 子模块
add_subdirectory(third_party/wil)
add_subdirectory(third_party/nlohmann_json)
add_subdirectory(third_party/spdlog)

# 库目标(干净架构分层)
add_library(win_sandbox_core STATIC src/core/...)        # 实体+用例+端口
add_library(win_sandbox_adapters STATIC src/adapters/...)
add_library(win_sandbox_infra STATIC src/infra/...)       # 依赖 core + adapters

target_link_libraries(win_sandbox_infra
    PRIVATE
        win_sandbox_core win_sandbox_adapters
        wil::wil nlohmann_json::nlohmann_json spdlog::spdlog
        userenv advapi32 kernel32 fwpuclnt ntdll)

# 可执行
add_executable(sandbox src/main.cpp)
target_link_libraries(sandbox PRIVATE win_sandbox_infra)

# 测试
enable_testing()
add_subdirectory(tests)
```

### 12.3 后续 LLD 拆分计划

| LLD 文档 | 范围 |
|----------|------|
| LLD-01-JobObject | Job 接口完整签名、IOCP 通知处理、限制项映射 |
| LLD-02-AppContainer | Profile 创建/删除、Token 生成、Capability 列表、GrantSidAccess 矩阵 |
| LLD-03-ProcessLauncher | CreateProcessAsUserW 参数、stdin/stdout 管道设计、信号模拟 |
| LLD-04-IPC | 帧格式、消息 schema、多客户端、DACL、错误码 |
| LLD-05-FileSystem | 4 模式详细流程、Junction 实现、archive 策略 |
| LLD-06-Network-WFP | WFP filter 注册、ALE 层处理、超时策略 |
| LLD-07-ETW-Monitor | session 配置、provider 列表、EVENT_RECORD 解析、ring buffer |
| LLD-08-Threading-Shutdown | 线程生命周期、关闭顺序、异常路径 |
