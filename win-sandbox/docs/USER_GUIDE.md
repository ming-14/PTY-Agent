# win-sandbox 用户手册

Windows 进程沙箱隔离系统用户指南。本文档面向最终用户，介绍安装、配置与使用。

---

## 1. 产品简介

win-sandbox 是一个 Windows 进程沙箱隔离系统，通过 **Job Object + Low IL token** 组合实现进程级隔离，Python 通过 pybind11 in-process 库（`win_sandbox_native.pyd`）直接调用 C++ 核心，无子进程、无 IPC。

核心能力：

| 能力 | 说明 |
|------|------|
| 资源限制 | CPU 速率/时间上限、内存上限、进程数上限、墙钟超时 |
| 进程隔离 | Low IL（完整性级别）隔离 token，全盘只读 + 可写区 |
| 文件系统隔离 | Low IL 完整性强制：全盘只读、写入落到 `%TEMP%` 重定向的可写区 |
| 网络隔离 | `unrestricted` / `allowlist`（SOCKS5 代理白名单） |
| 行为监控 | ETW 事件采集（管理员模式内核级；普通用户降级为进程列表轮询） |
| 权限自适应 | 启动时检测权限，自动降级不可用模块并报告 capabilities |

---

## 2. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 1809+ / Windows 11 |
| Python | 3.10+ |
| 权限 | 普通用户可运行（部分能力降级）；管理员可启用全部能力（权限模式能力对照见 [DEPLOYMENT.md §2](DEPLOYMENT.md)，降级语义见 §11） |

源码部署（克隆 / 构建 / 安装）所需构建工具见 [DEPLOYMENT.md §3](DEPLOYMENT.md)。

---

## 3. 安装

### 3.1 wheel 安装（推荐）

```powershell
pip install win-sandbox
```

安装后直接 `import win_sandbox` 即可使用，无需额外构建。

### 3.2 源码部署

源码部署（clone + 子模块 + 构建 + 安装）见 [DEPLOYMENT.md §3](DEPLOYMENT.md)。

### 3.3 验证安装

```powershell
python tests/e2e/smoke.py
```

运行 `python tests/e2e/smoke.py` 验证完整 round-trip。smoke.py 的场景为：创建沙箱 → 查询 capabilities → 关闭，**不启动子进程**。预期输出：

```
[1] create sandbox ...
    capabilities in <1s: {...}
[2] shutdown ...
    ok in <1s
[OK] smoke test passed
```

退出码 0 即通过（全程 < 2s）。

---

## 4. 快速开始

### 4.1 第一个沙箱进程

```python
import win_sandbox

sb = win_sandbox.SandboxInstance(log_level="info")
proc = sb.start_process(command_line="cmd.exe /c echo hello from sandbox!")

# 排空 stdout
def on_out(data):
    print(data.decode("utf-8", "replace"), end="")
win_sandbox.drain_stdout(proc, on_out).join()

exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
print(f"\n[exit code: {exit_code}, reason: {exit_reason}]")

sb.shutdown()
```

输出：

```
hello from sandbox!
[exit code: 0, reason: normal]
```

### 4.2 交互式 REPL

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()
proc = sb.start_process(
    command_line="python -i",
    interactive=True,
    quota={"memory_mb": 256, "cpu_ms": 30000},
)

# 持续写入 stdin
win_sandbox.write_pipe(proc.stdin_handle, b"print('hello')\n")
win_sandbox.write_pipe(proc.stdin_handle, b"import os; print(os.getcwd())\n")
win_sandbox.write_pipe(proc.stdin_handle, b"exit()\n")
proc.close_stdin()

def on_out(data):
    print(data.decode("utf-8", "replace"), end="")
win_sandbox.drain_stdout(proc, on_out).join()

proc.wait(timeout_ms=10000)
sb.shutdown()
```

### 4.3 资源配额示例

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()
proc = sb.start_process(
    command_line="python -c \"while True: pass\"",
    quota={
        "cpu_ms": 5000,             # 5 秒 CPU 时间上限
        "memory_mb": 128,           # 单进程内存上限
        "job_memory_mb": 256,       # Job 总内存上限
        "wall_clock_timeout_ms": 30000,  # 墙钟 30 秒
        "cpu_timeout_ms": 15000,    # CPU 超时 15 秒
        "max_processes": 8,         # 最多 8 个进程
        "no_ui": True,              # 禁止 UI 交互
    },
)

proc.on_resource_limit = lambda info: print(f"resource limit hit: {info}")
exit_code, exit_reason, usage = proc.wait(timeout_ms=40000)
print(f"exit: {exit_code}, reason: {exit_reason}")

sb.shutdown()
```

### 4.4 文件系统隔离（Low IL 全盘只读）

Low IL 隔离 token 使子进程对全盘只读（除可写区外无法写任何目录），写入默认落到 `%TEMP%`（已重定向到可写区）：

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()
proc = sb.start_process(
    command_line="cmd.exe /c echo result > output.txt",  # 写入落在可写区 %TEMP%
    isolation_policy={},
)
proc.wait(timeout_ms=10000)
sb.shutdown()
```

- 可写区路径：`%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable`
- `working_dir` 缺省时自动落到可写区（可读可写，状态隔离）；沙箱关闭时清理（残留由 StartupCleanup 兜底清理）
- 无需任何配置即全盘只读；`start_process` 未传 `working_dir` 时默认工作目录即可写区

### 4.5 使用配置文件

复杂场景（默认配额、隔离策略、行为监控、全局配额、Silo）通过 JSON 配置文件统一设置：

```python
import win_sandbox

# SandboxInstance 创建时读取 config.json，作为所有 start_process 的默认值
sb = win_sandbox.SandboxInstance(
    config=r"config.json",   # 见第 5 章配置参考
    log_level="info",
)
```

配置文件里的 `default_quota` 会在 `start_process` 未显式指定配额时兜底；显式指定的值优先生效。
默认隔离策略由 `isolation` 段（`net_policy` / `net_allowlist` / `clipboard_isolate`）配置，
`start_process` 的 `isolation_policy` 未显式指定时使用该默认值。

---

## 5. 配置

`SandboxInstance(config=path)` 支持通过 JSON 配置文件设置默认参数：

```python
sb = win_sandbox.SandboxInstance(config=r"config.json", log_level="debug")
```

### 5.1 完整配置参考

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
        "net_policy": "unrestricted",
        "net_allowlist": [
            {"ip": "10.0.0.1", "port": 443, "protocol": 6}
        ],
        "clipboard_isolate": false
    },
    "monitoring": {
        "etw_enabled": true,
        "ring_buffer_size": 10000,
        "dispatch_batch_size": 100,
        "dispatch_timeout_ms": 10,
        "stats_interval_ms": 5000,
        "filter_pids": [],
        "degraded_monitor_dirs": ["%LOCALAPPDATA%\\win-sandbox-workspace"],
        "degraded_net_polling": true,
        "force_degraded": false
    },
    "silo": {
        "enabled": false
    },
    "global_quota": {
        "enabled": false,
        "pool_name": "win-sandbox-quota",
        "max_cpu_rate_percent": 100,
        "max_memory_mb": 2048,
        "max_processes": 256
    }
}
```

### 5.2 配置段说明

| 段 | 字段 | 说明 |
|----|------|------|
| `logging.level` | | `trace\|debug\|info\|warn\|error` |
| `logging.dir` | | 日志目录（支持 `%VAR%` 环境变量展开） |
| `logging.retention_days` | | 日志保留天数 |
| `default_quota` | | 默认资源配额（见下表） |
| `isolation.net_policy` | | `unrestricted`（默认）\|`allowlist` |
| `isolation.net_allowlist` | | IP/port/protocol 白名单规则数组（仅 `allowlist` 生效） |
| `isolation.clipboard_isolate` | | 是否启用剪贴板隔离（Job UI 限制，默认 false） |
| `monitoring.etw_enabled` | | 是否启用 ETW 行为监控（默认 false） |
| `monitoring.ring_buffer_size` | | ETW 事件环形缓冲容量（默认 10000） |
| `monitoring.dispatch_batch_size` | | 批量回调每条 batch 的最大事件数（默认 100） |
| `monitoring.dispatch_timeout_ms` | | 批量回调最大累积等待（默认 10ms） |
| `monitoring.stats_interval_ms` | | ETW 统计上报间隔（默认 5000ms） |
| `monitoring.filter_pids` | | 仅采集这些 pid 的行为事件（非空时其余进程事件过滤，默认空 = 全部采集） |
| `monitoring.degraded_monitor_dirs` | | 降级模式文件监控目录数组（非管理员时 ReadDirectoryChangesW 递归监控） |
| `monitoring.degraded_net_polling` | | 降级模式网络轮询开关（默认 true） |
| `monitoring.force_degraded` | | 强制走降级路径（管理员环境验证降级能力用，默认 false） |
| `silo.enabled` | | 是否尝试启用 Server Silo 更强隔离（需平台支持，默认 false） |
| `global_quota` | | 多沙箱全局资源配额配置（见 5.6 节） |

### 5.3 `default_quota` 字段

默认配额字段全集（`cpu_ms` / `memory_mb` / `max_processes` / `wall_clock_timeout_ms` / `crash_silent` 等）见 [API_REFERENCE.md §7.1](API_REFERENCE.md)。

> `cpu_rate_percent` / `io_rate_*` 在普通用户下不可用，会自动降级并记录在 capabilities 报告。

### 5.4 `isolation` 段字段

默认隔离策略（`net_policy` / `net_allowlist` / `clipboard_isolate`），`start_process` 未显式传 `isolation_policy` 时使用。字段说明见 [API_REFERENCE.md §7.2](API_REFERENCE.md)。

> 文件系统隔离**无需配置**：Low IL token 天然全盘只读，写入落到重定向的 `%TEMP%`（可写区）。

### 5.5 环境变量展开

所有路径字段支持 `%VAR%` 展开，如 `%TEMP%`、`%LOCALAPPDATA%`、`%SystemRoot%`。`start_process` 的 `working_dir` / `env_vars` 中的路径**不展开**环境变量，调用方应传完整绝对路径。

### 5.6 `global_quota` 段字段

多沙箱全局资源配额：多个 `SandboxInstance` 实例共享一个配额池，合计占用 CPU/内存/进程数不超过上限。跨进程通过命名共享内存实现。字段说明见 [API_REFERENCE.md §8](API_REFERENCE.md)。

> 超限时 `start_process` 抛异常（`GlobalQuotaExceeded`），进程不会启动。

---

## 6. 隔离能力详解

### 6.1 资源限制（Job Object）

所有资源限制通过 Windows Job Object 实现：

- **CPU 时间**：`cpu_ms` 达到后触发 `on_resource_limit` 回调，沙箱终止进程
- **内存**：`memory_mb` / `job_memory_mb` 超限同样触发回调
- **进程数**：`max_processes` 限制 Job 内同时运行进程数
- **超时**：`wall_clock_timeout_ms` 为墙钟硬超时（沙箱内建：`start_process` 时自动挂墙钟定时器，到期调用 `Terminate`，`exit_reason=wall_clock_timeout`）；`cpu_timeout_ms` 为 CPU 累积超时

### 6.2 文件系统隔离（Low IL）

文件系统隔离由 **Low IL 完整性级别**强制（纯用户态，无需管理员）：

| 语义 | 行为 |
|------|------|
| 全盘只读 | Low IL token 的 NO_WRITE_UP：子进程对任何目录（含宿主 `%TEMP%`、`C:\Windows` 等）只读，写/改/删一律拒绝 |
| 全盘可读可执行 | 完整性级别不限制读/执行（Low 进程可读任意常规文件，执行用户可读的可执行文件）。**例外**：宿主目录带显式受限 DACL（受控文件夹/安全工具注入的 AppContainer ACE）时，读/执行同样被拒（DACL 优先于 IL 检查）——需沙箱内访问的文件应放普通 ACL 目录（如 `%LOCALAPPDATA%` 下） |
| 可写区 | 唯一可写目录：`%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable`（创建时打 Low 标签 + 用户完全控制 DACL） |
| `%TEMP%` / `%TMP%` | 启动时重定向到可写区（子进程看到的 `%TEMP%` 即可写区） |
| 工作目录 | `working_dir` 未传时默认落到可写区 |

可写区生命周期：
- 沙箱关闭时 `WriteArea::Teardown` 删除整个会话目录（含空目录残留）
- 沙箱异常退出（崩溃/强杀）残留的会话目录由下次初始化时的 `StartupCleanup` 兜底清理

### 6.3 网络隔离

| 策略 | 说明 |
|------|------|
| `unrestricted`（默认） | 不做网络限制（子进程以用户 token 天然网络权限运行） |
| `allowlist` | 仅白名单 IP 可访问。沙箱在 127.0.0.1 起 **SOCKS5 代理**，按 `net_allowlist` 判定放行/拒绝，并把代理地址注入子进程环境变量（`ALL_PROXY`/`HTTP_PROXY`/`HTTPS_PROXY`） |

> **allowlist 实现要点与边界**：
> - 代理仅拦截**走代理的应用**（curl、requests 等识别代理环境变量的程序）；**直接 socket 连接不受代理管控**，可绕过白名单直连外网——敏感场景不要单独依赖 allowlist 作为硬边界。
> - `allowlist` 依赖 WFP callout（需管理员）：非管理员下 Open 失败，记 Warn 降级（`allowlist` 不生效，进程网络不受限，语义等同 `unrestricted`，见 [API_REFERENCE.md §6.1](API_REFERENCE.md) 的 `network` 模块）。
> - 代理仅实现 SOCKS5 CONNECT 命令（不支持 BIND / UDP ASSOCIATE）；**IPv6 目标地址（ATYP=0x04）被拒绝**。
> - 每个连接独立工作线程（并发放行上限 16）；上游连接 10s 超时（上游不可达时连接快速失败，不挂死线程）。
> - 代理监听端口在启动后立即确定（bind+listen 于初始化阶段完成），无"端口已公示但未监听"的竞争窗口。
> - 拦截事件通过日志（`network blocked (native): ip=... port=... proto=...`）上报。

### 6.4 行为监控（ETW）

- **管理员模式**：真实 ETW 内核 session，采集进程启动/退出、文件 IO、注册表、网络行为事件
- **普通用户模式（降级模式）**：
  - 进程事件：进程列表轮询（ProcessStart/Stop）
  - 文件事件：`ReadDirectoryChangesW` 递归监控 `monitoring.degraded_monitor_dirs` 配置的目录（FileCreate/FileWrite/FileDelete）
  - 网络事件：`GetExtendedTcpTable`/`GetUdpTable` 轮询连接表（TcpConnect/UdpSend）
  - 注册表事件不可用（无全局非管理员 API）
  - 首次轮询只建基线，不把全系统进程当新进程（无噪音）
- 降级模式的事件类型仍带 `seq` 序号，支持丢包检测

行为事件通过 `proc.on_behavior_event` 回调批量上报，每条 batch 为 dict 列表。

### 6.5 Server Silo 更强隔离（可选）

Server Silo 是 Windows 的进程隔离容器（Silo Job），提供**视图级隔离**：独立对象命名空间、注册表 hivestack、文件系统挂载重定向、网络 compartment。与现有 Job（资源限制）+ Low IL 隔离（完整性级别）正交叠加。

配置方式：

```json
{ "silo": { "enabled": true } }
```

启用后沙箱启动时探测平台支持：

| 平台 | 行为 |
|------|------|
| Win Server / Win11 预览 | 把 Job 就地升级为 Server Silo，获得更强视图级隔离 |
| Win10 客户端（含 22H2） | 探测失败，自动降级到 Job+Low IL，功能不受影响 |

> Silo 用户态 API 未文档化，仅在支持的平台启用；本机 Win10 客户端会记录 `Silo: platform does not support Server Silo` 日志并降级。

### 6.6 多沙箱全局资源配额（可选）

当需要限制**所有沙箱实例合计**占用的资源时启用全局配额。多个 `SandboxInstance` 配置相同 `pool_name` 即共享同一配额池：

```json
{
  "global_quota": {
    "enabled": true,
    "pool_name": "my-pool",
    "max_cpu_rate_percent": 100,
    "max_memory_mb": 2048,
    "max_processes": 128
  }
}
```

- 每次 `start_process` 前检查全局余量，超限拒绝并抛异常
- 进程退出/沙箱关闭时自动释放占用
- 用于 CI 多实例并行、OJ 评测集群等需要全局资源上限的场景

---

## 7. 权限模式与 capabilities 报告

启动时沙箱自动检测权限，`sb.capabilities` 属性返回 capabilities 报告（结构与字段说明见 [API_REFERENCE.md §6.1](API_REFERENCE.md)）。Python 端据此判断实际可用能力，决定测试/功能降级策略。

---

## 8. 使用场景

### 8.1 OJ 评测（test_oj_scenario.py）

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()
proc = sb.start_process(
    command_line="main.exe",
    working_dir=r"C:\oj\submission",
    quota={
        "cpu_ms": 2000,
        "memory_mb": 128,
        "wall_clock_timeout_ms": 5000,
        "max_processes": 1,
        "no_ui": True,
    },
    isolation_policy={
        "clipboard_isolate": True,
    },
)
exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
sb.shutdown()
```

### 8.2 样本分析（test_scenario_c_sample.py）

样本在隔离环境运行（Low IL 全盘只读，写入落可写区），ETW 监控行为：

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()
proc = sb.start_process(
    command_line="sample.exe",
    isolation_policy={
        "clipboard_isolate": True,
    },
    quota={"wall_clock_timeout_ms": 30000},
)

proc.on_behavior_event = lambda events: print(f"behavior: {events}")
exit_code, exit_reason, usage = proc.wait(timeout_ms=40000)
sb.shutdown()
```

### 8.3 CI 多实例并行（test_scenario_d_ci.py）

每个测试用例创建独立 `SandboxInstance`，互不干扰：

```python
import win_sandbox

sb1 = win_sandbox.SandboxInstance()
sb2 = win_sandbox.SandboxInstance()
# 两个沙箱独立运行，互不影响
```

### 8.4 多实例共享全局配额（test_global_quota.py）

CI 并行跑多个沙箱时，用全局配额限制合计资源占用。所有实例配置相同的 `pool_name`：

```python
import win_sandbox

# config.json（所有实例共用）
# {
#   "global_quota": {
#     "enabled": true,
#     "pool_name": "ci-pool",
#     "max_cpu_rate_percent": 100,
#     "max_memory_mb": 2048,
#     "max_processes": 64
#   }
# }

# 每个测试用例（独立 SandboxInstance，共享同一配额池）
sb = win_sandbox.SandboxInstance(config="config.json")
proc = sb.start_process(command_line="main.exe", quota={"memory_mb": 256})
```

当池中配额耗尽时，后续 `start_process` 抛异常（`GlobalQuotaExceeded`），进程不启动。

### 8.5 进程列表查询与崩溃静默

Job 功能增强：查询进程的 Job 内进程列表、识别崩溃进程、禁用崩溃弹窗。

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()

# 1. 启动进程（开启崩溃静默：无头场景崩溃不弹对话框/不触发 WER 挂起）
proc = sb.start_process(
    command_line="python.exe script.py",
    quota={"crash_silent": True, "wall_clock_timeout_ms": 30000},
)
#    process_started 携带主进程路径
print(f"可执行文件: {proc.process_path}")

# 2. 查询 Job 内进程列表（含子进程，未 escape 的）
#    → list[int]（OS PID 列表）
pids = proc.query_process_list()
print(f"Job 内进程: {pids}")

# 3. 崩溃检测：exit_code 为非零 NTSTATUS（如 0xC0000005 = -1073741819），
#    且 exit_reason 对应异常退出（0 退出码 → normal，非零（含崩溃 NTSTATUS）→ abnormal）
exit_code, exit_reason, usage = proc.wait(timeout_ms=40000)
if exit_code != 0:
    print(f"进程异常退出: code={exit_code}, reason={exit_reason}")

sb.shutdown()
```

要点：
- `query_process_list()` 返回 OS PID 列表（含进程自身及 Job 内子进程，未 breakaway 逃逸）
- 进程退出后 Job 内 pid 清理有短暂延迟，查询实时性要求高时建议轮询
- 崩溃退出码是 int32（NTSTATUS），Python 端为负数；与 `0xC0000005` 比较请用 `code & 0xFFFFFFFF`

### 8.6 进程树管理：生命周期事件与退出码查询

补全 Job 进程树管理的 Python 面：**子/孙进程生命周期实时事件**（`on_job_process_started` / `on_job_process_exited` 回调）与**任意 PID 退出码查询**（`query_process_exit_code`）。

```python
import win_sandbox

sb = win_sandbox.SandboxInstance()

# 1. 启动 cmd 长跑 + ping 子进程
proc = sb.start_process(command_line='cmd.exe /c "ping -n 6 127.0.0.1 >nul"')

# 2. 实时收到子进程创建事件（回调，无需请求）
#    on_job_process_started(info): info = {"process_id": 1, "pid": 5678,
#                                          "process_name": "ping.exe",
#                                          "process_path": "...",
#                                          "parent_pid": <主pid>}
#    注意：控制台场景会先出现 conhost.exe 事件（Windows 创建，父进程为主进程，合法）
child_pids = []
proc.on_job_process_started = lambda info: child_pids.append(info["pid"])

# 3. 子进程退出实时事件（回调）
proc.on_job_process_exited = lambda info: print(f"子进程退出: {info}")

# 4. 查询 Job 内任意 PID 退出码（运行中 → is_active=True, exit_code=259）
import time
time.sleep(1)  # 等子进程出现
if child_pids:
    exit_code, is_active = proc.query_process_exit_code(child_pids[0])
    if is_active:
        print(f"子进程仍在运行 (STILL_ACTIVE=259)")

exit_code, exit_reason, usage = proc.wait(timeout_ms=15000)
sb.shutdown()
```

要点：
- **主进程不产生 `on_job_process_*` 回调**：主进程只走 `wait()` 返回；Job 内子/孙进程才产生 `on_job_process_*` 回调，无重复
- `exit_kind` 语义：`normal`（退出码 0）/ `abnormal`（非 0 含崩溃）/ `unknown`（兜底：退出码查询失败，此时**无 `exit_code` 字段**，用 `info.get("exit_code")` 访问）
- 崩溃路径（ABNORMAL_EXIT + EXIT 双通知）同一 pid 仅一条 `on_job_process_exited`（服务端已去重）
- `is_active` 语义：`exit_code == 259`（STILL_ACTIVE）时 `true`；进程恰好以 259 退出时会误判为运行中（Win32 约定）
- **已退出进程的查询窗口**：进程对象在退出后仅存活短暂时间（约 100ms），之后查询抛异常（`query_failed`）；需要已退出进程的退出码时，请在收到退出回调后立即查询
- **Job 归属校验**：`pid` 必须是该 `proc` 对应 Job 内的进程（含已退出进程），跨实例/无关 pid 的查询抛异常（`process_not_found`），不会泄露其他沙箱实例的进程状态
- 错误码：`process_id` 无效或 pid 不属于本 Job → `process_not_found`；pid 属于本 Job 但进程对象已回收 → `query_failed`；缺字段或类型错误（pid 须为整数，float 会被拒绝）→ `invalid_payload`

### 8.7 启用 Server Silo 更强隔离（test_silo.py）

样本分析等需要更强隔离的场景，可在配置中启用 Silo（需平台支持）：

```json
{ "silo": { "enabled": true } }
```

Win Server / Win11 预览上 Job 会被升级为 Server Silo（独立对象命名空间等视图级隔离）；Win10 客户端自动降级，行为与普通 Job+Low IL 一致，不影响业务代码。

### 8.8 普通用户行为监控（降级模式文件+网络事件）

非管理员模式下，通过 `monitoring.degraded_monitor_dirs` 指定要监控的目录，即可在降级模式下获得文件创建/写入/删除事件与 TCP/UDP 网络事件：

```json
{
  "monitoring": {
    "etw_enabled": true,
    "degraded_monitor_dirs": ["%LOCALAPPDATA%\\win-sandbox-workspace"],
    "degraded_net_polling": true
  }
}
```

- 文件事件：目录（含子目录）内文件被创建/修改/删除时触发 `on_behavior_event` 回调（type 5/6/7）
- 网络事件：新建立的 TCP 连接/新 UDP 端点触发回调（type 11/12）
- 首次轮询只建基线，不会把全系统进程当新进程上报
- 注册表事件在非管理员模式下不可用

---

## 9. 多沙箱实例

pybind11 in-process 形态下，一个 Python 进程可创建多个 `SandboxInstance`，各自独立管理进程与资源：

```python
import win_sandbox

sb1 = win_sandbox.SandboxInstance()
sb2 = win_sandbox.SandboxInstance()

proc1 = sb1.start_process(command_line="cmd.exe /c echo sb1")
proc2 = sb2.start_process(command_line="cmd.exe /c echo sb2")

# 各自独立等待
proc1.wait(timeout_ms=5000)
proc2.wait(timeout_ms=5000)

sb1.shutdown()
sb2.shutdown()
```

配合 `global_quota` 可限制所有实例合计资源占用（见 §5.6）。

---

## 10. 日志

Python 端日志：`logging.getLogger("win_sandbox")`，可配置级别。

C++ 端日志（spdlog）：
- 配置文件 `logging.dir` 指定持久化日志目录（默认 `%LOCALAPPDATA%\win-sandbox\logs`；`%LOCALAPPDATA%` 缺失时回退 `%TEMP%\win-sandbox-logs`）
- `log_level=debug` 时额外写 stderr
- 过期日志按 `retention_days` 自动清理（含历史 `%TEMP%\win-sandbox-<pid>` 目录）

日志级别：`trace < debug < info < warn < error`。

---

## 11. 已知限制

1. **Low IL 单向墙**：完整性级别阻止"低写高"（子进程不能写宿主），但"高读低"不受限——子进程可读宿主常规文件（敏感文件请用 ACL 另行限制）；完整性级别不阻止通过继承句柄访问宿主资源，进程创建时已最小化句柄继承
2. **宿主显式 ACL 目录不可达**：带显式受限 DACL 的宿主目录（受控文件夹、安全工具注入 AppContainer ACE 的目录、系统 Temp 等）对 Low IL 沙箱**读/执行也被拒绝**（DACL 检查先于 IL 检查）；`cmd /c` 中转启动此类目录下的程序会报"参数格式不正确/系统找不到文件"——沙箱内要运行的程序请放普通 ACL 目录（如 `%LOCALAPPDATA%` 下）
3. **可写区集中在 `%TEMP%` 重定向**：子进程写任意路径失败（全盘只读），需持久化时从可写区（`%LOCALAPPDATA%\win-sandbox\sessions\...\writable`）取回
4. **ETW 内核 session 需管理员**：普通用户降级为进程轮询 + 目录文件监控 + 网络轮询（无注册表事件）
5. **allowlist 不是硬网络边界**：SOCKS5 代理仅拦截走代理的应用；直接 socket 连接可绕过；非管理员下 `allowlist` 降级为 `unrestricted`（WFP 不可用）；代理不支持 IPv6 目标与 UDP ASSOCIATE
6. **CtrlC 信号不支持**：Windows 上无法定向投递，用 `ctrl_break` 替代
7. **Server Silo 需平台支持**：Win10 客户端不支持用户态创建 Server Silo（`silo.enabled` 时自动降级）
8. **全局配额仅软性拒绝**：超限时新进程被拒绝，已运行进程不受影响（不做 OOM/强杀）
9. **stdin 写入等待上限 30s**：子进程不读 stdin 时 `write_pipe` 超时抛异常（OVERLAPPED 写 + CancelIoEx 取消），不会无限挂起调用线程
10. **`cmd /c "绝对路径\a.exe"` 引号陷阱**：cmd 引号剥除规则会把"路径+参数"整串当文件名（"系统找不到文件"）；请用 `cmd /c ""C:\path\a.exe" arg"` 双层引号或省略引号（路径无空格时）
