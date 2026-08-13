# win-sandbox API 参考

Python API 完整参考。涵盖 `SandboxInstance`（沙箱管理器）、`SandboxProcess`（进程对象）、事件回调与 payload 结构、`quota` / `isolation_policy` schema、配置文件 schema、异常类型、管道 helper 与工具函数。

---

## 1. 快速导航

| 内容 | 章节 |
|------|------|
| 安装 | §2 |
| 沙箱管理器 `SandboxInstance` | §3 |
| 进程对象 `SandboxProcess` | §4 |
| 事件回调接口 | §5 |
| 事件 payload 结构 | §6 |
| `quota` / `isolation_policy` schema | §7 |
| 配置文件 schema | §8 |
| 异常类型 | §9 |
| 管道 helper 与工具函数 | §10 |

---

## 2. 安装

```powershell
pip install win-sandbox
```

`import win_sandbox` 自动加载 `win_sandbox_native.pyd`（pybind11 编译的 C++ 核心，wheel 打包时注入），无需额外配置。源码部署与构建见 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 3. SandboxInstance（沙箱管理器）

### 3.1 构造

```python
sb = win_sandbox.SandboxInstance(
    config: str | os.PathLike | None = None,  # 可选配置文件路径
    log_level: str = "info",                   # trace|debug|info|warn|error
)
```

`config` 非法路径或配置解析失败抛 `SandboxError`。`log_level` 覆盖配置文件中的 `logging.level`。

### 3.2 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `capabilities` | dict | 权限能力报告（见 §6.1），构造后立即可用 |
| `process_count` | int | 当前托管进程数 |

### 3.3 方法

#### `start_process`

```python
proc = sb.start_process(
    command_line: str,                 # 完整命令行（含可执行路径）
    *,
    working_dir: str | None = None,    # 工作目录（None = 继承父进程）
    env_vars: dict[str, str] | None = None,  # 额外环境变量
    inherit_env: bool = True,          # 是否继承父进程环境变量
    quota: dict | None = None,         # 资源配额（见 §7.1）
    isolation_policy: dict | None = None,  # 隔离策略（见 §7.2）
    interactive: bool = False,         # 交互模式（保留 stdin 句柄）
    stream_buffer_size: int = 0,       # 管道缓冲大小（0=64KB；上限 64MB，超限拒绝）
    stdin_data: bytes | None = None,   # 启动时一次性写入 stdin（仅 interactive=False）
    request_id: int | None = None,     # 调用方关联 ID（透传到 proc.request_id，仅用于关联，不参与沙箱逻辑）
    hpcon: int | None = None,          # 外部创建的 ConPTY 句柄（CreatePseudoConsole 返回值）；
                                       # 传入后进程以伪控制台运行，stdio 句柄全部为 None
) -> SandboxProcess
```

注意：
- `working_dir` / `env_vars` / `quota` / `isolation_policy` 为 `None` 时使用配置文件兜底
- `stdin_data`：bytes 直接写入 stdin 管道；interactive=True 时忽略，由调用方后续 `write_pipe` 写入
- `stream_buffer_size` 会 commit 全部内存，按进程×流数线性增加占用，仅测试用；上限 64MB（过大拒绝，防误配置）
- `hpcon` 非空时进入 ConPTY 模式：stdin/stdout/stderr 句柄由伪控制台驱动管理，`proc.stdin_handle` / `stdout_handle` / `stderr_handle` 均为 `None`，读写经 ConPTY 管道由调用方完成；该模式仍应用隔离 token（Low IL）+ Job 限制
- 调用方应传完整绝对路径（不展开环境变量）

#### `list_processes`

```python
procs = sb.list_processes() -> list[dict]
```

返回当前托管进程列表，每项为 dict（含 `process_id` / `pid` / `command` / `state` 等）。

#### `cleanup_finished`

```python
sb.cleanup_finished()
```

清理已退出进程的内部条目（释放 Job/配额等资源）。`start_process` 入口自动执行一次，通常无需手动调用；进程数长时间高水位且需及时释放资源时可显式调用。

#### `shutdown`

```python
sb.shutdown()
```

终止全部进程、清理资源（Job / 隔离 token / 可写区 / ETW session / 句柄）。RAII 析构也会自动调用，但建议显式调用以及时释放。

### 3.4 上下文管理器

```python
with win_sandbox.SandboxInstance(log_level="info") as sb:
    proc = sb.start_process(command_line="cmd.exe /c echo hello")
    # ...
# __exit__ 自动调用 shutdown()
```

### 3.5 完整示例

```python
import win_sandbox

sb = win_sandbox.SandboxInstance(log_level="info")
print("capabilities:", sb.capabilities)

proc = sb.start_process(
    command_line="cmd.exe /c echo hello",
    quota={"memory_mb": 128, "wall_clock_timeout_ms": 15000},
)

def on_out(data):
    print(data.decode("utf-8", "replace"), end="")
win_sandbox.drain_stdout(proc, on_out).join()

exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
print(f"\nexit_code={exit_code}, reason={exit_reason}")
print(f"usage={usage}")

sb.shutdown()
```

---

## 4. SandboxProcess（进程对象）

`sb.start_process(...)` 返回的进程对象，持有 C++ `ProcessEntry` 引用。

### 4.1 句柄属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `process_id` | int | 沙箱内部进程 ID |
| `pid` | int | OS 进程 PID |
| `request_id` | int \| None | `start_process` 传入的关联 ID（未传为 None）；仅用于调用方关联，不参与沙箱逻辑 |
| `process_handle` | int | 进程 HANDLE 值（int，可 ctypes 操作） |
| `stdin_handle` | int \| None | stdin 写端句柄（仅 interactive=True 时非 None） |
| `stdout_handle` | int | stdout 读端句柄 |
| `stderr_handle` | int | stderr 读端句柄 |

> 句柄为 OS 句柄值（int），Python 用 `win_sandbox.read_pipe` / `write_pipe` / `close_handle` 操作，或用 `ctypes` 直接调用 Win32 API。
> `Process` 暴露的 `process_handle` / `stdin_handle` / `stdout_handle` / `stderr_handle` 由库内部管理，禁止用 `close_handle` 关闭（双重关闭可误关句柄值被系统复用后的无关对象）；用 `proc.close()` / `proc.close_stdin()` 代替。

### 4.2 回调 setter

均为 `Callable[[dict], None]`，设为 `None` 表示取消回调。详见 §5。

| setter | 触发时机 |
|--------|----------|
| `on_resource_limit` | Job 资源限制命中 |
| `on_job_process_started` | Job 内子/孙进程创建 |
| `on_job_process_exited` | Job 内子/孙进程退出 |
| `on_behavior_event` | ETW 行为事件批量上报 |
| `on_access_denied` | ETW access_denied 事件 |

### 4.3 方法

#### `wait`

```python
exit_code, exit_reason, resource_usage = proc.wait(timeout_ms: int = -1) -> tuple[int, str, dict]
```

阻塞等待进程退出。`timeout_ms=-1` 表示无限等待。返回三元组：
- `exit_code`：进程退出码（int32；崩溃为 NTSTATUS 负数）
- `exit_reason`：退出原因字符串（见 §6.4）
- `resource_usage`：资源使用统计 dict（见 §6.4 `resource_usage` 字段）

超时抛 `SandboxTimeoutError`。

#### `terminate`

```python
proc.terminate(exit_code: int = 1)
```

强制终止进程（Job `TerminateAll` 语义，KILL_ON_JOB 确保子进程全灭）。

#### `signal`

```python
proc.signal(sig: str)   # "ctrl_break" | "kill"
```

- `ctrl_break`：CTRL_BREAK_EVENT 定向投递到子进程组（子进程可捕获）。
  注意：未注册 CTRL_BREAK handler 的子进程会被默认 handler 以退出码
  `0xC000013A`（STATUS_CONTROL_C_EXIT）终止——该退出码同时用于 Ctrl+C 与
  Ctrl+Break 的默认终止，属 Windows 控制台控制事件的统一终止状态，并非信号
  投递失败。若要优雅处理，子进程需注册 `SetConsoleCtrlHandler` 并捕获
  `CTRL_BREAK_EVENT`。
- `kill`：TerminateProcess 强制终止（不可捕获）

`sig` 非法值抛 `ValueError`。

> 不支持 `ctrl_c`：Windows 上 CTRL_C_EVENT 无法定向投递，只能广播（会命中调用方自身）。

#### `close_stdin`

```python
proc.close_stdin()
```

关闭 stdin 写端（子进程 ReadFile 立即 EOF）。interactive 模式下写完 stdin 后调用。

#### `query_accounting`

```python
info = proc.query_accounting() -> dict
```

查询 Job 会计信息（CPU 时间 / IO / 页面错误 / 进程数），返回 dict（见 §6.5 形态 A）。

#### `query_peak_memory`

```python
peak = proc.query_peak_memory() -> int
```

查询峰值内存（bytes）。

#### `query_process_list`

```python
pids = proc.query_process_list() -> list[int]
```

查询该进程所属 Job 内所有进程的 OS PID 列表（含进程自身及 Job 内子进程，未 breakaway 逃逸）。

- 进程已退出后查询 → 空列表（Job 内 pid 清理可能有短暂延迟，建议轮询）
- 进程不存在 → 抛异常（`process_not_found`）

#### `query_process_exit_code`

```python
exit_code, is_active = proc.query_process_exit_code(pid: int) -> tuple[int, bool]
```

查询指定 Job 内任意 PID 的退出码。

- 运行中 → `exit_code=259`（STILL_ACTIVE）、`is_active=true`；已退出 → 最终退出码、`is_active=false`
- **时序约束**：已退出进程的进程对象在退出后仅存活短暂窗口（console 子系统等持有引用，约 100ms），窗口过后 `OpenProcess` 失败 → 抛 `query_failed`；建议在收到 `on_job_process_exited` 回调 / `wait()` 返回后立即查询
- **Job 归属校验**：`pid` 必须是本进程对应 Job 的进程（含已退出进程），不属于 → 抛 `process_not_found`。跨 sandbox 实例的 pid 探测被拒绝
- `pid` 不属于本 Job 但进程对象已回收 → 抛 `query_failed`；缺字段或类型错误（如 float，`pid` 须为整数）→ 抛 `invalid_payload`

#### `close`

```python
proc.close()
```

关闭进程对象持有的句柄（stdin/stdout/stderr）。`wait()` 返回后自动调用，通常无需显式调用。

---

## 5. 事件回调接口

pybind11 in-process 形态下，事件通过 `SandboxProcess` 的 setter 回调推 Python。回调签名为 `Callable[[dict], None]`，payload 为 dict（pybind11 自动转换 `nlohmann::json` ↔ Python `dict`）。

回调在 C++ IOCP / ETW 分发线程中执行，pybind11 自动获取 GIL。回调实现须注意：
- 不要在回调中阻塞（会阻塞 IOCP 线程，影响后续通知）
- 不要在回调中调用 `proc.wait()` 等阻塞方法（死锁）
- 需要重处理时建议把 payload 放入 `queue.Queue`，由主线程消费

| setter | 触发时机 | payload 结构 |
|--------|----------|--------------|
| `on_resource_limit` | Job 资源限制命中（CPU/内存/进程数） | §6.5 |
| `on_job_process_started` | Job 内子/孙进程创建（主进程不触发） | §6.12 |
| `on_job_process_exited` | Job 内子/孙进程退出（主进程不触发） | §6.13 |
| `on_behavior_event` | ETW 行为事件批量上报 | §6.7 |
| `on_access_denied` | ETW access_denied 事件 | `{"pid": int, "path": str, "operation": str, "source": "etw"}` |

> stdout/stderr 不走回调，通过管道句柄由 Python 自行读取（见 §10）。

---

## 6. 事件 payload 结构

### 6.1 `capabilities`（SandboxInstance.capabilities 属性）

`SandboxInstance` 构造后立即可用，描述当前权限模式下各模块可用性。

```json
{
    "mode": "standard_user",
    "capabilities": [
        {"module": "job_object", "available": true},
        {"module": "low_il_token", "available": true},
        {"module": "etw", "available": false, "degraded_reason": "non-admin: degraded to process polling + dir file watch + network polling (no registry events)"},
        {"module": "network", "available": false, "degraded_reason": "non-admin: WFP connect filter unavailable, only net_policy=unrestricted"},
        {"module": "pipe_security", "available": true}
    ]
}
```

字段说明：
- `mode`：权限模式（`admin` 或 `standard_user`）
- `capabilities`：**模块列表**，每项 `{module, available, degraded_reason?}`
  - `degraded_reason` 仅在 `available=false` 时存在
  - `available=false` 表示该模块当前不可用（如非管理员下 ETW / WFP 网络受限），
    对应能力会降级——**调用方应据此调整预期**，不要假设所有隔离策略都能生效
- 模块清单：
  - `job_object`：Job 对象资源控制，始终可用
  - `low_il_token`：Low IL 隔离 token（DuplicateTokenEx + SetTokenInformation IL=Low + SetNamedSecurityInfo 打标），纯用户态，始终可用
  - `etw`：ETW 行为监控，仅管理员；非管理员降级为轮询 + 目录监控 + 网络轮询（无注册表事件）
  - `network`：WFP 网络限制（allowlist/SOCKS5 callout），仅管理员；非管理员仅可用 `net_policy=unrestricted`
  - `pipe_security`：管道 DACL 保护，始终可用

### 6.2 `process_started`（start_process 返回的 proc 属性）

`start_process` 返回 `SandboxProcess` 对象，含以下属性：

| 属性 | 说明 |
|------|------|
| `process_id` | 沙箱内部 ID |
| `pid` | OS PID |
| `process_handle` | 进程 HANDLE |
| `stdin_handle` / `stdout_handle` / `stderr_handle` | 管道句柄 |

> 主进程可执行文件名与完整路径可通过 `proc.process_path`（如暴露）或 `query_accounting()` 获取。

### 6.3 `process_output`（管道读取）

stdout/stderr 通过 `win_sandbox.read_pipe(proc.stdout_handle, size)` 读取，返回 `bytes`。EOF 时返回空 `b""`。

或用 `win_sandbox.drain_stdout(proc, callback)` / `drain_stderr(proc, callback)` 起后台线程排空，回调签名 `callback(data: bytes) -> None`，EOF 后线程自动结束。

### 6.4 `process_exited`（wait 返回值）

`proc.wait()` 返回三元组 `(exit_code, exit_reason, resource_usage)`：

- `exit_code`：进程最终退出码（int32）。崩溃进程（未处理异常，含 `crash_silent=true`）返回 NTSTATUS 崩溃码，如空指针解引用 `0xC0000005`（Python 中为负数 -1073741819）。
- `exit_reason`：退出原因字符串，取值 `normal` / `crash` / `cpu_limit` / `memory_limit` / `process_count_limit` / `wall_clock_timeout` / `killed_by_user` / `pipe_closed` / `unknown`
  - `crash`：进程未处理异常崩溃（退出码为 NTSTATUS 异常段 0xC0000000-0xCFFFFFFF）；`STATUS_CONTROL_C_EXIT (0xC000013A)` 除外（它是 Ctrl+C/Ctrl+Break 默认终止，语义归 `normal`）
- `resource_usage`：资源使用统计 dict

`resource_usage` 结构（嵌套分组：cpu / io / memory / processes / page_faults / sample_time_ms）：

```json
{
    "sample_time_ms": 1786633508807,
    "cpu": {
        "total_user_ms": 15,
        "total_kernel_ms": 10,
        "period_user_ms": 0,
        "period_kernel_ms": 15
    },
    "io": {
        "read_ops": 0,
        "write_ops": 2,
        "other_ops": 0,
        "read_bytes": 0,
        "write_bytes": 2048,
        "other_bytes": 0
    },
    "processes": {
        "total": 1,
        "active": 1,
        "terminated": 0
    },
    "memory": {
        "peak_process_bytes": 1234567,
        "peak_job_bytes": 2345678
    },
    "page_faults": 42
}
```

- `sample_time_ms`：采样时刻（Unix ms）
- `cpu`：用户态/内核态 CPU 时间（total 为 Job 累计，period 为本采样周期）
- `io`：读写/其他字节数与操作次数
- `processes`：Job 内进程数（total / 当前 active / 已 terminated）
- `memory`：峰值内存（`peak_process_bytes` 单进程、`peak_job_bytes` Job 合计）
- `page_faults`：总页面错误数

> `exit_kind`：正常/异常二元分类，
> `normal`（退出码 0）/ `abnormal`（退出码非零，含崩溃 NTSTATUS）。
> 该字段与 `exit_reason` 正交：`exit_reason` 表达"谁/因何退出"（自行退出/被沙箱杀），
> `exit_kind` 表达"退出是否异常"（0 / 非 0）。可通过 `exit_code == 0` 判断。

### 6.5 `resource_limit_hit`（on_resource_limit 回调 payload）

```json
{
    "process_id": 1,
    "pid": 1234,
    "limit_type": "cpu_time" | "memory" | "process_count" | "unknown",
    "notification_type": 3,
    "limit": "cpu_ms" | "memory_mb" | "job_memory_mb" | "max_processes" | "unknown",
    "value": 128
}
```

- `limit_type`：通知类别（字符串；源码 `JobNotificationType` 的翻译）
- `notification_type`：`JobNotificationType` 枚举的 int 值（`EndOfJobTime`=0、`EndOfProcessTime`=1、`ActiveProcessLimit`=2、`ProcessMemoryLimit`=3、`JobMemoryLimit`=4，完整见 `src/core/entities/JobNotification.hpp`）
- `limit` / `value`：落到哪个配额字段及其配置值；`value` 仅在存在对应配额时出现（如 CPU 配额全部未配置时只发 `limit_type` 无 `value`）
- 触发后被杀的进程会再触发 `wait()` 返回（`exit_reason=*_limit` 对应值）

### 6.6 `query_accounting` 返回值

`proc.query_accounting()` 返回 dict，结构（与 `resource_usage` 相同的分组形态，另含活动进程数）：

```json
{
    "sample_time_ms": 1786633508807,
    "cpu": {
        "total_user_ms": 15,
        "total_kernel_ms": 10,
        "period_user_ms": 0,
        "period_kernel_ms": 15
    },
    "io": {
        "read_ops": 0,
        "write_ops": 2,
        "other_ops": 0,
        "read_bytes": 0,
        "write_bytes": 2048,
        "other_bytes": 0
    },
    "processes": {
        "total": 1,
        "active": 1,
        "terminated": 0
    },
    "memory": {
        "peak_process_bytes": 1234567,
        "peak_job_bytes": 2345678
    },
    "page_faults": 42
}
```

`proc.query_peak_memory()` 返回 `int`（峰值内存 bytes）。

### 6.7 `behavior_log`（on_behavior_event 回调 payload）

```json
{
    "events": [
        {
            "type": "process_start",
            "pid": 1234,
            "tid": 5678,
            "ts": 1722112345,
            "seq": 1,
            "image_path": "C:\\Windows\\System32\\cmd.exe",
            "cmdline": "cmd.exe /c echo hello",
            "ppid": 1,
            "file_path": "...",
            "key_path": "...",
            "value_name": "...",
            "local_addr": "...",
            "local_port": 0,
            "remote_addr": "...",
            "remote_port": 0,
            "operation": "...",
            "gap_count": 0
        }
    ]
}
```

- `events`：行为事件数组（一次回调打包多条）
- 基础字段：`type`（字符串）、`pid`、`tid`、`ts`（事件时间，Unix ms）、`seq`（序号）
- **可选字段**：非零/非空才出现——`ppid`、`image_path`、`cmdline`、`file_path`、`key_path`、`value_name`、`local_addr`、`remote_addr`、`local_port`、`remote_port`、`operation`、`gap_count`

行为事件类型（`BehaviorEventType`）：

| 类型字符串 | 说明 | 关键字段 |
|-----------|------|---------|
| `process_start` | 进程创建 | image_path, cmdline, ppid |
| `process_stop` | 进程退出 | image_path |
| `image_load` | 模块加载 | image_path |
| `file_create` | 文件创建/打开 | file_path |
| `file_write` | 文件写入 | file_path |
| `file_delete` | 文件删除 | file_path |
| `registry_set_key` | 注册表写值 | key_path, value_name |
| `tcp_connect` | TCP 连接 | local_addr, local_port, remote_addr, remote_port |
| `udp_send` | UDP 发送 | local_addr, remote_addr, remote_port |
| `access_denied` | 访问拒绝 | operation |
| `gap_detected` | 丢包检测 | gap_count |
| `unknown` | 未知 | — |

> AccessDenied 类型事件除进入 `on_behavior_event` 外，还会**额外**触发 `on_access_denied` 回调（payload：`pid`/`path`/`operation`/`source="etw"`），便于即时告警。

### 6.8 `access_denied`（on_access_denied 回调 payload）

```json
{
    "pid": 1234,
    "path": "C:\\Protected\\file.txt",
    "operation": "write",
    "source": "etw"
}
```

### 6.9 `query_process_list` 返回值

`proc.query_process_list()` 返回 `list[int]`（OS PID 列表，含进程自身及 Job 内子进程，未 breakaway 逃逸）。

- 进程已退出后查询 → 空列表
- 进程不存在 → 抛异常（`process_not_found`）

### 6.10 `query_process_exit_code` 返回值

`proc.query_process_exit_code(pid)` 返回二元组 `(exit_code, is_active)`：

```python
exit_code, is_active = proc.query_process_exit_code(5678)
# exit_code: int, is_active: bool
```

- `is_active`：`true` 表示进程仍在运行（`exit_code` 为 STILL_ACTIVE=259）；`false` 表示已退出
- **固有歧义**：进程恰好以退出码 259 正常退出时 `is_active` 误判为 true——Win32 `GetExitCodeProcess` 的既有约定，非本功能引入；实践中以 259 为退出码的程序罕见
- 错误情况抛异常（`process_not_found` / `query_failed` / `invalid_payload`，见 §4.3 `query_process_exit_code`）

### 6.11 `job_process_started`（on_job_process_started 回调 payload）

Job 内子/孙进程创建实时通知（主进程不触发，避免与 `start_process` 返回重复）。

```json
{
    "process_id": 1,
    "pid": 5678,
    "process_name": "ping.exe",
    "process_path": "C:\\Windows\\System32\\ping.exe",
    "parent_pid": 1234,
    "timestamp_ms": 1722112345678
}
```

- `process_id`：沙箱内部 ID（标识所属 Job）
- `pid`：新加入 Job 的进程 OS PID（**不含主进程**——主进程只走 `start_process` 返回，避免重复）
- `process_name` / `process_path`：进程名与完整路径（NEW_PROCESS 时查询填充）
- `parent_pid`：父进程 PID（best-effort；父进程已退出时**省略**该字段，不写 null）
- 注意：控制台应用启动时 Windows 会创建 `conhost.exe` 并加入 Job，回调中会看到 conhost 事件（合法，其 pid ≠ 主 pid）

### 6.12 `job_process_exited`（on_job_process_exited 回调 payload）

Job 内子/孙进程退出实时通知（主进程不触发）。

```json
{
    "process_id": 1,
    "pid": 5678,
    "exit_code": 7,
    "exit_kind": "abnormal",
    "timestamp_ms": 1722112345678
}
```

- `exit_kind`：`"normal"`（退出码 0）/ `"abnormal"`（退出码非 0，含崩溃 NTSTATUS）/ `"unknown"`（兜底：退出码查询失败时使用，此时**省略 `exit_code` 字段**，不写 null——客户端须用 `payload.get("exit_code")` 访问）
- 崩溃路径（ABNORMAL_EXIT + EXIT 双通知）同一 pid **仅下发一次**（既有 `exited_pids_` 去重）
- 主进程不产生此事件（其退出走 `wait()` 返回）

---

## 7. `quota` / `isolation_policy` schema

### 7.1 `quota`

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `cpu_ms` | int | — | CPU 时间上限（ms）。**精度说明**：基于 Windows Job `JOB_OBJECT_LIMIT_JOB_TIME`，实际终止延迟与内核调度粒度相关（约 5-7s 波动）——小于 ~2s 的 CPU 配额可能无法精确兑现，但限制必然触发（`exit_reason=cpu_limit`） |
| `cpu_rate_percent` | int | — | CPU 速率限制（1-100，需管理员） |
| `memory_mb` | int | — | 单进程内存上限（MB） |
| `job_memory_mb` | int | — | Job 总内存上限（MB） |
| `max_processes` | int | — | 最大活动进程数 |
| `wall_clock_timeout_ms` | int | — | 墙钟超时（ms，内建实现：`start_process` 自动挂定时器，到期 `Terminate`，`exit_reason=wall_clock_timeout`） |
| `cpu_timeout_ms` | int | — | CPU 超时（ms） |
| `no_ui` | bool | 默认 true | 限制跨进程 UI 访问（Job UI restriction：禁止读取其他进程窗口句柄、修改系统参数/显示设置）。**不含剪贴板**——剪贴板由 `isolation_policy.clipboard_isolate` 单独控制（避免默认值误伤剪贴板可用性）。**不阻止沙箱内进程创建自己的顶层窗口**——`no_ui=true` 不等于无头，需要无头请用 `create_no_window`/非交互模式 |
| `breakaway_ok` | bool | — | 允许子进程脱离 Job |
| `crash_silent` | bool | 默认 false | 崩溃静默。true 时启用 Job `DIE_ON_UNHANDLED_EXCEPTION`，Job 内进程未处理异常（崩溃）直接终止、不弹 Windows 错误对话框、不触发 WER 挂起；退出码为崩溃的 NTSTATUS（如空指针解引用 `0xC0000005`，int32 负数） |

> 注意：`io_rate_bytes_per_sec` / `io_rate_iops` **仅存在于配置文件** `default_quota`
> 中（见 §8），`start_process` 的 `quota` 不接受这两个字段（解析期拒绝未知字段）。

### 7.2 `isolation_policy`

| 字段 | 类型 | 说明 |
|------|------|------|
| `net_policy` | str | `unrestricted`（默认）/ `allowlist`。**`allowlist` 依赖 WFP callout（需管理员）**：非管理员下 WFP 不可用，`allowlist` 不生效并记 Warn 降级，进程网络不受限（语义等同 `unrestricted`，见 §6.1 `network` 模块） |
| `net_allowlist` | list[dict] | 网络白名单 `{"ip": str, "port": int, "protocol": int}`（仅 `net_policy=allowlist` 时生效）。`ip` 空 = 任意 IP；`port` 0 = 任意端口；`protocol` 6 = TCP、17 = UDP、0 = 任意 |
| `clipboard_isolate` | bool | 默认 false。true 时启用 Job UI 限制（剪贴板 / 全局原子表 / 系统参数不可访问） |

示例：

```python
isolation_policy={
    "net_policy": "allowlist",
    "net_allowlist": [{"ip": "10.0.0.1", "port": 443, "protocol": 6}],
    "clipboard_isolate": True,
}
```

---

## 8. 配置文件 schema

`SandboxInstance(config=path)` 加载 JSON 配置文件。

```json
{
    "logging": {
        "level": "debug",
        "dir": "%LOCALAPPDATA%\\win-sandbox\\logs",
        "retention_days": 7
    },
    "default_quota": {
        "cpu_ms": 30000,
        "cpu_rate_percent": 50,
        "memory_mb": 512,
        "job_memory_mb": 1024,
        "io_rate_bytes_per_sec": 10485760,
        "io_rate_iops": 1000,
        "max_processes": 16,
        "wall_clock_timeout_ms": 60000,
        "cpu_timeout_ms": 45000,
        "no_ui": true,
        "breakaway_ok": false,
        "crash_silent": false
    },
    "isolation": {
        "net_policy": "allowlist",
        "net_allowlist": [
            {"ip": "10.0.0.1", "port": 0, "protocol": 0}
        ],
        "clipboard_isolate": false
    },
    "monitoring": {
        "etw_enabled": true,
        "ring_buffer_size": 10000,
        "dispatch_batch_size": 100,
        "dispatch_timeout_ms": 10,
        "stats_interval_ms": 5000,
        "filter_pids": [1234, 5678]
    },
    "silo": {
        "enabled": false
    },
    "global_quota": {
        "enabled": false,
        "pool_name": "win-sandbox-quota",
        "max_cpu_rate_percent": 60,
        "max_memory_mb": 4096,
        "max_processes": 64
    }
}
```

### 各段默认值与缺省行为

- `logging`：`level=info`、`retention_days=7`；`dir` 空 = 默认 `%LOCALAPPDATA%\win-sandbox\logs`（`%LOCALAPPDATA%` 缺失时回退系统临时目录 `%TEMP%\win-sandbox-logs`）
- `monitoring`：**`etw_enabled=false`**（ETW 行为监控默认关闭）；`ring_buffer_size=10000`、`dispatch_batch_size=100`、`dispatch_timeout_ms=10`、`stats_interval_ms=5000`、`filter_pids=[]`（非空时仅采集这些 pid 的行为事件，其余过滤）
- `silo`：`enabled=false`
- `global_quota`：`enabled=false`；启用后按 `pool_name` 池化限额，`max_cpu_rate_percent`（1-100）、`max_memory_mb`、`max_processes` 为池上限
- `isolation`（Low IL 模型的默认隔离策略，作为 `start_process` 未显式传 `isolation_policy` 时的默认值）：缺省 `net_policy=unrestricted`、`net_allowlist=[]`、`clipboard_isolate=false`；`net_policy=allowlist` 时按 `net_allowlist` 白名单放行（见 §7.2）

### 配置校验规则

- 未知字段 → 配置加载失败抛异常，提示 `unknown field: <path> (strict mode)`
- 非法枚举值 → 加载失败并提示 allowed 列表
- 路径字段支持 `%VAR%` 展开（`start_process` 的 `working_dir` / `env_vars` 中不展开）；**无法展开的变量（未定义/未闭合/畸形）→ 拒绝加载**，不会按字面创建目录
- `default_quota` 的整型 size 字段有上界（2^40，约 1TB/34 年；`max_processes` 上界 65536），超界拒绝
- `isolation.net_allowlist` 条目须为对象（`ip` 须为字符串、`port`/`protocol` 须为非负整数），缺失/类型不符的字段忽略并取默认（`ip`=任意、`port`=0、`protocol`=0）
- `logging.retention_days` 上界 36500（100 年）
- `default_quota` 中 `io_rate_bytes_per_sec` / `io_rate_iops` / `cpu_rate_percent` 在普通用户下自动降级（不报错，降级状态记录于 capabilities 报告，见 §6.1）

---

## 9. 异常类型

```
SandboxError                 # 所有异常基类
├── SandboxTimeoutError      # 超时：wait、墙钟超时
├── ProtocolError            # 配置错误：未知字段、非法枚举、路径展开失败
└── SandboxProcessError      # 进程异常退出或启动失败、句柄无效
```

```python
from win_sandbox import SandboxError, SandboxTimeoutError, ProtocolError, SandboxProcessError

try:
    proc = sb.start_process(command_line="main.exe")
    exit_code, _, _ = proc.wait(timeout_ms=3000)
except SandboxTimeoutError as e:
    print(f"timeout: {e}")
```

Job 内查询错误（`query_process_exit_code` / `query_process_list`）通过异常 `code` 属性区分：`process_not_found` / `query_failed` / `invalid_payload` / `GlobalQuotaExceeded`。

---

## 10. 管道 helper 与工具函数

`win_sandbox` 模块级函数，用于操作句柄与辅助排空。

### 10.1 管道 I/O

```python
# 读取管道（阻塞，EOF 返回 b""）
data = win_sandbox.read_pipe(handle: int, size: int = 4096) -> bytes

# 写入管道（OVERLAPPED，超时抛 OSError；返回写入字节数）
written = win_sandbox.write_pipe(handle: int, data: bytes, timeout_ms: int = 30000) -> int

# 关闭句柄
win_sandbox.close_handle(handle: int)
# 仅用于关闭无主句柄（独立创建的管道/事件）；Process 暴露的 process_handle /
# stdin_handle / stdout_handle / stderr_handle 由库内部管理，禁止用本函数关闭
# （双重关闭可误关句柄值被复用后的无关对象），用 proc.close() / close_stdin() 代替

# 等待进程句柄（超时返回 False）
ok = win_sandbox.wait_process(handle: int, timeout_ms: int = -1) -> bool
```

### 10.2 排空 helper

```python
# 起后台线程排空 stdout，回调 callback(data: bytes) -> None
thread = win_sandbox.drain_stdout(proc: SandboxProcess, callback: Callable[[bytes], None]) -> threading.Thread

# 起后台线程排空 stderr
thread = win_sandbox.drain_stderr(proc: SandboxProcess, callback: Callable[[bytes], None]) -> threading.Thread
```

线程在 EOF 后自动结束，可 `thread.join()` 等待排空完成。

### 10.3 上下文管理器

```python
# 墙钟定时器（Python 端实现）
with win_sandbox.WallClockTimer(timeout_ms: int, on_timeout: Callable[[], None]) as timer:
    # 超时触发 on_timeout 回调（通常调用 proc.terminate(1)）
    ...

# 统计轮询（Python 端实现）
with win_sandbox.StatsPoller(proc: SandboxProcess, interval_ms: int, on_stats: Callable[[dict], None]) as poller:
    # 周期调用 proc.query_accounting() 并触发 on_stats 回调
    ...
```

### 10.4 工具函数

```python
# 检测文本是否含 AccessDenied 关键字（stderr 扫描用）
hit = win_sandbox.contains_access_denied_keyword(text: str) -> bool
```

### 10.5 完整示例（交互模式 + 排空 + 回调）

```python
import win_sandbox

sb = win_sandbox.SandboxInstance(log_level="info")
proc = sb.start_process(
    command_line="python -i",
    interactive=True,
    quota={"memory_mb": 256, "wall_clock_timeout_ms": 30000},
)

# 注册回调
proc.on_resource_limit = lambda info: print(f"limit: {info}")
proc.on_behavior_event = lambda info: print(f"behavior: {info}")

# 排空 stdout / stderr（后台线程）
win_sandbox.drain_stdout(proc, lambda d: print(d.decode("utf-8", "replace"), end=""))
win_sandbox.drain_stderr(proc, lambda d: print(d.decode("utf-8", "replace"), end="", file=__import__("sys").stderr))

# 写入 stdin
win_sandbox.write_pipe(proc.stdin_handle, b"print('hello')\n")
win_sandbox.write_pipe(proc.stdin_handle, b"exit()\n")
proc.close_stdin()

exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
print(f"\nexit: {exit_code}, reason: {exit_reason}")
print(f"peak memory: {proc.query_peak_memory()} bytes")

sb.shutdown()
```
