# Windows 沙箱（Job + AppContainer）产品需求文档

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| 文档类型 | PRD（产品需求文档，技术向） |
| 项目名称 | win-sandbox |
| 版本 | v0.1 |
| 作者 | rikka |
| 创建日期 | 2026-07-26 |
| 状态 | Draft，待评审 |
| 关联文件 | `AGENTS.md`、`docs/` 下后续 HLD/LLD |

---

## 2. 背景与目标

### 2.1 背景

在 Windows 平台上执行不可信代码、隔离普通应用、分析可疑样本等场景，需要一个**可在 Python 中编排**的、**隔离强度可配置**的通用沙箱。原生方案各有局限：

- **Job Object**：能限制 CPU/内存/IO/进程数，但**无法阻止文件系统访问**。
- **AppContainer**：能基于能力（Capability）隔离文件/注册表/网络，但**配置复杂**，且不能限制 CPU/内存等资源。
- **WSL/Hyper-V**：隔离强但**启动慢、资源占用大**，不适合细粒度任务编排。
- **Sandboxie** 等第三方方案：闭源或重定向层过重。

单一技术无法覆盖"全面沙箱"诉求，需要 **Job Object + AppContainer 组合**，并叠加 ETW/WFP 等可观测性手段，把用户态能做到的隔离强度推到上限。

### 2.2 目标

构建一个 **C++ 实现的 Windows 沙箱运行时**，具备：

1. **组合隔离**：Job Object（资源限制）+ AppContainer（访问控制）+ 路径白名单 + 网络拦截。
2. **全面可观测**：进程/句柄、文件/注册表、网络、资源统计四类事件全量采集。
3. **Python 编排**：沙箱作为独立子进程运行，Python 通过**命名管道**与之通信，支持事件流回调与双向交互。
4. **自适应权限**：根据启动时权限自动调整能力集（管理员模式启用完整功能，普通用户模式优雅降级）。
5. **可配置**：隔离强度、资源上限、可访问路径、网络策略等均通过配置文件/IPC 消息动态指定。

### 2.3 非目标（Out of Scope）

- 不做内核态驱动（minifilter/WFP callout driver 等以**用户态可达上限**为边界）。
- 不做跨平台（仅 Windows 10 1809+）。
- 不做 GUI（沙箱本体是无头服务，GUI 由前端另行开发）。
- 不替代 Hyper-V/WSL 级别的虚拟化隔离。

---

## 3. 用户角色与场景

### 3.1 角色

| 角色 | 描述 | 主要诉求 |
|------|------|----------|
| Python 编排者 | 通过 Python 脚本调用沙箱的开发者 | 简洁 API、事件流、可控超时、可双向交互 |
| 沙箱运维者 | 部署/配置沙箱服务的运维 | 清晰日志、权限说明、可诊断 |
| 被隔离程序 | 在沙箱内运行的任意 Win32/控制台程序 | （从沙箱视角）受限但不破坏正常 I/O 语义 |

### 3.2 核心场景

**场景 A：OJ 评测 / 不可信代码执行**
- Python 提交一段代码（编译后的 exe 或脚本解释器）到沙箱，限时 5s、内存 256MB、禁网。
- 沙箱回收 stdout/stderr，超时自动杀死，返回退出码与资源统计。

**场景 B：交互式 REPL / 长跑服务**
- Python 启动一个长跑进程（如 `python.exe` REPL），通过 IPC 双向发送 stdin、接收 stdout 事件流。
- 进程可在沙箱内自由创建子进程，但都被 Job 级联管控。

**场景 C：恶意样本行为分析**
- Python 投递可疑样本到沙箱，开启全量监控。
- 沙箱记录所有文件/注册表/网络访问事件，结束后输出结构化行为报告。
- 样本无法逃逸到宿主文件系统。

**场景 D：CI 批量任务隔离**
- 多个独立任务并行跑在各自沙箱中，互不干扰，资源配额独立。

---

## 4. 功能需求

### 4.1 沙箱生命周期管理

**FR-1.1 启动沙箱实例**
- 作为 Python 子进程启动（`sandbox.exe --pipe <name> --config <path>`）。
- 启动时检测自身权限（TokenElevation），决定能力集。
- 创建独占命名管道（名称由 Python 端指定，避免冲突）。
- 完成初始化后向 Python 端发送 `Ready` 事件。

**FR-1.2 停止沙箱实例**
- Python 端发送 `Shutdown` 命令：级联终止 Job 内所有进程，清理 AppContainer profile，关闭管道，退出。
- 沙箱进程被外部杀死时：Job Object 自动级联杀死所有被隔离进程（Job 的 kill-on-close 特性）。

**验收标准**
- 启动后 1s 内发出 `Ready`。
- `Shutdown` 后所有子进程在 500ms 内退出。
- 沙箱进程被 `taskkill /F` 后，Job 内进程全部终止，无残留。

---

### 4.2 Job Object 资源限制

**FR-2.1 资源配额可配置**

| 维度 | 配置项 | 实现方式 |
|------|--------|----------|
| CPU 时间 | `cpu_ms`（单进程总时间）、`cpu_rate_percent`（CPU 占比上限） | `JobObjectLimit`（`JOB_OBJECT_LIMIT_CPU_RATE_CONTROL` + `JobObjectCpuRateControl`） |
| 内存 | `memory_mb`（提交内存上限）、`peak_memory_mb` | `JOB_OBJECT_LIMIT_PROCESS_MEMORY` / `JOB_OBJECT_LIMIT_JOB_MEMORY` |
| IO 速率 | `io_rate_bytes_per_sec`、`io_rate_iops` | `JobObjectIoRateControl`（Win10+） |
| 进程/线程数 | `max_processes`、`max_threads` | `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` |
| 超时 | `wall_clock_timeout_ms`、`cpu_timeout_ms` | 沙箱主循环定时器 |
| UI 限制 | `no_ui`（禁止创建窗口/剪贴板/系统参数） | `JOB_OBJECT_UILIMIT_*` |

**FR-2.2 级联终止**
- Job 内所有进程（含子进程）随 Job 销毁而终止。
- `BreakawayOk` 可配置：默认禁止子进程逃逸 Job。

**验收标准**
- 内存超限：触发 `JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT`，相关进程被杀。
- CPU 时间超限：触发 `JOB_OBJECT_MSG_END_OF_JOB_TIME`，整个 Job 被杀。
- 超时：沙箱主动 `TerminateJobObject`。

---

### 4.3 AppContainer 隔离

**FR-3.1 AppContainer Profile 创建**
- 为每个沙箱实例生成唯一 AppContainer SID 与 Profile 目录（`%LOCALAPPDATA%\Packages\win-sandbox-<id>`）。
- 使用 `CreateAppContainerToken` 生成受限 Token，用于 `CreateProcessAsUser`。

**FR-3.2 能力（Capability）授予**
- 默认授予最小能力集：`internetClient`（仅当配置允许网络时）、`privateNetworkClientServer`（按需）。
- 文件系统访问通过 `GrantSidAccess` 显式授予指定路径的 Read/Write/Execute。

**FR-3.3 自适应权限降级**
- 普通用户权限下：AppContainer 仍可创建，但部分 Job 限制（如某些 CPU Rate Control 增强项）不可用。
- 启动时输出 `CapabilityReport` 事件，列出当前实际生效的能力集。

**验收标准**
- AppContainer 内进程尝试访问 `C:\Windows\System32\config` 失败（Access Denied）。
- 授予 `<workdir>\read` 的 Read 权限后，进程可读取该目录。
- 普通用户模式下启动不崩溃，仅能力子集生效。

---

### 4.4 文件系统隔离

**FR-4.1 多模式可配置**

| 模式 | 行为 |
|------|------|
| `whitelist` | 仅授予配置的路径白名单读写权限，其余路径 AppContainer 默认拒绝 |
| `temp-workspace` | 复制/链接指定工作目录到沙箱临时区，写操作隔离到临时区，结束后可保留或丢弃 |
| `redirect` | （高级）路径重定向，被隔离程序看到的路径不变但实际落到临时区。优先用 NTFS 链接/Junction，复杂场景后续考虑 minifilter（标记为 Phase 2） |
| `combined`（默认） | whitelist 授予读 + temp-workspace 重定向写 |

**FR-4.2 路径规则**
- 配置项：`read_paths[]`、`write_paths[]`、`execute_paths[]`。
- 支持 `%ENV%` 变量展开。
- 支持 glob（如 `C:\Tools\*.exe`）。

**FR-4.3 临时工作区**
- 默认位置：`%TEMP%\win-sandbox-<instance-id>\`。
- 退出策略：`keep` / `discard` / `archive`（打包成 zip）。

**验收标准**
- `whitelist` 模式下，访问白名单外路径触发 `AccessDenied` 事件。
- `temp-workspace` 模式下，程序写入 `C:\proj\out.txt`，实际落到临时区，宿主原路径不受影响。
- `archive` 模式结束后生成 zip 包含所有写操作产物。

---

### 4.5 网络隔离

**FR-5.1 网络策略可配置**

| 策略 | 行为 |
|------|------|
| `deny-all`（默认） | 不授予任何网络能力，AppContainer 默认拒所有 socket |
| `allowlist` | 通过 WFP（用户态 `FwpmEngineOpen0` + filter）或命名管道代理限制目标 IP/端口 |
| `proxy` | 强制走 SOCKS/HTTP 代理（沙箱内置代理转发器） |
| `full` | 授予 `internetClient` 能力，不限制目标 |

**FR-5.2 WFP 集成（用户态）**
- 注册 `FWPM_LAYER_ALE_AUTH_CONNECT_V4/V6` callout（用户态过滤器，无需驱动签名）。
- 拦截 connect 调用，按白名单放行/拒绝。

**验收标准**
- `deny-all` 模式下，`curl example.com` 失败。
- `allowlist` 模式下，仅允许的 IP:port 可连通。
- 拦截事件通过 `NetworkBlocked` 事件流回传。

---

### 4.6 IPC（命名管道）

**FR-6.1 协议设计**
- 管道名：`\\.\pipe\win-sandbox-<instance-id>`。
- 帧格式：长度前缀（4 字节小端）+ JSON 负载（UTF-8）。
- 消息类型分两类：
  - **Command**（Python → 沙箱）：`StartProcess`、`WriteStdin`、`SignalProcess`、`Shutdown`、`QueryStatus`。
  - **Event**（沙箱 → Python）：`Ready`、`ProcessStarted`、`ProcessOutput`（stdout/stderr 分通道）、`ProcessExited`、`ResourceLimitHit`、`AccessDenied`、`NetworkBlocked`、`BehaviorLog`、`StatsReport`、`CapabilityReport`、`Error`。

**FR-6.2 多客户端**
- 支持多个 Python 客户端连接同一沙箱（如一个发命令、一个收事件）。
- 客户端通过 `ClientType`（`controller` / `observer`）标识，observer 仅收事件不发命令。

**FR-6.3 安全描述符**
- 管道 DACL 限制为当前用户 SID，防止跨用户访问。

**验收标准**
- Python 端 `import win32pipe` 能成功连接并完成完整握手。
- 单条消息最大 16MB（可配置），超出分片。
- 异常断连后，沙箱进入安全状态（按配置 `Shutdown` 或继续运行）。

---

### 4.7 IO 交互（数据面）

**FR-7.1 流回收模式（一次性运行）**
- Python 发送 `StartProcess`（含命令行、stdin 内容、超时）。
- 沙箱创建被隔离进程，stdin 一次性写入，stdout/stderr 全量缓冲并分块回传为 `ProcessOutput` 事件。
- 进程退出后发送 `ProcessExited`（含退出码、资源统计）。

**FR-7.2 双向交互模式**
- `StartProcess` 指定 `interactive: true`。
- Python 后续可通过 `WriteStdin` 持续写入。
- stdout/stderr 实时分块回传。
- 支持 `SignalProcess`（发送 CTRL_C/CTRL_BREAK/自定义信号）。

**FR-7.3 多进程管理**
- 单个沙箱实例可同时托管多个被隔离进程（同一 Job 内）。
- 每个进程有唯一 `process_id`（沙箱内分配），事件携带该 ID 路由。

**验收标准**
- 流回收模式：`echo hello` 的 stdout 在退出前完整回收。
- 交互模式：Python 端 `WriteStdin("1+1\n")` 后能收到 `ProcessOutput("2\n")`。
- 多进程：同时跑 3 个进程，事件能正确按 `process_id` 区分。

---

### 4.8 行为监控（可观测性）

**FR-8.1 进程/句柄/模块监控**
- ETW 内核会话（`NT Kernel Logger` 或 `EventTrace`）订阅：
  - `ProcessStart` / `ProcessStop`（含父进程链、命令行）。
  - `ThreadStart/Stop`。
  - `ImageLoad`（DLL 加载）。
  - `FileCreate` / `FileDelete` / `FileRename`。
  - `RegistryCreate/Delete/SetQuery`。
- 句柄打开：通过 `ObRegisterCallbacks`（需要驱动，**降级为 ETW + API hook** 在用户态实现）。

**FR-8.2 文件/注册表访问日志**
- AppContainer 拒绝访问时，触发 `AccessDenied` 事件（路径、操作类型、所需能力）。
- 允许的访问通过 ETW FileIo 事件记录。

**FR-8.3 网络请求日志**
- ETW `TcpIp` / `UdpIp` provider 记录 connect/send/recv。
- WFP 拦截事件（FR-5.2）。

**FR-8.4 资源使用统计**
- Job Object 会计信息（`JobObjectBasicAccountingInformation` / `JobObjectExtendedLimitInformation`）：
  - CPU 时间（用户/内核）。
  - IO 读写字节/次数。
  - 页错误数。
  - 峰值内存。
- 周期性（默认 1s）发送 `StatsReport` 事件，结束时发送最终汇总。

**验收标准**
- 启动记事本：能看到 `ProcessStart`、`ImageLoad`（notepad.exe 及依赖 DLL）。
- 写文件：能看到 `FileCreate` 事件含完整路径。
- 联网：能看到 `TcpIp` connect 事件含目标 IP:port。
- 结束后 `StatsReport` 包含 CPU/内存/IO 汇总。

---

## 5. 非功能需求

### 5.1 性能

| 指标 | 目标 |
|------|------|
| 沙箱启动延迟（Ready 事件） | ≤ 1s（管理员模式）、≤ 500ms（普通用户降级模式） |
| IPC 单向延迟 | ≤ 5ms（本机命名管道，<4KB 消息） |
| ETW 事件吞吐 | ≥ 10000 events/s 不丢事件（使用 ring buffer + 异步发送） |
| 监控开销 | 单进程 CPU 占用增加 ≤ 5% |
| 内存占用 | 沙箱本体常驻 ≤ 50MB（不含被隔离进程） |

### 5.2 安全

- 沙箱进程自身最小权限运行（如可行，沙箱主进程也降权）。
- 命名管道 DACL 严格限制。
- 配置文件 schema 校验，拒绝越权配置（如要求超出当前权限的能力）。
- 不信任 Python 端输入：所有 IPC 命令做边界校验。

### 5.3 兼容性

- **最低系统**：Windows 10 1809 (17763)。
- **目标系统**：Win10 1809+ / Win11。
- **架构**：x64 优先，ARM64 后续考虑。
- **被隔离程序**：任意 Win32 控制台程序、GUI 程序（GUI 受 `no_ui` 限制）。

### 5.4 可观测性（沙箱自身）

- 完备日志系统（spdlog），分级：trace/debug/info/warn/error。
- 日志输出：文件（按天滚动）+ stderr（启动期）+ 可选 ETW provider。
- 关键决策点、错误路径、IPC 消息摘要均记录。

### 5.5 可维护性

- 干净架构分层（实体/用例/适配器/框架层），见 HLD。
- 关键部分注释、决策点注释。
- 单元测试覆盖率 ≥ 60%，e2e 测试覆盖核心场景。

---

## 6. 数据/配置要求

### 6.1 沙箱配置文件（JSON）

`config.json` 示例结构：

`{
  "instance_id": "auto",
  "permission_mode": "auto",
  "job": {
    "cpu_ms": 5000,
    "cpu_rate_percent": 50,
    "memory_mb": 256,
    "io_rate_bytes_per_sec": 10485760,
    "max_processes": 32,
    "wall_clock_timeout_ms": 10000,
    "no_ui": true,
    "breakaway_ok": false
  },
  "appcontainer": {
    "enabled": true,
    "capabilities": ["internetClient"]
  },
  "filesystem": {
    "mode": "combined",
    "read_paths": ["C:\\Tools\\", "%PROJECT_DIR%"],
    "write_paths": ["%TEMP%\\win-sandbox-out"],
    "execute_paths": ["C:\\Windows\\System32\\cmd.exe"],
    "temp_workspace": {
      "enabled": true,
      "source": "%PROJECT_DIR%",
      "exit_strategy": "archive"
    }
  },
  "network": {
    "policy": "allowlist",
    "allowlist": [{"ip": "127.0.0.1", "port": 8080}]
  },
  "monitoring": {
    "etw": true,
    "wfp": true,
    "stats_interval_ms": 1000
  },
  "logging": {
    "level": "info",
    "file": "%TEMP%\\win-sandbox-<id>\\sandbox.log"
  }
}`

### 6.2 IPC 消息示例

`StartProcess` 命令：

`{
  "type": "StartProcess",
  "command_line": "cmd.exe /c dir",
  "working_dir": "C:\\proj",
  "stdin": "",
  "interactive": false,
  "env": {"FOO": "bar"},
  "timeout_ms": 5000
}`

`ProcessOutput` 事件：

`{
  "type": "ProcessOutput",
  "process_id": 1,
  "channel": "stdout",
  "data_base64": "PCBkaXIgLi4uPg==",
  "seq": 1
}`

---

## 7. 依赖与约束

### 7.1 技术栈

| 项 | 选型 | 用途 |
|----|------|------|
| 语言 | C++17 | 主实现语言 |
| 构建 | CMake ≥ 3.20 | 构建系统 |
| 编译器 | MSVC (cl.exe) | 项目内自动定位 |
| Windows SDK | 10.0.19041+（已内置 `windows-debugging` 工具集中的部分组件可复用） | AppContainer/Job/WFP/ETW API |
| WIL | Microsoft Windows Implementation Libraries（header-only） | COM/句柄/HRESULT 简化 |
| JSON | nlohmann/json（header-only） | 配置与 IPC 序列化 |
| 日志 | spdlog | 结构化日志 |
| Python 端 | Python 3.8+，仅用标准库 `win32pipe`/`ctypes` 或提供轻量 `win_sandbox` 包 | 编排 |

### 7.2 平台约束

- 仅支持 Windows 10 1809+。
- AppContainer 完整功能需管理员权限（自适应降级）。
- WFP 用户态 callout 需管理员权限。
- 部分 Job 增强（CPU Rate Control v2）需 Win10+。

### 7.3 工程规范（来自 AGENTS.md）

- 干净架构分层，依赖只能外→内。
- 禁止降级/兼容/缓解方案。
- 重构后不留兼容接口，引用点必须更新。
- 完备日志与关键注释。
- 硬编码路径禁止（用 `%ENV%` / `WINDIR` 等）。
- 必须写测试（最少 e2e）。
- 未查到根本因素不改源码。

---

## 8. 里程碑

| 阶段 | 交付物 | 验收 |
|------|--------|------|
| M0：脚手架 | CMake 工程、目录结构、WIL/json/spdlog 集成、空沙箱能启动并响应 `Shutdown` | `sandbox.exe --pipe test` 启动后 Python 能连管道并收 `Ready` |
| M1：Job 隔离 | Job Object 创建、资源限制、级联终止、stdout 流回收 | 场景 A（OJ 评测）跑通 |
| M2：AppContainer | AppContainer profile、能力授予、路径白名单 | 沙箱内程序无法访问白名单外路径 |
| M3：IPC + 交互模式 | 完整命令/事件协议、双向交互、多进程 | 场景 B（交互 REPL）跑通 |
| M4：文件系统隔离 | temp-workspace、combined 模式、archive 退出 | 写操作隔离到临时区 |
| M5：网络隔离 | WFP 用户态 callout、allowlist/proxy | 场景 C（样本分析）网络事件可见 |
| M6：行为监控 | ETW 订阅、四类事件全量、StatsReport | 行为报告完整 |
| M7：自适应权限 | 权限检测、能力降级、CapabilityReport | 普通用户模式不崩、能力子集生效 |
| M8：Python 包 | `win_sandbox` 轻量包、e2e 测试套件 | 4 类场景 e2e 全绿 |

---

## 9. 风险与开放问题

### 9.1 已知风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| AppContainer profile 创建失败（权限/同名冲突） | 沙箱启动失败 | 重试 + 唯一 SID + 明确错误码 |
| ETW 事件丢失（高负载） | 监控不完整 | ring buffer + 背压告警 + 事件序号检测丢包 |
| WFP 用户态 callout 性能瓶颈 | 网络延迟上升 | 异步处理 + 超时默认放行/拒绝策略 |
| 被隔离程序通过命名管道/共享内存逃逸 | 隔离失效 | AppContainer 默认拒所有 IPC，显式授予 |
| Job Object 限制对子进程 spawn 行为影响 | 程序异常 | `breakaway_ok` 可配置，但默认禁止 |

### 9.2 开放问题（待后续 HLD/LLD 决策）

- [TBD: minifilter 驱动是否在 Phase 2 引入以实现完整路径重定向？]
- [TBD: 是否支持 Server Silo（进程隔离容器）作为更强隔离层？需 Win10 1809+ 且需更深入内核交互。]
- [TBD: Python 包发布形式（pip wheel / 单文件 / 源码安装）。]
- [TBD: 是否需要 Web UI 用于查看沙箱状态与历史行为报告？]
- [TBD: 多沙箱实例间的资源隔离（CPU/内存全局配额）。]

---

## 10. 附录

### 10.1 术语表

| 术语 | 含义 |
|------|------|
| Job Object | Windows 进程组对象，可对组内所有进程施加资源限制与级联控制 |
| AppContainer | Windows 8+ 引入的沙箱执行环境，基于能力（Capability）的访问控制 |
| Capability | AppContainer 的权限单元，如 `internetClient`、`documentsLibrary` |
| WFP | Windows Filtering Platform，网络过滤框架 |
| ETW | Event Tracing for Windows，内核级事件追踪 |
| SID | Security Identifier，安全标识符 |
| DACL | Discretionary Access Control List，自主访问控制列表 |

### 10.2 参考文档

- Microsoft Docs: Job Objects
- Microsoft Docs: AppContainer
- Microsoft Docs: Windows Filtering Platform
- Microsoft Docs: Event Tracing for Windows
- WIL: https://github.com/microsoft/wil
- nlohmann/json: https://github.com/nlohmann/json
- spdlog: https://github.com/gabime/spdlog

### 10.3 后续文档计划

| 文档 | 状态 | 说明 |
|------|------|------|
| HLD（高层设计） | 待编写 | 架构分层、模块划分、数据流 |
| LLD（低层设计） | 待编写 | 类/接口定义、详细流程 |
| TDD（技术设计） | 待编写 | 关键技术决策对比 |
| 测试方案 | 待编写 | e2e 场景与自动化策略 |
