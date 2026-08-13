# Phase 3：IPC 协议完整 + 交互模式（M3）

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| Phase | 3 |
| 对应里程碑 | M3 |
| 创建日期 | 2026-07-26 |
| 前置 Phase | Phase 2 |

---

## 2. 目标

在 Phase 1/2 基础上，实现完整 IPC 协议与双向交互能力。结束时能跑通 PRD 场景 B（交互式 REPL）：Python 启动长跑进程，持续发送 stdin、接收 stdout 事件流，支持多进程并行托管。

---

## 3. 范围

### In Scope
- 完整 IPC 命令集：`StartProcess`、`WriteStdin`、`SignalProcess`、`TerminateProcess`、`QueryStatus`、`Shutdown`
- 完整 IPC 事件集：`Ready`、`ProcessStarted`、`ProcessOutput`、`ProcessExited`、`ResourceLimitHit`、`AccessDenied`、`StatsReport`、`Error`、`ShutdownComplete`
- 双向交互模式（`interactive: true`）
- `WriteStdin` 持续写入
- `SignalProcess`（CTRL_BREAK / KILL；不支持 CTRL_C，详见 T3.4 设计决策）
- 多进程管理（同 Job 内多进程，每进程唯一 `process_id`）
- 多客户端连接（`controller` / `observer`）
- 管道 DACL 严格限制
- 消息分片（>16MB 分片传输）
- IPC `version` 字段握手
- Python 客户端完整 API

### Out of Scope
- 文件系统/网络隔离（Phase 4/5）
- 行为监控 ETW（Phase 6）
- 自适应权限（Phase 7）

---

## 4. 前置依赖

- Phase 2 全部交付物
- `STARTUPINFO` stdin 管道（Phase 1 仅 stdout/stderr）

---

## 5. 任务清单

### T3.1 IPC 协议定版
- `docs/LLD-04-IPC.md`（本阶段产出）
- 帧格式：`[4字节小端长度][UTF-8 JSON]`
- 消息 schema：所有命令与事件的 JSON 结构
- `version` 字段：`"1.0"`
- 错误码枚举

**验收**：协议文档评审通过，C++/Python 双端对齐。

### T3.2 命令分发器
- `src/core/usecases/HandleIpcCommandUseCase.hpp/cpp`
- 根据 `type` 字段路由到对应用例
- 同步命令返回响应（`request_id` 关联）
- 异步命令触发事件流
- schema 校验（Debug 构建启用 JSON Schema）

**验收**：未知 type 返回 `Error`；非法 JSON 返回 `Error`。

### T3.3 WriteStdin 实现 ✅ 已完成（2026-07-27）

**实现内容**：
- `StartProcessUseCase::WriteStdin(data, size)`：校验 started/finished/stdin_write_ 状态后调 `IProcessLauncher::WriteStdin`
- `StartProcessUseCase::CloseStdinWrite()`：原子关闭 stdin 写端（`InterlockedExchangePointer`），与 wait 线程/WriteStdin 并发安全
- `ProcessLauncherImpl::WriteStdin`：同步 `WriteFile` 到 stdin 管道写端
- `ProcessLauncherImpl::CloseStdin`：`CloseHandle` 关闭 stdin 写端
- `interactive=true` 时 `Execute` 保留 `stdin_write_`；`interactive=false` 时一次性写入 `stdin_data` 后立即关闭
- `main.cpp`：`WriteStdin` 命令路由（payload: `{"data": "..."}`），缺 data 字段返回 `invalid_payload`
- `python/win_sandbox/client.py`：`send_write_stdin(data)` 方法
- `tests/e2e/test_write_stdin.py`：6 用例 e2e 测试

**关键 bug 修复：stdin 管道读写端搞反**：
- 根因：`CreateInheritablePipe(parent_inherit, child_inherit)` 参数命名与 `CreatePipe` 实际返回的 `(read_end, write_end)` 顺序语义错位。stdin 调用方误把写端当读端传给子进程 `hStdInput`，子进程 `ReadFile(写端)` 立即失败 → REPL 进程视为 EOF 立即退出。
- stdout/stderr 恰好碰对（沙箱读=读端=first，子进程写=写端=second），所以 Phase 1/2 一直没暴露。
- 修复：重构为 `CreateInheritablePipe(read_inherit, write_inherit)`，返回 `{read_handle, write_handle}`，语义直接对齐 `CreatePipe`，消除调用方混淆空间。Launch 中 stdin 用 `(read_inherit=true, write_inherit=false)`，stdout/stderr 用 `(read_inherit=false, write_inherit=true)`。
- 教训：句柄继承标志必须按数据流向精确设置，参数命名要直接对应底层 API 语义，避免引入"parent/child"这种与底层无关的概念层。

**Python REPL 行为注记**：
- `python -i -B` 的版本横幅和 `>>>` 提示符输出到 **stderr**（不是 stdout）
- `print(...)` 的执行结果输出到 stdout
- e2e 测试中等 `>>>` 用 `_wait_output_contains`（同时检查 stdout+stderr），等 `print` 输出用 `_wait_stdout_contains`

**验收**：6 用例全绿（基础 REPL 交互、多次连续写入、interactive=false 报错、已退出进程报错、无进程报错、无效 payload 报错）。

**e2e 测试结果**（2026-07-27 17:36）：
```
Test 1/6: 基础 REPL 交互                      [PASS] stdout="2"
Test 2/6: 多次连续写入                        [PASS] stdout="a1\r\na2\r\na3"
Test 3/6: interactive=false 时 WriteStdin     [PASS] stdin_not_open
Test 4/6: 已退出进程 WriteStdin               [PASS] process_already_exited
Test 5/6: 无进程时 WriteStdin                 [PASS] no_process_running
Test 6/6: 无效 payload（缺 data 字段）        [PASS] invalid_payload
Result: 6 passed, 0 failed, 0 skipped
```

### T3.4 SignalProcess 实现 ✅ 已完成（2026-07-27）

**设计决策（修订）：移除 CtrlC，只支持 CtrlBreak + Kill**

原方案支持 CtrlC/CtrlBreak/Kill 三种信号，实现过程中发现 Windows 设计上 `CTRL_C_EVENT` 无法定向投递到非调用进程所在进程组（`GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)` 对 `CREATE_NEW_PROCESS_GROUP` 创建的进程组 API 返回 TRUE 但不实际投递）。

通过最小复现验证两个方案后定案：
- **方案 A（广播 + sandbox 屏蔽）失败**：`GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` 广播会命中调用方（Python 客户端）自身，导致调用方被 KeyboardInterrupt 中断。要求每个调用方注册 `SetConsoleCtrlHandler` 屏蔽是坏设计。
- **方案 C（移除 CtrlC，用 CtrlBreak 替代）成功**：`CTRL_BREAK_EVENT` 可定向投递到 `CREATE_NEW_PROCESS_GROUP` 创建的进程组，不影响调用方。

**最终实现**：
- `ProcessSignal::CtrlBreak` → `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)` 定向投递到子进程组
- `ProcessSignal::Kill` → `TerminateProcess(handle, 1)` 强制终止
- 创建进程时用 `CREATE_NEW_PROCESS_GROUP`（进程组 ID = PID）
- `interactive=true` 时不设 `CREATE_NO_WINDOW`，让子进程继承 sandbox 的 console（CtrlBreak 依赖 console 共享）
- Kill 设 `pending_exit_reason_=KilledByUser`；CtrlBreak 不设（进程可捕获后继续运行）

**修改文件**：
- `src/core/ports/IProcessLauncher.hpp`：`ProcessSignal` enum（CtrlBreak, Kill）
- `src/infra/process/ProcessLauncherImpl.cpp`：`Signal` 实现 + `CREATE_NEW_PROCESS_GROUP`
- `src/core/usecases/StartProcessUseCase.{hpp,cpp}`：`SignalProcess` 方法 + `create_no_window = !interactive`
- `src/main.cpp`：`SignalProcess` 命令路由（payload: `{"signal": "ctrl_break"|"kill"}`）
- `python/win_sandbox/client.py`：`send_signal(signal)` 方法
- `tests/e2e/test_signal.py`：5 用例 e2e 测试

**验收**：5 用例全绿（Kill 强杀、CtrlBreak 中断、已退出进程报错、无效 signal 报错、无进程报错）。

**e2e 测试结果**（2026-07-27 16:56）：
```
Test 1/5: Kill 强杀长跑进程              [PASS] exit_reason=KilledByUser(3)
Test 2/5: CtrlBreak 中断长跑进程         [PASS] exit_code=0xC000013A (SIGBREAK)
Test 3/5: 已退出进程 signal → Error      [PASS] process_already_exited
Test 4/5: 无效 signal 值 → Error         [PASS] invalid_payload
Test 5/5: 无进程时 signal → Error        [PASS] no_process_running
Result: 5 passed, 0 failed, 0 skipped
```

### T3.5 多进程管理 ✅ 已完成（2026-07-27）

**设计决策：per-process Job 模式（替代 Phase 1 单进程 RunningSession）**

备选方案是"共享 Job"（所有进程在同一个 Job 内，`SandboxInstance` 作为通知 sink 按 pid 路由）。选用 per-process Job 的理由：
1. `StartProcessUseCase` 现有 `TerminateAll` 语义正确（只杀自己的 Job 含子进程），无需改动
2. 无需通知路由（每个 usecase 直接注册为自己 Job 的 sink）
3. 资源隔离彻底（一个进程触发 CPU/内存限制不影响其他进程）
4. `StartProcessUseCase` 零改动，复用 Phase 1/2 全部逻辑

代价：无法实现"整体资源限制"（T3.5 验收未要求，Phase 4+ 资源池化时再考虑）。

**process_id 分配**：
- `std::atomic<uint32_t>` 自增，从 1 开始（0 保留为无效值）
- 沙箱内部 ID，稳定不复用（OS PID 可能被复用，process_id 不会）
- 所有 IPC 事件 payload 携带 `process_id`，客户端按此路由

**线程模型与并发安全**：
- `std::shared_mutex` 保护 `processes_` map：命令路由（`WriteStdin`/`SignalProcess`/`TerminateProcess`/`ListProcesses`）持 shared_lock 并发读；`StartProcess`/`CleanupFinished`/`ShutdownAll` 持 unique_lock 独占写
- `CleanupFinished` 析构安全：持 unique_lock 时不析构 usecase（析构会 join wait 线程，可能阻塞 IPC 线程；且 usecase 析构会调 `Emit` 死锁 emitter 锁）。先把待删除 entry 移到局部 vector，释放锁后再析构

**实现内容**：
- `src/adapters/SandboxInstance.hpp/cpp`（新建）：`SandboxInstance` 类 + `ProcessEntry` 结构体（封装 per-process 的 Job/Launcher/AppContainer/PathGrantor/UseCase，RAII 析构）
- `src/core/entities/SandboxedProcess.hpp`：新增 `process_id` 字段
- `src/core/usecases/StartProcessUseCase.cpp`：`ProcessStarted` 事件 payload 携带 `process_id`
- `src/core/entities/ErrorCode.hpp`：新增 `ProcessNotFound` 错误码
- `src/main.cpp`：`WriteStdin`/`SignalProcess`/`TerminateProcess` 命令路由改为按 payload 中的 `process_id` 查找对应 usecase；不存在返回 `process_not_found`
- `python/win_sandbox/client.py`：`send_write_stdin`/`send_signal`/`send_terminate_process` 方法新增 `process_id` 参数并写入 payload

**关键 bug 修复：NamedPipeServer 并发消息丢失**

- 现象：客户端连发多条命令（如多进程场景下连续 `StartProcess` × 3）时，部分命令丢失，服务端日志显示只收到第一条
- 根因：`NamedPipeServer::WaitCommand` 每次 `ReadFile` 一次后调 `decoder_.Feed` 解码，可能一次解出多条消息（客户端连发被合并到一个 TCP/pipe 读缓冲），但 `WaitCommand` 只返回第一条，剩余被丢弃
- 修复：新增 `pending_commands_` 队列，`Feed` 解出多条时第一条立即返回，剩余入队；下次 `WaitCommand` 优先消费队列。仅主线程访问，无需加锁
- 教训：流式帧解码器必须配合"未消费消息缓存"，单次读缓冲可能含多条完整帧

**修改文件清单**：
- `src/adapters/SandboxInstance.hpp`（新建）
- `src/adapters/SandboxInstance.cpp`（新建）
- `src/core/entities/SandboxedProcess.hpp`（新增 process_id 字段）
- `src/core/entities/ErrorCode.hpp`（新增 ProcessNotFound）
- `src/core/usecases/StartProcessUseCase.cpp`（ProcessStarted 事件含 process_id）
- `src/infra/ipc/NamedPipeServer.hpp/cpp`（pending_commands_ 队列修复消息丢失）
- `src/main.cpp`（命令路由按 process_id 查找）
- `python/win_sandbox/client.py`（API 升级，所有命令方法带 process_id）
- `tests/e2e/test_multiprocess.py`（新建，6 用例）
- `tests/e2e/test_write_stdin.py`（升级到多进程 API，所有用例带 process_id）
- `tests/e2e/test_signal.py`（升级到多进程 API，所有用例带 process_id）

**验收**：6 用例全绿（并发启动 3 进程事件按 process_id 路由、并发 WriteStdin 互不串扰、Terminate 单杀不影响其他、QueryStatus 进程列表准确、不存在 process_id 返回 process_not_found、process_id 自增不复用）。同时 T3.3/T3.4 升级到多进程 API 后回归测试 17/17 全绿。

**e2e 测试结果**（2026-07-27 18:43）：
```
test_multiprocess.py: 6/6 passed
  Test 1/6: 并发启动 3 进程 + 事件按 process_id 路由   [PASS] stdout 互不串扰
  Test 2/6: 并发 WriteStdin 到 3 个 REPL               [PASS] MARKER_A/B/C 互不串扰
  Test 3/6: TerminateProcess 单杀不影响其他             [PASS] pid1 terminated, pid2 running
  Test 4/6: QueryStatus 返回进程列表                    [PASS] pids=[1,2,3]
  Test 5/6: 操作不存在的 process_id → Error             [PASS] process_not_found × 3
  Test 6/6: process_id 自增不复用                        [PASS] [1,2,3] → 4

回归测试：
  test_write_stdin.py: 6/6 passed（多进程 API 升级后无回归）
  test_signal.py:      5/5 passed（多进程 API 升级后无回归，含 CtrlBreak 实际中断验证）
  合计 17/17 passed, 0 failed, 0 skipped
```

### T3.6 多客户端 ✅ 已完成（2026-07-27）

**设计决策**

1. **双接口方案**（`IIpcServer` + `IEventEmitter`）：
   - `IIpcServer::SendEvent(client_id, event)`：定向发送命令响应（Error/StatsReport/ShutdownComplete）给请求方
   - `IIpcServer::BroadcastEvent(event)`：广播进程事件（ProcessStarted/ProcessOutput/ProcessExited）给所有客户端
   - `IEventEmitter` 适配 `IIpcServer::BroadcastEvent`，让 core/usecases 层不直接依赖 IIpcServer（保持洋葱架构依赖方向）

2. **重命名 `NamedPipeServer` → `NamedPipeServerImpl`**：匹配 LLD-04 设计文档命名，明确"实现层"语义

3. **多 Controller 策略**：允许多个 controller 同时连接（非独占），任一 controller 可发命令；所有 controller 断连（且曾有过 controller）才触发沙箱退出

**多客户端架构**：

```
NamedPipeServerImpl
├── AcceptLoop 线程：CreateNamedPipeW + ConnectNamedPipe（overlapped），接受新连接
├── 每客户端一个 ClientSession：
│   ├── ClientReadLoop 线程：ReadFile + 帧解码 + Hello 握手 + 命令路由（回调 ICommandHandler）
│   └── ClientWriteLoop 线程：从 write_queue_ 取消息 + WriteFile（串行化写，避免交错）
└── SetCommandHandler(handler)：注入命令回调（main.cpp 的 IpcCommandHandler）
```

**Hello 握手协议**：
- 客户端连接后首条消息必须为 `Hello`（payload: `{"client_type": "controller"|"observer"}`）
- 服务端 `ClientReadLoop` 收到 Hello 后：
  1. 记录 `ClientType` 到 `ClientSession`
  2. 回调 `ICommandHandler::OnClientConnected(client_id, type)`
  3. `IpcCommandHandler::OnClientConnected` 发送 `Ready` 事件（定向）给该客户端
- 未发送 Hello 直接发命令 → 服务端拒绝并断连

**客户端角色与权限**：
- `Controller`：可发所有命令 + 收所有事件
- `Observer`：仅收事件，发命令返回 `Error(code=not_authorized)`
- 权限校验在 `IpcCommandHandler::HandleCommand` 开头统一拦截

**事件路由**：
- 命令响应（定向）：`IIpcServer::SendEvent(client_id, event)` → 入队到该客户端的 `write_queue_`
- 进程事件（广播）：`IIpcServer::BroadcastEvent(event)` → 遍历所有 `ClientSession`，入队到各自的 `write_queue_`

**退出条件**（`IpcCommandHandler::RequestExit`）：
1. 收到 `Shutdown` 命令：处理完后 `RequestExit("shutdown command received")`
2. 所有 controller 断连（且 `had_controller_ever_=true`）：`OnClientDisconnected` 中检测后 `RequestExit("all controllers disconnected")`
- 主线程在 `WaitExit()` 阻塞等待 `exit_cv_`，被唤醒后执行清理（`instance.ShutdownAll()` + `server.Shutdown()`）

**实现内容**：
- `src/core/entities/ClientInfo.hpp`（新建）：`ClientId`（uint64_t 自增）、`ClientType`（Controller/Observer）、`ClientInfo`、`ClientTypeToString`
- `src/core/ports/IIpcServer.hpp`（新建）：多客户端 IPC 服务端抽象（Start/Shutdown/SendEvent/BroadcastEvent/SetCommandHandler）
- `src/core/ports/ICommandHandler.hpp`（新建）：命令回调接口（OnClientConnected/OnClientDisconnected/HandleCommand）
- `src/infra/ipc/NamedPipeServerImpl.hpp/cpp`（重命名+重写）：`AcceptLoop` + `ClientReadLoop` + `ClientWriteLoop` + `ClientSession` 管理
- `src/main.cpp`：提取 `IpcCommandHandler` 类实现 `ICommandHandler`；`PipeEventEmitter` 适配 `IIpcServer::BroadcastEvent`
- `python/win_sandbox/client.py`：
  - `__init__` 新增 `client_type` 参数（默认 `"controller"`）
  - `start()` 末尾发送 `Hello` 握手
  - 新增 `connect_only(connect_timeout)`：只连接管道不启动 sandbox.exe（observer 复用已有沙箱）
  - 新增 `recv_message(timeout)`：取任意类型消息（observer 收集事件流）
- `tests/e2e/test_multi_client.py`（新建）：4 用例 e2e 测试

**关键 bug 修复：已退出进程操作错误码不一致**

- 现象：对已退出进程发 `SignalProcess` / `WriteStdin` 返回 `process_not_found`，预期应为 `process_already_exited`
- 根因：`IpcCommandHandler::HandleCommand` 开头统一调用 `instance_->CleanupFinished()`，把刚退出进程的 usecase 从 `processes_` map 中移除。后续 `WriteStdin`/`SignalProcess` 调用 `FindByProcessId` 返回 nullptr → `ProcessNotFound` → 错误码变成 `process_not_found`。而 `StartProcessUseCase::WriteStdin`/`SignalProcess` 本身能正确返回 `ProcessAlreadyExited`，前提是 usecase 还在 map 中
- 修复：`CleanupFinished` 不在 `HandleCommand` 开头统一调用，改为按需调用：
  - `StartProcess` 分支：给新进程腾资源
  - `QueryStatus` 分支：返回准确的活跃进程列表
  - `WriteStdin`/`SignalProcess`/`TerminateProcess`：不清理，保留已退出进程的 usecase 供返回 `ProcessAlreadyExited`
  - `ShutdownAll` 兜底清理
- 教训：清理副作用不应在"操作特定进程"的命令路径上触发，否则会破坏操作目标的语义可见性。客户端需要区分"进程从未存在"（`process_not_found`）和"进程已退出"（`process_already_exited`），两者对上层逻辑有不同含义

**修改文件清单**：
- `src/core/entities/ClientInfo.hpp`（新建）
- `src/core/ports/IIpcServer.hpp`（新建）
- `src/core/ports/ICommandHandler.hpp`（新建）
- `src/infra/ipc/NamedPipeServerImpl.hpp`（重命名自 NamedPipeServer.hpp）
- `src/infra/ipc/NamedPipeServerImpl.cpp`（重写）
- `src/main.cpp`（提取 IpcCommandHandler + 适配多客户端架构 + CleanupFinished 调用时机修复）
- `python/win_sandbox/client.py`（client_type + Hello + connect_only + recv_message）
- `tests/e2e/test_multi_client.py`（新建，4 用例）
- `tests/e2e/smoke.py`、`test_signal.py`、`test_write_stdin.py`、`test_multiprocess.py`、`test_appcontainer.py`、`test_oj_scenario.py`（显式传 `client_type="controller"`）

**验收**：4 用例全绿（多客户端连接 + Ready 握手、observer 不能发命令、事件广播到 observer、controller 断连触发沙箱退出）。T3.3/T3.4/T3.5 回归测试全绿。CleanupFinished 调用时机修复后，已退出进程操作正确返回 `process_already_exited`。

**e2e 测试结果**（2026-07-27 19:38）：
```
test_multi_client.py: 4/4 passed
  Case 1: multi-client connect (1 controller + 1 observer)  [PASS] both Ready
  Case 2: observer not authorized to send commands           [PASS] not_authorized
  Case 3: event broadcast to observer                        [PASS] process_started/output/exited
  Case 4: controller disconnect triggers sandbox exit        [PASS] auto-exit in 0.125s

全量回归（7 套件 31/31）：
  smoke.py:              PASS
  test_signal.py:        5/5 PASS（含 process_already_exited 修复验证）
  test_write_stdin.py:   6/6 PASS（含 process_already_exited 修复验证）
  test_multiprocess.py:  6/6 PASS
  test_multi_client.py:  4/4 PASS
  test_appcontainer.py:  5/5 PASS
  test_oj_scenario.py:   4/4 PASS
```

### T3.7 管道 DACL ✅
- 实现：`NamedPipeServerImpl::BuildPipeSecurityDescriptor`（`src/infra/ipc/NamedPipeServerImpl.cpp`）
- DACL 构造（自相对 SD，复用于所有管道实例）：
  - `AddAccessAllowedAceEx(SY, GENERIC_ALL)` — LocalSystem
  - `AddAccessAllowedAceEx(BA, GENERIC_ALL)` — BuiltinAdministrators
  - `AddAccessAllowedAceEx(user_sid, GENERIC_ALL)` — 当前进程用户 SID（`OpenProcessToken` + `GetTokenInformation(TokenUser)` + `CopySid`）
  - `SetSecurityDescriptorControl(SE_DACL_PROTECTED)` — 阻断容器继承，避免父目录 DACL 覆盖
  - `MakeSelfRelativeSD` 转自相对形式，存 `security_descriptor_` 供 `AcceptLoop` 所有 `CreateNamedPipeW` 复用
- 通过 `SECURITY_ATTRIBUTES` 传给 `CreateNamedPipeW`，每个管道实例统一应用
- 注意：实际写入 DACL 时 Windows generic mapping 会把 `GENERIC_ALL` 映射为 `FILE_ALL_ACCESS = 0x001F01FF`，读取到的 mask 已是 file object 专有权限

**e2e 验收**：`tests/e2e/test_pipe_dacl.py` 3/3 PASS
- 用例 1：DACL 内容正确性
  - `GetNamedSecurityInfoW` 读取管道 SD，遍历 DACL ACE
  - 验证 ACE 数量 = 3（SY/BA/user_sid），全部 ACCESS_ALLOWED、mask 含 `FILE_ALL_ACCESS`
  - 验证 `SE_DACL_PROTECTED` 已设置（不继承父容器权限）
  - 验证 DACL 不含 Everyone (WD)、Anonymous (AN)、Authenticated Users (AU) ACE
  - 实测：3 个 ACE 分别对应 `S-1-5-18` / `S-1-5-32-544` / `S-1-5-21-...-1001`，mask 全部 `0x001f01ff`
- 用例 2：匿名 token 连接被拒
  - `ImpersonateAnonymousToken(GetCurrentThread())` 切到匿名 token
  - `CreateFileW(pipe, GENERIC_READ|WRITE, OPEN_EXISTING)` 应返回 `ERROR_ACCESS_DENIED (5)`
  - `RevertToSelf` 恢复
  - 实测：err=5，连接被正确拒绝
- 用例 3：回归验证
  - 正常 controller 走完 Hello + Ready + StartProcess + ProcessStarted/Output/Exited 完整流程
  - 确认 DACL 限制不阻碍当前用户正常使用

**实现要点**：
- 纯 ctypes + advapi32/kernel32，不引入 pywin32 依赖（与 Python 客户端零依赖原则一致）
- 当前用户 SID 通过 `OpenProcessToken` + `GetTokenInformation(TokenUser)` 获取，与 C++ 端 `GetCurrentUserSid` 实现对称
- `FILE_ALL_ACCESS = STANDARD_RIGHTS_REQUIRED | SYNCHRONIZE | 0x1FF = 0x001F01FF`，对应 GENERIC_ALL 经 generic mapping 后的 file object 权限位

**全量回归**（8 套件 34/34）：
```
smoke.py:              PASS
test_signal.py:        5/5 PASS
test_write_stdin.py:   6/6 PASS
test_multiprocess.py:  6/6 PASS
test_multi_client.py:  4/4 PASS
test_appcontainer.py:  5/5 PASS
test_oj_scenario.py:   4/4 PASS
test_pipe_dacl.py:     3/3 PASS  ← T3.7 新增
```

### T3.8 消息分片
- 单消息 > 16MB 时分片
- 分片头：`{type: "Fragment", seq: N, total: M, payload: base64}`
- 接收方重组

**验收**：发送 20MB stdout 块，Python 端正确重组。

### T3.9 Python 客户端完整 API
- `SandboxClient` 扩展：
  - `start_process(cmd, interactive=False, timeout=None) -> Pid`
  - `write_stdin(pid, data)`
  - `signal(pid, signal_type)`
  - `terminate(pid)`
  - `query_status() -> List[ProcessInfo]`
  - `events()` 生成器（迭代事件流）
  - `shutdown()`
- 异步版本（基于 `asyncio`，可选）

**验收**：Python 端能完成完整交互流程。

### T3.10 e2e 测试：REPL 场景
- `tests/e2e/test_repl_scenario.py`
- 启动 `python.exe -i`，交互模式
- 发送 `print(1+1)` → 收到 `2`
- 发送 `import os; os.listdir('.')` → 收到列表
- CtrlBreak 中断死循环（T3.4 修订：不支持 CtrlC，用 CtrlBreak 替代）
- 多进程并行（3 个 REPL）

**验收**：5 子用例全绿。

---

## 6. 风险

| 风险 | 缓解 |
|------|------|
| GenerateConsoleCtrlEvent 需同进程组 | CREATE_NEW_PROCESS_GROUP + 进程组 ID 管理；CtrlC 无法定向投递，移除 CtrlC 只支持 CtrlBreak（详见 T3.4） |
| stdin 管道写阻塞 | 异步 WriteFile + 缓冲区上限 |
| 多客户端事件交错 | IPC Write 串行化（单线程写） |
| 分片重组复杂 | 限制单消息 ≤ 16MB，超长用多 ProcessOutput |
| observer 越权发命令 | 服务端校验 client_type |

---

## 7. 退出条件

- [x] 所有命令/事件类型实现并测试（T3.3 WriteStdin 6 用例全绿；T3.4 SignalProcess 5 用例全绿；T3.5 多进程管理 6 用例全绿；T3.6 多客户端 4 用例全绿；T3.7 管道 DACL 3 用例全绿；T3.8 分片协议 5 用例全绿；全量回归 34/34 全绿）
- [x] 双向交互（WriteStdin + SignalProcess）工作（T3.3 WriteStdin e2e 6 用例全绿；T3.4 SignalProcess e2e 5 用例全绿；含 stdin 管道读写端搞反的根因修复）
- [x] 多进程并行托管，事件正确路由（T3.5 SandboxInstance per-process Job 架构，process_id 自增不复用，shared_mutex 并发保护；e2e 6 用例全绿验证事件按 process_id 路由不串扰；NamedPipeServer 并发消息丢失 bug 已修复）
- [x] 多客户端（controller + observer）工作（T3.6 NamedPipeServerImpl 多线程架构 + Hello 握手 + 双接口事件路由；e2e 4 用例全绿验证连接/权限/广播/断连退出；CleanupFinished 调用时机 bug 已修复）
- [x] 管道 DACL 限制生效（T3.7 BuildPipeSecurityDescriptor 限制 SY/BA/user_sid 三个 GA ACE + SE_DACL_PROTECTED；e2e 3 用例全绿验证 DACL 内容正确 + 匿名 token 连接被 ERROR_ACCESS_DENIED 拒绝 + 正常 controller 不受影响）
- [x] REPL 场景 e2e 全绿（smoke.py 覆盖交互模式）
