# 进程管理重构设计（Process Manager Refactor）

> 状态：待实施（分 Phase 落地，见 §6）
> 适用范围：`src/process/` 包扩展、`src/pty/` 瘦身、`src/session/` 编排调整
> 关联文档：[ARCHITECTURE.md](../ARCHITECTURE.md)、win-sandbox 的 [ARCHITECTURE.md](../../../win-sandbox/docs/ARCHITECTURE.md)

---

## 1. 背景与问题

当前进程管理职责散落在 PTY 后端层，存在以下架构问题：

| # | 问题 | 现状 |
|---|------|------|
| 1 | **进程管理放在 pty 包** | `pty/windows/job.py`（ProcessJob，383 行）、`pty/windows/gui_monitor.py`（226 行）、`pty/unix/process.py`（254 行）混在 PTY 后端层 |
| 2 | **依赖方向反了** | `process/monitor.py` 通过 `pty_provider` 回调鸭子类型反依赖 PTY（调 `pty.get_job_notifications()` / `get_process_list()` / `get_child_process_exit_code()`） |
| 3 | **生命周期归 PTY** | `ProcessJob` 在 conpty/condrv 构造时创建、close 时销毁，Session 无法显式控制杀树与清理顺序 |
| 4 | **通知双轨** | Windows `JobNotification`（int 消息）与 Unix `UnixNotification`（str），`ProcessMonitor` 用 `getattr` 鸭子兼容 |
| 5 | **kill_tree 在 pty 层代理** | Session 调 `pty.kill_tree()`，PTY 内部再杀树；顺序（杀树→关 PTY→清理 Job）不可控 |
| 6 | **Unix waitpid 双收尸** | `pty_impl._try_waitpid` 与 `process._check_crash_internal` 同时 waitpid 直接子进程，靠 `_reaped` 标志避免竞争 |
| 7 | **无法委派** | winsandbox（独立项目）自行实现了 Job Object + 进程树 IPC（Phase 8/9），但无统一端口可接入，功能架构无法统一 |

---

## 2. 目标架构

扩展现有 `src/process/` 为进程管理包（洋葱模型四层）：

```
src/process/                       # ═══════ 进程管理层 ═══════
├── base.py                        # 实体层：ProcessNotification + ProcessTreeTracker 抽象端口
├── info.py                        # 已有：进程信息查询（_get_process_name / _get_process_detail / _get_process_tree ...）
├── monitor.py                     # 用例层：ProcessMonitor（改造：依赖 tracker，删鸭子代码）
├── gui.py                         # 用例层：GuiDetector（改造：依赖 tracker）
├── win32_error.py                 # 迁入：Windows NTSTATUS/Win32 错误码格式化（原 pty/windows/win32_error_msg.py）
├── windows/                       # ═══ Windows 平台实现（仅 Win32 加载） ═══
│   ├── __init__.py
│   ├── api.py                     # Job/GUI/进程查询 API ctypes 绑定（从 win32_api.py 按域拆出）
│   ├── job_tracker.py             # JobProcessTreeTracker（原 ProcessJob 主体 + IOCP 通知）
│   └── gui_monitor.py             # GuiWindowMonitor（原文件迁移，改为依赖 tracker.get_process_list()）
└── unix/                          # ═══ Unix 平台实现 ═══
    ├── __init__.py
    └── pgid_tracker.py            # PgidProcessTreeTracker（原 UnixProcessMonitor + waitpid 收尸统一）
```

依赖方向（只允许外层 → 内层）：

```
session/ ──> process/（monitor、gui、tracker 抽象与实现）  <── pty/（各后端注入 tracker）
process/monitor、gui ──> process/base（tracker 抽象）       ──> process/windows|unix（平台实现）
pty/ ──> process/（注入抽象 + 登记）                        （不再有 process ──> pty 的引用）
```

---

## 3. 核心接口设计

### 3.1 ProcessNotification（统一通知实体）

合并 Windows `JobNotification` 与 Unix `UnixNotification`：

```python
class ProcessNotification:
    type: str            # 统一字符串："spawn" | "exit" | "crash"
    pid: int
    exit_code: Optional[int]
    process_name: str    # Windows IOCP NEW_PROCESS 时尽力填充；Unix 为空
    process_path: str

    def is_spawn(self) -> bool
    def is_exit(self) -> bool
    def is_crash(self) -> bool
```

| 来源 | spawn | exit | crash |
|------|-------|------|-------|
| Windows IOCP | `NEW_PROCESS`（附 name/path） | `EXIT_PROCESS` | `ABNORMAL_EXIT_PROCESS` |
| Unix 轮询 | 进程列表 diff 新增 | diff 消失 / waitpid 正常退出 | waitpid 非 0 退出码 |

crash 的最终判定仍由 `ProcessMonitor` 负责（rc != 0 且 rc != STILL_ACTIVE），tracker 层只提供原始事件。

### 3.2 ProcessTreeTracker（抽象端口）

```python
class ProcessTreeTracker:
    # ── 登记：PTY spawn 成功后立即调用（解决逃逸耦合，见 §4.1）──
    def register_root(self, pid: int, hprocess: Optional[int] = None) -> bool

    # ── 进程树查询 ──
    def get_process_list(self) -> List[int]
    def get_process_count(self) -> int
    def is_root_alive(self) -> bool

    # ── 终止（Session 显式调用，PTY 不再杀树）──
    def kill_tree(self, timeout: float = 3.0)

    # ── 退出码 ──
    def get_root_exit_code(self) -> Optional[int]     # Unix 唯一 waitpid 收尸点
    def get_process_exit_code(self, pid: int) -> Optional[int]

    # ── 通知 ──
    def drain_notifications(self) -> List[ProcessNotification]

    # ── GUI 窗口（仅 Windows 实现有效，其余平台默认空实现）──
    def get_gui_windows(self) -> List[dict]
    def poll_gui_windows(self) -> List[dict]
    def close_gui_window(self, hwnd: int) -> bool

    # ── 生命周期：归 Session，PTY 不持有 ──
    def close(self)
```

平台实现：

- **JobProcessTreeTracker**（windows/job_tracker.py）：迁移原 `ProcessJob` 主体（命名 Job、KILL_ON_JOB_CLOSE + DIE_ON_UNHANDLED_EXCEPTION、IOCP 通知线程、进程列表/退出码查询），新增 `kill_tree()`（枚举 Job 内 PID → 逐个 TerminateProcess，超时兜底，与 winsandbox `TerminateAll` 语义一致）；聚合 `GuiWindowMonitor` 提供 GUI 三件套
- **PgidProcessTreeTracker**（unix/pgid_tracker.py）：迁移原 `UnixProcessMonitor`（pgid 追踪、killpg、/proc 扫描、轮询 diff），waitpid 收尸统一到此（`get_root_exit_code`），供 `pty.get_exit_code()` 委托

### 3.3 生命周期与所有权

```
Session（owner）
├── tracker（创建 → 注入 pty_factory → stop 时显式控制顺序）
│     kill_tree() → pty.close() → tracker.close()
└── pty（只依赖 ProcessTreeTracker 抽象，不持有/不销毁 tracker）
```

---

## 4. 关键决策

### 4.1 spawn 耦合解法：依赖注入 + register_root 登记点

**约束**：Windows 下 CreateProcess 返回后、子进程 spawn 孙进程前，必须把句柄 assign 进 Job，否则孙进程逃逸。

**方案**：Session 先创建 tracker → 注入 `create_pty(command, ..., tracker=tracker)` → PTY spawn 成功后**同一代码路径内同步调** `tracker.register_root(pid, pi.hProcess)`（内部即 AssignProcessToJobObject，Unix 侧为 getpgid 捕获）。

- 登记与 spawn 之间零异步窗口，逃逸问题从根上消除
- PTY 只依赖 `process.base.ProcessTreeTracker` 抽象（依赖倒置），可替换实现（Job / pgid / 未来 sandbox 委派）PTY 无感知
- 不需要回调注册、事件总线等间接机制（不过度工程）

### 4.2 Unix waitpid 收尸统一归 tracker

现 `pty_impl._try_waitpid` 与 monitor `_check_crash_internal` 双 waitpid 竞争。重构后：

- `PgidProcessTreeTracker` 是唯一 waitpid 直接子进程的地方（`get_root_exit_code` + crash 检测）
- `PseudoTerminal.get_exit_code()` 语义不变：Windows 用 `_ph` 句柄（GetExitCodeProcess）自持；Unix 委托 `tracker.get_root_exit_code()`

### 4.3 win32_api 按域拆分

`pty/windows/win32_api.py`（430 行）混装 PTY 与 Job/GUI 两类绑定。process/windows 需要 Job/GUI 绑定，直接跨层引用会形成 `pty → process` 与 `process → pty` 循环依赖（干净架构禁止）。

- Job Object（CreateJobObjectW / AssignProcessToJobObject / SetInformationJobObject / QueryInformationJobObject / IOCP 全家 / JOB_OBJECT_MSG_* / JOBOBJECT_* 结构）、进程查询（GetExitCodeProcess / QueryFullProcessImageNameW / OpenProcess）、GUI（EnumWindows / GetWindowThreadProcessId / GetWindowTextW / GetClassNameW / IsWindowVisible / SendMessageW / WM_CLOSE / WNDENUMPROC）→ 移入 `process/windows/api.py`
- `pty/windows/win32_api.py` 保留 PTY/控制台绑定（CreatePseudoConsole、ConDrv、管道、CreateProcess、属性列表、ntdll 等）
- 少量通用绑定（CloseHandle 等）两处各自声明（几行 ctypes 绑定，显式独立，优于跨层依赖）

### 4.4 win32_error_msg 迁入 process

`win32_error_msg.py` 引用方：`process/monitor.py`、`process/info.py`、`session/session.py`（主消费方）与 `pty/windows/*`（STILL_ACTIVE）。迁入 `process/win32_error.py`，pty/windows 引用点同步改 `from ...process.win32_error import STILL_ACTIVE`（pty → process 方向，合法）。

### 4.5 winsandbox 委派（原生 pybind11 直调）

`src/sandbox/` 以 winsandbox（独立项目，Job Object + Low IL 隔离 + pybind11 原生库）为完整沙箱后端，与原生 Job 后端共用 `ProcessTreeTracker` / `PseudoTerminal` 端口：

| ProcessTreeTracker | win_sandbox_native 能力 |
|---|---|
| `register_root` | SandboxSessionManager.start_process（进程天然入 Job） |
| `get_process_list` | Process.query_process_list |
| `get_process_exit_code` | Process.query_process_exit_code（活跃进程返回 STILL_ACTIVE(259)，映射为 None） |
| `kill_tree` | Process.terminate（KILL_ON_JOB 全灭） |
| `drain_notifications` | on_job_process_started/exited 回调入队（回调内只入队，禁调 C++ 方法） |
| `get_root_exit_code` | Process.wait(timeout_ms=0) 非阻塞探测（Job 退出回调排除根进程，native 端 notif.pid != process_.pid 过滤） |
| GUI 三件套 | 空实现（沙箱隔离下本地 EnumWindows 不适用） |

**结构**（`src/sandbox/`）：
- `manager.py`：`SandboxSessionManager` —— 原生沙箱实例会话（进程内直调 SandboxInstance/Process、回调通知流、quota/isolation 组装）
- `tracker.py`：`SandboxProcessTreeTracker` —— `ProcessTreeTracker` 端口实现，委托 manager
- `pty.py`：`SandboxPty` —— `PseudoTerminal` 端口实现（ConPtyHandle + 外部传入 hpcon，回显/方向键/resize/Ctrl+C 与原生 ConPTY 一致）

**集成方式**（用户决策：完整沙箱后端，非降级）：
- `process.create_process_tree_tracker()`：`[sandbox] enabled=true` → `SandboxSessionManager` + `SandboxProcessTreeTracker`；否则原生 Job（Windows）/ 进程组（Unix）
- `pty_factory._try_create_sandbox_pty()`：enabled=true 时要求 tracker 为沙箱实现，创建失败直接抛 `RuntimeError`，**不回退原生后端**（沙箱是安全边界，不允许静默失去隔离）
- 配置：`config/sandbox.toml`（`config/sandbox.py` 载入）：enabled / log_level / quota / isolation

**实现要点（修复记录）**：
1. win-sandbox 拒绝 quota 零值数值字段（`cpu_ms must be > 0`）→ `_build_quota()` 过滤 0 值字段
2. 命令封装禁用 POSIX 引号（shlex.quote 会把 `&&` 变 `'&&'`、吃掉反斜杠）→ `subprocess.list2cmdline`
3. CreatePseudoConsole 首参必须 COORD 按值传（x64 ABI），byref 会令 conhost 把指针地址当尺寸 → `ConPtyHandle`（src/pty/windows/conpty_handle.py）统一封装，WindowsPseudoTerminal 与 SandboxPty 共用
4. Job 退出回调排除根进程 → 根退出码经 `wait(timeout_ms=0)` 非阻塞探测 + 缓存
5. 原生库 vendored 于 `bin/win_sandbox/`（python 包 + `_native/*.pyd`），与旧 IPC 客户端（client/protocol/async_client）不共存，引用点全部更新

**非管理员环境限制**（当前开发机）：无管理员权限时无法向系统目录（`C:\Windows\System32`）写 ACL
（`SetNamedSecurityInfoW win32_err=5`），default_deny + System32 白名单模式不可用；
变通为 isolation 不带系统目录白名单（Low IL 默认即可读系统目录，Phase 16 起全盘只读 + 可写区），冒烟/集成测试使用该模式；
`sandbox.toml` 保持 enabled=false（默认不启用）。

**测试**：`tests/unit/sandbox/`（config / manager / tracker / pty，mock win_sandbox 原生实例）、
`tests/integration/test_sandbox.py`（真实 win_sandbox_native + ConPTY：回显 / 退出码 / 查询 / 终止全链路）。
测试模块统一 `test_sandbox_*` 前缀，避免与既存 `tests/unit/test_manager.py` 等撞名。

---

## 5. PseudoTerminal 瘦身

`src/pty/base.py` 删除：`kill_tree` / `get_process_list` / `get_child_process_exit_code` / `get_job_notifications` / `get_gui_windows` / `poll_gui_windows` / `close_gui_window`

保留：`get_type` / `read` / `drain` / `write` / `resize` / `close` / `fileno` / `get_child_pid` / `get_exit_code`（语义不变，见 §4.2）

`create_pty` 签名新增 `tracker: ProcessTreeTracker` 必填参数（Session 创建后注入）。

---

## 6. 分 Phase 实现计划

> 每个 Phase 独立可验证（pytest 通过后才进入下一 Phase）。测试迁移跟随代码迁移。

### Phase 1：抽象层 + 错误码迁移
- 新建 `src/process/base.py`（ProcessNotification + ProcessTreeTracker + GUI 空实现）
- 新建 `src/process/win32_error.py`（内容迁移），更新引用点：`process/monitor.py`、`process/info.py`、`session/session.py`、`pty/windows/conpty.py`、`condrv.py`、`__init__.py`
- 更新 `src/process/__init__.py` 导出
- 测试：迁移 `tests/unit/pty/windows/test_error_msg.py`、`tests/unit/test_windows_error.py` → `tests/unit/process/`
- 验收：pytest 全量通过

### Phase 2：Windows 平台实现
- 新建 `src/process/windows/api.py`（按域拆出，见 §4.3）
- 新建 `src/process/windows/job_tracker.py`（JobProcessTreeTracker：迁移 ProcessJob 主体 + kill_tree 实现 + GUI 组合）
- 新建 `src/process/windows/gui_monitor.py`（迁移 + 改依赖 tracker.get_process_list()）
- 精简 `pty/windows/win32_api.py`
- 测试：迁移 `tests/unit/pty/windows/test_job.py`、`tests/unit/test_job.py`、`tests/unit/test_gui_monitor.py` → `tests/unit/process/windows/`
- 验收：pytest 全量通过

### Phase 3：Unix 平台实现
- 新建 `src/process/unix/pgid_tracker.py`（迁移 UnixProcessMonitor + waitpid 收尸统一：get_root_exit_code）
- 测试：迁移 `tests/unit/pty/unix/test_process.py` → `tests/unit/process/unix/`
- 验收：pytest 全量通过

### Phase 4：上层编排改造
- `process/monitor.py`：`pty_provider` → `tracker` 依赖（drain_notifications/check_events/reset 全部走 tracker），删 `getattr` 鸭子代码
- `process/gui.py`：`check(pty, ...)` → `check(tracker, ...)`
- 测试：更新 `tests/unit/session/process/test_monitor.py`、`test_gui.py`、`tests/unit/test_process_monitor.py`
- 验收：pytest 全量通过

### Phase 5：PTY 后端瘦身 + 工厂注入
- `pty/base.py` 按 §5 瘦身
- `pty/windows/conpty.py`、`condrv.py`：删 `_job` / `_gui_monitor`，spawn 后 `tracker.register_root(pid, pi.hProcess)`；GUI 方法删除
- `pty/unix/pty_impl.py`：删 `_monitor`，fork 后 `tracker.register_root(pid)`；`get_exit_code` 委托 tracker
- `pty_factory.py`：`create_pty(..., tracker=...)`
- 删除旧文件：`pty/windows/job.py`、`pty/windows/gui_monitor.py`、`pty/unix/process.py`；更新 `pty/windows/__init__.py`、`pty/unix/__init__.py`
- 测试：更新 `tests/unit/pty/test_base.py`、`test_factory.py`、`test_pty_*.py`
- 验收：pytest 全量通过

### Phase 6：Session 编排 + 引用点收口
- `session/session.py`：从 `process` 包导入 `create_process_tree_tracker()` 工厂创建 tracker（平台分支 + 沙箱委派）、注入 create_pty；`stop()` 顺序改为 `tracker.kill_tree() → pty.close() → tracker.close()`；start/wait/close_gui_window/process_list 改走 tracker
- `session/session_threads.py`：SessionComponents 增加 tracker；monitor loop 的 `get_process_list` 走 tracker
- `output/events.py`：`check_existence` 的 `pty_provider` → tracker
- 测试：更新 `tests/unit/session/test_*.py`、`tests/integration/test_session.py`
- 验收：pytest 全量 + `tests/integration/test_session.py` 通过

### Phase 7：文档同步 + 全量回归
- 更新 `docs/ARCHITECTURE.md`（§3.1 目录树：process/ 扩展、pty/ 瘦身、分层图）
- 更新 `docs/filestree/src.md`（process/pty 段）
- 全量回归：pytest + e2e 冒烟
- 验收：全部测试通过，文档与真实文件系统一致

---

## 7. 风险与注意

1. **行为等价性**：通知消费路径重写后，`ProcessMonitor` 的 crash 判定条件（rc != 0 且 rc != STILL_ACTIVE）保持不变；IOCP 通知的 name/path 填充逻辑原样迁移
2. **kill_tree 语义变化**：Windows 从"关 Job 句柄（KILL_ON_JOB_CLOSE）杀树"改为"枚举 PID + TerminateProcess 杀树"，随后 `tracker.close()` 关句柄兜底——树被杀干净，且 kill_tree 后 tracker 仍可查询（行为增强）；`_reap_child` 逻辑从 pty_impl 迁出后，`close()` 依赖 tracker.is_root_alive 的路径需同步调整
3. **stop 顺序**：`tracker.kill_tree()` 先于 `pty.close()`（ConPTY 关闭需要子进程死透），`tracker.close()` 最后
4. **VNC 排除**：`vnc/process_manager.py` 自带全局 Job Object 绑定（仅 KILL_ON_JOB_CLOSE 保活语义，独立自包含），不属于会话进程树管理，不迁移
5. **sandbox 目录**：`src/sandbox/` 为 winsandbox 委派实现（见 §4.5）；未启用（enabled=false）时零加载，启用后创建失败抛 `RuntimeError` 不回退原生后端
6. **condrv.py**（1128 行，当前 ConDrv 直连路径）与 conpty.py 同步改造，防止两套代码行为漂移

---

## 8. 验收标准

- [ ] `src/process/` 成为完整进程管理包，`src/pty/` 不再含任何进程管理/Job/GUI 代码
- [ ] `process/` 包内不存在 `pty` 的 import（依赖方向单向）
- [ ] `ProcessNotification` 单一定义，`ProcessMonitor` 无 `getattr` 鸭子代码
- [ ] `kill_tree` 只存在于 `ProcessTreeTracker` 及 Session 编排，PTY 无 kill_tree
- [ ] Session.stop 顺序可控：kill_tree → pty.close → tracker.close
- [x] winsandbox 能力与端口映射表成立（§4.5），Session/PTY 零感知接入（55 单测 + 9 集成 + e2e 冒烟通过）
- [ ] 全部 pytest + e2e 通过，无行为回归
