# win-sandbox 部署指南

部署与发布操作指南。涵盖构建、安装、管理员/普通用户部署差异、CI/CD 集成、wheel 打包与安全注意事项。

> 对应 Phase 7 任务 T7.11。使用教程见 [USER_GUIDE.md](USER_GUIDE.md)，API 见 [API_REFERENCE.md](API_REFERENCE.md)。

---

## 1. 部署形态

win-sandbox 为单一 pybind11 in-process 库：

| 组件 | 形态 | 说明 |
|------|------|------|
| `win_sandbox_native.pyd` | C++ 原生扩展（pybind11） | 沙箱核心，被 Python `import win_sandbox` 直接加载 |
| `win_sandbox` | Python 包（wheel） | helpers + pybind11 绑定，`pip install win-sandbox` 即用 |

两种部署方式：
1. **源码部署**：从仓库构建 `win_sandbox_native.pyd` + `pip install .`
2. **产物部署**：分发构建好的 wheel（CI artifact 或手动构建），`pip install win-sandbox` 即用

---

## 2. 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 1809+ / Windows 11 |
| 架构 | x64（仅支持 64 位） |
| Python | 3.10 - 3.12（64 位；`X | Y` 联合类型注解需 3.10+） |
| 磁盘 | ~200MB（构建） / ~10MB（仅运行，wheel 内嵌 pyd） |

### 权限模式

| 模式 | 可用能力 | 适用场景 |
|------|---------|---------|
| 管理员 | 全部能力：ETW 内核监控、WFP 网络白名单拦截、CPU/IO 速率限制 | 安全要求高的生产环境、样本分析 |
| 普通用户 | Job 资源限制 + Low IL 文件系统隔离（全盘只读 + 可写区）；ETW 降级；`allowlist` 网络策略降级为 `unrestricted` | 开发调试、受限 CI runner |

部署前请评估目标环境的权限模式，见 [USER_GUIDE.md §7](USER_GUIDE.md) 的 capabilities 报告说明。

---

## 3. 从源码构建

### 3.1 获取依赖

```powershell
git clone https://github.com/ming-14/win-sandbox
cd win-sandbox
git submodule init
git submodule update
```

> 国内网络：`git -c url.https://v4.gh-proxy.org/.insteadOf=https://github.com/. submodule update`

第三方依赖（Git submodule）：
- [WIL](https://github.com/microsoft/wil) — Windows Implementation Libraries（RAII wrapper）
- [nlohmann/json](https://github.com/nlohmann/json) — JSON 解析（配置文件）
- [spdlog](https://github.com/gabime/spdlog) — 日志库（静态链接，内置 fmt）
- [pybind11](https://github.com/pybind/pybind11) — Python ↔ C++ 绑定

### 3.2 构建 win_sandbox_native.pyd

```powershell
# VS 2026 Preview 示例
& "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"

cmake -B build -G "Visual Studio 18 2026" -A x64 -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
```

产物：`build/bin/Release/win_sandbox_native.pyd`（pybind11 扩展模块）

### 3.3 安装 Python 包

```powershell
# 可编辑安装（开发）
pip install -e .

# 普通安装
pip install .
```

---

## 4. 分发部署

### 4.1 构建 wheel

wheel 打包引用 `../build/bin/Release/win_sandbox_native.pyd`，**必须先完成 Release 构建**：

```powershell
# 0. 前置：Release 构建 win_sandbox_native.pyd（见 §3.2）

# 1. 构建原始 wheel
pip install build
python -m build

# 2. 修正平台标记（py3-none-any → py3-none-win_amd64）
python scripts/fix_wheel_platform.py
```

产物：
- 原始：`dist/win_sandbox-0.2.0-py3-none-any.whl`
- 修正后（分发用）：`dist/win_sandbox-0.2.0-py3-none-win_amd64.whl`

wheel 内容：
- `win_sandbox/` Python 源码（helpers）
- `win_sandbox/win_sandbox_native.pyd`（Release 构建，通过 hatchling `force-include` 注入）

> `fix_wheel_platform.py` 直接改写 WHEEL 元数据的 Tag 并重命名文件；未修正的
> `py3-none-any` wheel 在非 Windows 平台也能安装成功但运行即失败，勿分发。

### 4.2 终端用户安装

```powershell
pip install win-sandbox
```

无运行时依赖（spdlog 静态链接进 pyd），安装即用：

```python
import win_sandbox
sb = win_sandbox.SandboxInstance()
```

### 4.3 验证部署

```powershell
python -c "import win_sandbox; sb = win_sandbox.SandboxInstance(); print(sb.capabilities); sb.shutdown()"
python tests/e2e/smoke.py
```

`smoke.py` 退出码 0 表示部署成功。

---

## 5. 管理员 vs 普通用户部署

### 5.1 管理员部署（完整能力）

以管理员身份运行 Python 进程。部署后验证：

```powershell
# 检查 capabilities
python -c "
import win_sandbox
sb = win_sandbox.SandboxInstance()
print(sb.capabilities)
sb.shutdown()
"
```

预期：`job_object` / `low_il_token` / `etw` / `network` / `pipe_security` 全部 `available: true`。

### 5.2 普通用户部署（降级模式）

普通用户环境自动降级：
- `low_il_token` / `job_object` / `pipe_security`：始终可用（Low IL token 纯用户态派生，不依赖管理员）
- `etw`：降级为进程轮询 + 可选目录文件监控 + 网络轮询
  - 进程事件：进程列表轮询（ProcessStart/Stop）
  - 文件事件：`ReadDirectoryChangesW` 递归监控 `monitoring.degraded_monitor_dirs` 配置的目录
  - 网络事件：`GetExtendedTcpTable`/`GetUdpTable` 轮询（TcpConnect/UdpSend）
  - 注册表事件不可用（无法全局监控）
- `network`：不可用（WFP callout 需管理员）→ `net_policy=allowlist` 降级为 `unrestricted` 语义（记 Warn，进程网络不受限）

无需额外配置，沙箱启动时自动检测并报告。应用程序应根据 capabilities 报告调整功能。

---

## 6. 本地回归与测试

### 6.1 本地回归脚本

> 注意：`build/bin/win_sandbox_native.pyd`（顶层）是测试优先使用的二进制路径，本地更新构建后
> 若 e2e 仍跑旧版，请同步 `copy build\bin\Release\win_sandbox_native.pyd build\bin\`。

```powershell
# 全量 e2e（排除需管理员的 test_etw_admin.py）
python tests/e2e/run_all_regression.py

# 单元测试
ctest --test-dir build -C Debug
```

---

## 7. 安全注意事项

| 事项 | 说明 |
|------|------|
| 句柄所有权 | stdin/stdout/stderr 管道句柄以 `int` 暴露给 Python，Python 用 `close_handle` 显式关闭，避免句柄泄漏 |
| 残留清理 | 沙箱初始化时自动清理残留会话可写区目录（StartupCleanup）/ ETW session |
| 网络默认限制 | `net_policy=allowlist` 时按 `net_allowlist` 白名单放行（SOCKS5 代理，需管理员）；`unrestricted`（默认）不做网络限制 |
| 生产环境 | 建议以专用低权限账户运行 Python 进程，配合最小 capability 集 |
| 全局配额 | 多沙箱实例共享配额池时，跨进程共享内存 DACL 仅授予当前用户 |

---

## 8. 升级与回滚

- 升级：`pip install --upgrade win-sandbox`（或重新构建 wheel 后安装）
- 回滚：`pip install win-sandbox==<旧版本>`
- 数据兼容：Phase 12 起删除 `ipc` / `stats` 配置段，Phase 16 起 **不向后兼容旧隔离字段**（`appcontainer` / `filesystem` / `network` 段、`fs_mode` / `capabilities` / `path_rules` 已删除），升级后旧配置会加载失败——需按 [USER_GUIDE.md §5.4](USER_GUIDE.md) 迁移到 `isolation` 段；其余段（`logging` / `default_quota` / `monitoring` / `silo` / `global_quota`）向后兼容

---

## 9. 常见部署问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `ImportError: win_sandbox_native` | pyd 未正确打包 / Python 版本不匹配 | 重新构建 wheel，确认 Python 3.10-3.12 x64 |
| `pip install` 成功但 import 失败 | 平台标记未修正（py3-none-any） | 用 `fix_wheel_platform.py` 修正后分发 |
| e2e 用旧二进制 | `build/bin/win_sandbox_native.pyd` 未更新 | 从 Release 复制新构建 |
| ETW 无行为日志 | 非管理员降级模式 | 用管理员运行，或接受降级 |
| 配置文件加载报 `unknown field` | 配置含已删除的旧段/字段（`ipc` / `stats` 为 Phase 12 删除；`appcontainer` / `filesystem` / `network` / `fs_mode` / `path_rules` 为 Phase 16 删除） | 按 [USER_GUIDE.md §5.4](USER_GUIDE.md) 迁移到 `isolation` 段（schema 不向后兼容旧字段，加载期显式拒绝） |

详细排查见 [TROUBLESHOOTING.md](memory/TROUBLESHOOTING.md)。
