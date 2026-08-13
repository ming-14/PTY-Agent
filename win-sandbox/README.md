# win-sandbox

Windows 进程沙箱隔离系统 — 通过 Job Object + Low IL token 组合实现进程级隔离，Python 通过 pybind11 in-process 库（`win_sandbox_native.pyd`）直接调用 C++ 核心，无子进程、无 IPC。

> **状态**：版本 0.2.0，pybind11 in-process 形态。21 套件 e2e 测试全量通过（run_all_regression 21/21）+ ctest 6 项单测通过。

---

## 目录

- [功能特性](#功能特性)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [构建指南](#构建指南)
- [Python API](#python-api)
- [配置文件](#配置文件)
- [事件回调与管道 helper](#事件回调与管道-helper)
- [测试](#测试)
- [项目结构](#项目结构)
- [文档](#文档)
- [平台限制](#平台限制)
- [已知问题](#已知问题)
- [License](#license)

---

## 功能特性

### 进程隔离

| 能力 | 实现方式 | 说明 |
|------|----------|------|
| **资源限制** | Job Object | CPU 速率限制、内存上限、进程数上限、墙钟超时、CPU 超时 |
| **进程隔离** | Low IL token | 子进程以 Low 完整性级别运行（纯用户态，非管理员可用）：对宿主磁盘**全盘只读**（可写区除外）、%TEMP% 重定向到会话可写区 |
| **文件系统隔离** | Low IL + 可写区 | 全盘只读 + `%LOCALAPPDATA%\win-sandbox\sessions\<pid>-<process_id>\writable` 可写区（TEMP/TMP 重定向） |
| **网络隔离** | net_policy | `unrestricted`（不限制）/ `allowlist`（WFP 连接过滤 + SOCKS5 代理注入，需管理员，非管理员自动降级） |
| **剪贴板隔离** | Job UI 限制 | `clipboard_isolate=true` 时隔离剪贴板/全局原子表/系统参数 |
| **行为监控** | ETW（条件编译） | 管理员模式：真 ETW 内核 session；非管理员：降级轮询模式 |
| **Job 功能增强** | Job Object | 进程列表查询（`query_process_list`）、退出码精确读取（正常/异常/崩溃分类）、崩溃静默（`crash_silent`，崩溃不弹窗不触发 WER） |
| **进程树扩展** | Job Object | Job 内子/孙进程生命周期实时事件（`on_job_process_started` / `on_job_process_exited` 回调）、任意 PID 退出码查询（`query_process_exit_code`） |

> **⚠️ 默认姿态警告**：Low IL 隔离是**默认生效**的（子进程全盘只读 + 可写区，
> 无需任何配置）；`isolation_policy` 仅控制**网络策略与剪贴板隔离**——省略时
> 网络不受限、剪贴板不隔离。不要把"没传 isolation_policy"当作"完全不受保护"
> 或"受保护"：文件系统层面 Low IL 始终生效，网络/剪贴板层面按显式配置。
| **权限自适应** | PermissionDetector | 启动时检测权限，自动降级不可用模块并报告 capabilities |
| **更强隔离（可选）** | Server Silo | `silo.enabled` 启用；Win Server/Win11 预览将 Job 升级为 Silo（视图级隔离），Win10 客户端自动降级 |
| **全局配额（可选）** | 跨进程共享内存池 | `global_quota` 启用；多沙箱实例共享 CPU/内存/进程数上限，超限拒绝 |

### Python 直调 API（pybind11 in-process）

- `import win_sandbox` 直接加载 `win_sandbox_native.pyd`（pybind11 编译的 C++ 核心），无子进程、无 IPC、无序列化
- `SandboxInstance` / `SandboxProcess` 为 C++ 对象的 pybind11 绑定，方法直调
- stdin/stdout/stderr 管道句柄以 `int` 暴露给 Python，Python 用 `win_sandbox.read_pipe` / `write_pipe` / `close_handle` 自行 `ReadFile`/`WriteFile`
- Job IOCP / ETW 事件通过 pybind11 回调推 Python（`proc.on_resource_limit` / `on_job_process_started` / `on_behavior_event` 等 setter）
- 墙钟定时器（`WallClockTimer`）与统计轮询（`StatsPoller`）在 Python 端实现；`wall_clock_timeout_ms` 配额由沙箱内建定时器兑现
- 零子进程开销、零序列化开销、零第三方依赖

### 交互能力

- `start_process` — 启动沙箱进程（支持资源配额、隔离策略、交互模式、`request_id` 关联、外部传入 ConPTY `hpcon`）
- `write_pipe(proc.stdin_handle, data)` — 持续写入子进程 stdin（交互模式）
- `proc.signal(sig)` — `ctrl_break` / `kill` 信号
- `proc.terminate(exit_code)` — 主动终止指定进程
- `proc.query_accounting()` — 查询即时资源使用
- `sb.cleanup_finished()` — 清理已退出进程条目（`start_process` 入口自动执行）
- `sb.shutdown()` — 优雅关闭沙箱
- 事件回调：`on_resource_limit` / `on_job_process_started` / `on_job_process_exited` / `on_behavior_event` / `on_access_denied`

---

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    Python 进程                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ win_sandbox (Python helpers)                        │ │
│  │ read_pipe / write_pipe / drain_stdout / drain_stderr│ │
│  │ WallClockTimer / StatsPoller                        │ │
│  │ contains_access_denied_keyword / close_handle       │ │
│  └──────────────────┬──────────────────────────────────┘ │
│                     │ pybind11 直调 (in-process)          │
│  ┌──────────────────┴──────────────────────────────────┐ │
│  │         win_sandbox_native.pyd (C++ 核心)            │ │
│  │  ┌─────────────────────────────────────────────────┐ │ │
│  │  │ SandboxInstance / SandboxProcess (pybind11 绑定) │ │ │
│  │  ├─────────────────────────────────────────────────┤ │ │
│  │  │ Use Cases (NativeSandboxedProcess)      │ │
│  │  ├──────────────┬──────────────┬───────────────────┤ │
│  │  │ JobObject    │ TokenIsolator│ WriteArea         │ │
│  │  │ (资源限制)    │ (Low IL 隔离) │ (会话可写区)       │ │
│  │  ├──────────────┼──────────────┼───────────────────┤ │
│  │  │ EtwMonitor   │ WfpEngine    │ PermissionDetector│ │
│  │  │ (行为事件)    │ (allowlist)  │ (权限自适应)       │ │
│  │  └──────────────┴──────────────┴───────────────────┘ │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                   │
                   │ 子进程 (沙箱内)
                   ▼
            ┌──────────────┐
            │  用户程序     │
            │ (Low IL      │
            │  + Job Object)│
            └──────────────┘
```

**分层架构（洋葱模型）**：

- **Core 层**：实体（`SandboxConfig`、`ResourceQuota`、`IsolationPolicy`）+ 端口接口（`IJobObject`、`ITokenIsolator`、`IWriteArea`、`IWfpEngine` 等）
- **Use Case 层**：`NativeSandboxedProcess`
- **Adapter 层**：`ConfigLoader`、`NativeSandboxInstance`、`StartProcessPayloadParser`、`PermissionDetector`
- **Infra 层**：`JobObjectImpl`、`TokenIsolatorImpl`、`WriteAreaImpl`、`EtwMonitorImpl`、`WfpEngineImpl`、`ProcessLauncherImpl`
- **Python 绑定层**：pybind11 模块 `win_sandbox_native`，暴露 `SandboxInstance` / `Process` / 管道 helper / 工具函数

---

## 快速开始

### 前置要求

- **Windows 10 1809+**（ConPTY 支持需要；Low IL 隔离无版本特例）
- **Visual Studio 2022** 或 **VS 2026 Preview**（含 C++ 桌面开发工作负载）
- **CMake 3.20+**
- **Ninja**（推荐）或 MSBuild
- **Python 3.10+**

### 1. 安装

```powershell
pip install win-sandbox
```

或从源码：

```powershell
git clone https://github.com/ming-14/win-sandbox.git
cd win-sandbox
git submodule init
git submodule update

# 构建 C++ + 打包 wheel（构建脚本自动定位 MSVC 环境并执行 CMake 构建）
.\BUILD.ps1
pip install .
```

### 2. 第一个沙箱进程

```python
import win_sandbox

sb = win_sandbox.SandboxInstance(log_level="info")
proc = sb.start_process(command_line="cmd.exe /c echo Hello from sandbox!")

# 排空 stdout
def on_out(data):
    print(data.decode("utf-8", "replace"), end="")
win_sandbox.drain_stdout(proc, on_out).join()

exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
print(f"\n[exit code: {exit_code}, reason: {exit_reason}]")
print(f"peak memory: {usage.get('peak_memory_bytes', 0)} bytes")

sb.shutdown()
```

输出：

```
Hello from sandbox!
[exit code: 0, reason: normal]
peak memory: ... bytes
```

### 3. 交互模式示例

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

---

## 构建指南

### 构建

构建统一走仓库根目录的 `BUILD.ps1`（自动定位 MSVC 环境并执行 CMake+Ninja；需 PowerShell 7+，Windows PowerShell 5.1 无法解析 UTF-8 无 BOM 脚本）：

```powershell
.\BUILD.ps1              # Release（默认）
.\BUILD.ps1 -Config Debug
.\BUILD.ps1 -Rebuild     # 清理 build 目录后全新构建
```

产物：`build/bin/win_sandbox_native.cp311-win_amd64.pyd`（pybind11 扩展模块，文件名含 Python ABI tag）。

### 第三方依赖

通过 Git submodule 管理：

| 库 | 版本 | 用途 |
|----|------|------|
| [WIL](https://github.com/microsoft/wil) | latest | Windows Implementation Libraries（RAII wrapper） |
| [nlohmann/json](https://github.com/nlohmann/json) | latest | JSON 解析（配置文件） |
| [spdlog](https://github.com/gabime/spdlog) | latest | 日志库（静态链接，内置 fmt） |
| [pybind11](https://github.com/pybind/pybind11) | latest | Python ↔ C++ 绑定 |

### 构建验证

```powershell
# 编译 + 运行全量 e2e 回归（排除需管理员的 test_etw_admin.py）
.\BUILD.ps1
python tests/e2e/run_all_regression.py    # 需 PYTHONPATH=python（win_sandbox 包目录）
```

---

## Python API

完整 API 参考见 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)。

```python
import win_sandbox

sb = win_sandbox.SandboxInstance(log_level="info")
proc = sb.start_process(
    command_line="cmd.exe /c echo Hello from sandbox!",
    quota={"memory_mb": 256},
    isolation_policy={"net_policy": "unrestricted", "clipboard_isolate": False},
)

# 事件回调
proc.on_resource_limit = lambda info: print(f"limit hit: {info}")
proc.on_behavior_event = lambda info: print(f"behavior: {info}")

exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
print(f"exit: {exit_code}, reason: {exit_reason}, usage: {usage}")

sb.shutdown()
```

要点：
- `SandboxInstance` 为 C++ 对象的 pybind11 绑定，方法直调，无 IPC
- `quota` 支持：`cpu_ms` / `cpu_rate_percent` / `memory_mb` / `job_memory_mb` / `max_processes` / `wall_clock_timeout_ms` / `cpu_timeout_ms` / `no_ui` / `breakaway_ok` / `crash_silent`
- `isolation_policy` 支持：`net_policy`（`unrestricted` / `allowlist`）/ `net_allowlist`（白名单规则 `{ip, port, protocol}`）/ `clipboard_isolate`（严格模式：未知键传了会显式报错）
- 管道 helper：`win_sandbox.read_pipe(handle, size=4096)` / `win_sandbox.write_pipe(handle, data)` / `win_sandbox.close_handle(handle)`
- 工具函数：`win_sandbox.contains_access_denied_keyword(text)` / `win_sandbox.drain_stdout(proc, cb)` / `win_sandbox.drain_stderr(proc, cb)`
- 上下文管理器：`win_sandbox.WallClockTimer(timeout_ms, on_timeout)` / `win_sandbox.StatsPoller(proc, interval_ms, on_stats)`

---

## 配置文件

`SandboxInstance(config=path)` 支持通过 JSON 配置文件设置默认参数（详细 schema 与完整示例见 [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) §5 与 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) §8）：

```python
sb = win_sandbox.SandboxInstance(config=r"config.json", log_level="debug")
```

```json
{
    "logging": { "level": "debug", "dir": "%LOCALAPPDATA%\\win-sandbox\\logs", "retention_days": 7 },
    "default_quota": { "memory_mb": 512, "cpu_ms": 30000, "max_processes": 1, "wall_clock_timeout_ms": 60000 },
    "isolation": { "net_policy": "unrestricted", "net_allowlist": [], "clipboard_isolate": false }
}
```

要点：
- 配置段：`logging` / `default_quota` / `isolation` / `monitoring` / `silo` / `global_quota`
- 路径字段支持环境变量展开（`%TEMP%`、`%LOCALAPPDATA%`、`%SystemRoot%` 等）
- 配置严格模式：未知段/未知字段会显式报错拒绝（不静默忽略）
- `net_policy=allowlist` 依赖 WFP connect filter（需管理员）：非管理员下 Open 失败记 Warn 降级（语义等同 `unrestricted`，见 `capabilities` 报告）

---

## 事件回调与管道 helper

pybind11 in-process 形态下，事件通过 setter 回调推 Python，管道由 Python 自行读写。完整定义见 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) §5-§6。

- **进程回调**：`proc.on_resource_limit` / `proc.on_job_process_started` / `proc.on_job_process_exited` / `proc.on_behavior_event` / `proc.on_access_denied`（均为 `lambda info: ...`，`info` 为 dict）
- **管道 helper**：`win_sandbox.read_pipe(handle, size=4096) -> bytes` / `win_sandbox.write_pipe(handle, data) -> int` / `win_sandbox.close_handle(handle)` / `win_sandbox.wait_process(handle, timeout_ms) -> bool`
- **排空 helper**：`win_sandbox.drain_stdout(proc, callback) -> threading.Thread` / `win_sandbox.drain_stderr(proc, callback) -> threading.Thread`（回调签名 `callback(data: bytes) -> None`）
- **上下文管理器**：`win_sandbox.WallClockTimer(timeout_ms, on_timeout)` / `win_sandbox.StatsPoller(proc, interval_ms, on_stats)`
- **工具函数**：`win_sandbox.contains_access_denied_keyword(text) -> bool`

---

## 测试

### e2e 测试套件

```powershell
# 运行全部 e2e 测试（排除需管理员的 test_etw_admin.py）
python tests/e2e/run_all_regression.py

# 运行单个测试套件
python tests/e2e/test_lowil_isolation.py
python tests/e2e/test_resource_quota.py
python tests/e2e/test_network_allowlist.py
```

| 测试套件 | 用例数 | 说明 |
|----------|--------|------|
| `smoke.py` | — | 基础冒烟测试 |
| `test_lowil_isolation.py` | 6 | Low IL 隔离语义（IL 断言/全盘只读/可写区/网络/清理/剪贴板）|
| `test_write_stdin.py` | 6 | stdin 交互写入 |
| `test_signal.py` | 5 | 信号传递（CtrlBreak / Kill）|
| `test_multiprocess.py` | 6 | 多进程并行托管 |
| `test_hpcon_conpty.py` | — | ConPTY 终端语义（外部传入 hpcon）|
| `test_oj_scenario.py` | 4 | OJ 场景模拟 |
| `test_resource_quota.py` | 6 | 资源配额（内存/CPU/进程数限制）|
| `test_network_allowlist.py` | 4 | 网络 allowlist（WFP + SOCKS5 代理）|
| `test_behavior_log.py` | 5 | 行为事件日志（管理员 4 PASS；普通用户降级）|
| `test_degraded_monitor.py` | 6 | 降级监控模式（进程/文件/网络轮询）|
| `test_cleanup.py` | 2 | 会话目录清理验证（Low IL 可写区 Teardown）|
| `test_permission_matrix.py` | 2 | 权限矩阵（Admin / StandardUser）|
| `test_scenario_c_sample.py` | 1 | 样本分析场景 |
| `test_scenario_d_ci.py` | 1 | CI 多实例并行 |
| `test_global_quota.py` | 5 | 全局配额跨实例共享 |
| `test_silo.py` | 4 | Server Silo 更强隔离（可选）|
| `test_job_enhancement.py` | 6 | Job 功能增强 |
| `test_process_tree.py` | 11 | 进程树扩展（job_process_* 回调 + 退出码查询 + 类型校验 + 跨实例隔离）|
| `test_native_smoke.py` | — | pybind11 直调冒烟（SandboxInstance/Process/capabilities）|
| `test_native_etw.py` | — | pybind11 形态 ETW 直调 |
| `test_etw_admin.py` | 8 | 管理员模式真 ETW（需管理员，默认排除）|

**总计：21 套件全量通过（run_all_regression.py 21/21，排除 test_etw_admin.py）**；ctest 6 项通过。

### 单元测试

```powershell
# C++ 单元验证程序（ctest 已注册，可直接跑）
ctest --test-dir build -C Debug

# Python helper 单元测试
python tests/unit/test_helpers.py
```

ctest 注册 6 项程序（probe_t16 / verify_t11 / verify_t14 / verify_t17 / verify_t27 / verify_t28），覆盖
NativeSandboxedProcess、ConfigLoader（isolation schema 拒绝）、TokenIsolator、WriteArea、
isolation_policy 解析、Job 功能增强。

---

## 项目结构

完整目录树见 [docs/FILESTREE.md](docs/FILESTREE.md)。

```
win-sandbox/
├── src/          # C++ 源码（clean architecture：core → usecases → adapters → infra）
├── python/       # Python 包（win_sandbox/：helpers + pybind11 绑定）
├── tests/        # e2e Python 套件 + C++ 单元验证（verify_t*.cpp / ctest）
├── docs/         # 架构文档、User/API 参考、经验教训
├── third_party/  # Git 子模块（WIL / nlohmann_json / spdlog / pybind11）
└── build/        # 构建产物（git 忽略）
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 架构与技术原理（现行权威设计文档）|
| [Lessons-Learned](docs/Lessons-Learned.md) | 踩坑记录 |
| [USER_GUIDE](docs/USER_GUIDE.md) | 用户手册 |
| [API_REFERENCE](docs/API_REFERENCE.md) | Python API 参考 |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | 部署指南 |

---

## 平台限制

以下是实现过程中发现的 Windows 平台限制，已在设计中规避：

| 限制 | 影响 | 规避方式 |
|------|------|----------|
| **Low IL 全盘只读** | 沙箱进程无法向宿主磁盘任何位置写（含 `%TEMP%` 宿主路径） | 会话可写区 `%LOCALAPPDATA%\win-sandbox\sessions\<pid>-<process_id>\writable` 可写，TEMP/TMP 重定向 |
| **Low IL 读受限目录** | 宿主目录带显式受限 ACL（受控文件夹/安全工具注入的 AppContainer ACE、特殊 DACL）时，沙箱内对该目录的读/执行也被拒绝（`CreateProcess` 报 87）——完整性强制之外的第二道闸 | 沙箱内要访问的可执行/数据放普通 ACL 目录（如 `%LOCALAPPDATA%` 下）；不要依赖 Desktop/项目目录的读可达性 |
| **allowlist 需管理员** | `net_policy=allowlist` 依赖 WFP connect filter，非管理员 Open 失败 | 记 Warn 降级（语义等同 `unrestricted`），`capabilities` 报告的 `network` 模块给出 degraded_reason |
| **ETW 内核 session 需管理员** | 非管理员无法启动真 ETW trace | 条件编译：管理员走真 ETW，非管理员走降级轮询模式 |
| **TerminateProcess 返回歧义** | 对已退出进程返回 `ERROR_ACCESS_DENIED`，与权限不足无法区分 | 调用前用 `WaitForSingleObject(h, 0)` 预检测进程状态 |
| **DACL generic mapping** | 写入 `GENERIC_ALL` 的 ACE 会被内核自动转换为 `FILE_ALL_ACCESS` | DACL 断言使用对象类型对应的 specific mask |

---

## 已知问题

- **ETW 降级模式局限**（非管理员）：无注册表事件、文件事件仅覆盖配置目录；进程/文件/网络降级能力见 USER_GUIDE §6.4
- **`cmd /c "绝对路径\程序.exe"`（CreateProcess 直传形态）**：cmd 的引号剥除规则会把"路径+参数"整串当作文件名（报"系统找不到文件"）；需用 `cmd /c ""C:\path\a.exe" arg"` 双层引号或省略路径引号（路径无空格时）
- **stdin 写入阻塞上限 30s**：子进程不读 stdin 时 `write_pipe` 超时抛异常（OVERLAPPED 写 + CancelIoEx），不会无限挂起

---

## License

MIT
