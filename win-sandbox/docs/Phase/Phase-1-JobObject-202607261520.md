# Phase 1：Job Object 资源限制（M1）

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| Phase | 1 |
| 对应里程碑 | M1 |
| 创建日期 | 2026-07-26 |
| 前置 Phase | Phase 0 |

---

## 2. 目标

在 Phase 0 脚手架基础上，实现 Job Object 资源限制与进程托管。结束时能跑通 PRD 场景 A（OJ 评测）：Python 提交命令 → 沙箱启动被隔离进程 → 限时/限内存 → 回收 stdout → 返回退出码与资源统计。

---

## 3. 范围

### In Scope
- `IJobObject` 接口（src/core/ports/）+ `JobObjectImpl`（src/infra/job/）
- 资源配额配置：`cpu_ms`、`cpu_rate_percent`、`memory_mb`、`io_rate`、`max_processes`、`wall_clock_timeout_ms`、`no_ui`
- IOCP 绑定 Job，接收 `JOB_OBJECT_MSG_*` 通知
- `IProcessLauncher` 接口 + `ProcessLauncherImpl`（CreateProcessW，暂不用 AppContainer Token）
- 完整 IPC 命令/事件：
  - Command：`StartProcess`、`QueryStatus`
  - Event：`ProcessStarted`、`ProcessOutput`（stdout/stderr 分通道）、`ProcessExited`、`ResourceLimitHit`、`StatsReport`
- stdout/stderr 异步读线程（`ReadFile` + OVERLAPPED）
- 级联终止（`TerminateJobObject`）
- 配置文件加载（`ConfigLoader`，JSON schema 校验）
- `ResourceQuota`、`SandboxedProcess` 实体

### Out of Scope
- AppContainer 隔离（Phase 2）
- 双向交互 stdin 写入（Phase 3）
- 多客户端（Phase 3）
- 文件系统/网络隔离（Phase 4/5）

---

## 4. 前置依赖

- Phase 0 全部交付物
- Windows Job Object API（kernel32）
- 配置文件 schema（JSON）

---

## 5. 任务清单

### T1.1 实体与端口定义
- `src/core/entities/ResourceQuota.hpp`：资源配额值对象
- `src/core/entities/SandboxedProcess.hpp`：被隔离进程聚合
- `src/core/ports/IJobObject.hpp`：Job 接口
- `src/core/ports/IProcessLauncher.hpp`：进程启动器接口
- `src/core/ports/IStatsCollector.hpp`：统计收集器接口

**验收**：头文件编译通过，无 Win32 依赖。

### T1.2 JobObjectImpl
- `src/infra/job/JobObjectImpl.hpp/cpp`
- `CreateJobObjectW`（kill-on-close 标志）
- `SetInformationJobObject`：
  - `JobObjectExtendedLimitInformation`（内存、CPU 时间、active process）
  - `JobObjectCpuRateControlInformation`（CPU 占比，Win10+）
  - `JobObjectIoRateControlInformation`（IO 速率，Win10+）
  - `JobObjectBasicUIRestrictions`（UI 限制）
  - `JobObjectAssociateCompletionPortInformation`（IOCP 绑定）
- `AssignProcessToJobObject`
- `TerminateJobObject`
- `QueryInformationJobObject`（会计信息）

**验收**：单元测试（mock IOCP）验证限制设置成功。

### T1.3 IOCP 通知处理
- 实现位置：`src/infra/job/JobObjectImpl.cpp` 内联（按 LLD-01 §5.3 设计，
  IOCP 与 Job Object 强耦合：一个 Job 对应一个 IOCP，无需独立类）
- 独立线程 `GetQueuedCompletionStatus` 阻塞
- 处理消息：
  - `JOB_OBJECT_MSG_END_OF_JOB_TIME` → `EndOfJobTime`
  - `JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT` → `ProcessMemoryLimit`
  - `JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT` → `ActiveProcessLimit`
  - `JOB_OBJECT_MSG_EXIT_PROCESS` → `ProcessExit`（触发上层进程退出处理）
  - 其他 `JOB_OBJECT_MSG_*` → 对应 `JobNotificationType`
  - 未识别消息 → `JobNotificationType::Unknown`，记日志后丢弃，不投递 sink
- 翻译为 `JobNotification`，通过 `IJobNotificationSink` 投递给上层

**验收**：内存超限场景下，相关进程被杀，Python 收到 `ResourceLimitHit`
（需 T1.6 StartProcessUseCase 完成才能跑 e2e 验收）。

### T1.4 ProcessLauncherImpl
- `src/infra/process/ProcessLauncherImpl.hpp/cpp`
- `CreateProcessW`（暂用普通 Token，Phase 2 替换为 AppContainer Token）
- `CreatePipe` 创建 stdin/stdout/stderr 管道
- `STARTUPINFO` 设置 stdio 句柄
- 进程创建后立即 `AssignProcessToJobObject`
- 启动 stdout/stderr 读线程

**验收**：`cmd.exe /c echo hello` 启动成功，stdout 被读取。

### T1.5 stdout/stderr 异步读取
- `src/infra/process/StreamReader.hpp/cpp`
- `src/core/entities/ProcessOutputEvent.hpp`：输出事件实体
- `src/core/ports/IProcessOutputSink.hpp`：输出回调端口
- **读模型**：同步 `ReadFile` + 独立 `std::thread`（每条流一个）
  - 原计划 OVERLAPPED 异步读，但 MSDN 明确匿名管道不支持 overlapped I/O；
    ProcessLauncherImpl 已用 `CreatePipe` 创建匿名管道，无法切到 OVERLAPPED。
  - 经设计评审采用「同步读线程」方案：子进程退出 → 写端关闭 →
    ReadFile 返回 0 字节 EOF → 线程自然退出；沙箱主动断流调
    `CancelSynchronousIo` 取消阻塞的 ReadFile。
- 缓冲区分块（默认 64KB）
- 读到数据 → 投递 `ProcessOutputEvent(data, eof=false)` 到 `IProcessOutputSink`
- 读到 EOF → 投递 `ProcessOutputEvent("", eof=true)`，标记流结束
- 主动 Stop → 不投递 eof，直接退出线程（上层通过 IsFinished 感知）

**验收**：`dir C:\` 的 stdout 完整回收（含 `<DIR>` 标记，无截断），
stdout/stderr 分流正确，主动 Stop 在 1s 内返回。

### T1.6 StartProcessUseCase
- `src/core/usecases/StartProcessUseCase.hpp/cpp`
- 输入：`StartProcessRequest`（command_line、working_dir、stdin、timeout_ms、env）
- 流程：EnforcePolicy → ProcessLauncher::Launch → JobObject::Assign → 发 ProcessStarted
- 输出：`SandboxedProcess`

**验收**：Python 发 StartProcess，收到 ProcessStarted + ProcessOutput + ProcessExited 完整序列。

### T1.7 配置加载
- `src/adapters/ConfigLoader.hpp/cpp`
- JSON → `SandboxConfig` 领域对象
- schema 校验（必填字段、范围校验）
- `%ENV%` 变量展开

**验收**：合法配置加载成功；非法配置（如负数 memory_mb）拒绝启动并报错。

### T1.8 StatsCollector
- `src/infra/stats/StatsCollectorImpl.hpp/cpp`
- 周期查询 `JobObjectBasicAccountingInformation` / `JobObjectExtendedLimitInformation`
- CPU 时间（用户/内核）、IO 字节、峰值内存、页错误
- 周期发 `StatsReport`（默认 1s）
- 进程退出时发最终汇总

**验收**：跑 `ping -n 5 127.0.0.1` 后 StatsReport 包含 CPU/IO 统计。

### T1.9 e2e 测试：OJ 场景
- `tests/e2e/test_oj_scenario.py`
- 提交 `cmd.exe /c echo hello`，限时 5s、内存 256MB
- 验证 stdout = "hello"
- 验证退出码 0
- 提交死循环程序，验证超时被杀
- 提交内存爆炸程序，验证内存超限被杀

**验收**：4 个子用例全绿。

---

## 6. 风险

| 风险 | 缓解 |
|------|------|
| Job Object 已存在于父进程（如被 IDE 包裹） | 检测 `IsProcessInJob`，必要时报错 |
| IO Rate Control 需要 Win10+ 且管理员 | 自适应：普通用户跳过该项 |
| stdout 读线程在进程崩溃后挂起 | 同步 ReadFile + 独立线程，进程退出时写端关闭触发 EOF；沙箱主动断流用 CancelSynchronousIo |
| 配置 schema 校验遗漏边界 | 用 JSON Schema 库做严格校验 |

---

## 7. 退出条件

- [x] Job Object 创建并设置资源限制成功
- [x] StartProcess → ProcessStarted → ProcessOutput → ProcessExited 全链路通
- [x] 内存超限/CPU 超时触发 ResourceLimitHit 并杀死进程
- [x] StatsReport 周期发送且数据正确
- [x] OJ 场景 e2e 4 子用例全绿
- [x] 单元测试覆盖率 ≥ 60%
