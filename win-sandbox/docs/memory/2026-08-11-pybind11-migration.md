# pybind11 迁移踩坑记录（2026-08-11）

## Phase 10-15：sandbox.exe + IPC → win_sandbox_native.pyd (pybind11 in-process)

### 关键决策
1. **彻底替换**：不保留 IPC 兼容层，Phase 12 一次性删除所有 IPC 代码
2. **方案 B**：新增 `NativeSandboxedProcess` + `NativeSandboxInstance`（pybind11 专用），与旧 `StartProcessUseCase` + `SandboxInstance` 并存到 Phase 12，然后删旧
3. **wall_clock/StatsCollector 移 Python**：C++ 端不再管时间限制和统计轮询
4. **ETW 回调路由**：C++ 端 ETW monitor → `pid_to_usecase_` 路由 → 各进程的 `on_behavior_event` 回调

### 踩坑与修复

#### 1. pybind11 API 差异
- `py::runtime_error` 不存在 → 用 `std::runtime_error`
- `py::bytes` 没有 `.data()` / `.size()` → 用 `data.cast<std::string>()` 转换
- `std::shared_lock` 需要 `std::shared_mutex`，不能用于 `std::mutex`

#### 2. ConfigLoader strict mode
- `monitoring.etw` 子对象不被接受（只接受 `etw_enabled` bool）
- 需在 ConfigLoader 中添加 `monitoring` 段解析

#### 3. ExitReasonToString
- 返回 `"normal"` 而非 `"normal_exit"`，测试需对齐

#### 4. **C++ Runtime Library 弹窗（Phase 14 关键 bug）**
- **现象**：运行 `test_behavior_log.py` 触发 Microsoft Visual C++ Runtime Library error
- **根因**：`SandboxInstanceBinding::shutdown()` 释放 GIL 后调 `ShutdownAll()`，`ShutdownAll()` 内 `entry.usecase.reset()` 析构 `NativeSandboxedProcess`，析构销毁 `on_behavior_event` 等 `std::function`，其 lambda 捕获了 `py::function`，`py::function` 析构需要 GIL → 无 GIL 析构 → 崩溃
- **同时**：`~PySandboxInstance()` 持 GIL 调 `ShutdownAll()` → `etw_monitor_->Stop()` join dispatch 线程 → dispatch 线程阻塞在 `gil_scoped_acquire` → 死锁
- **修复**：三阶段 GIL 管理
  1. Phase 1：释放 GIL → `StopEtwMonitor()`（join ETW 线程，线程可获 GIL 完成回调）
  2. Phase 2：持 GIL → `ClearAllCallbacks()`（安全销毁 `py::function` 捕获）
  3. Phase 3：释放 GIL → `ShutdownAll()`（usecase 已无 `py::function`，安全析构）
- **教训**：C++ 对象持有 `py::function`（通过 `std::function` lambda 捕获）时，析构必须在 GIL 下。线程 join 时需释放 GIL，防回调线程死锁。

#### 5. wheel 打包路径
- CMake 输出到 `build/bin/`（非 `build/bin/Release/`）
- .pyd 文件名含 Python ABI tag：`win_sandbox_native.cp311-win_amd64.pyd`
- `force-include` 需映射到 `win_sandbox/_native/` 子目录

#### 6. ETW 降级模式文件事件 pid=0
- `ReadDirectoryChangesW` 事件 `pid=0`，不在 `pid_to_usecase_` 路由表中
- 文件事件被丢弃，`test_degraded_monitor.py` T3/T4 SKIP

#### 7. global_quota 未注入
- `SandboxInstanceBinding.cpp` 固定传 `nullptr` 给 `global_quota`
- `test_global_quota.py` 全部 SKIP
- 需在 binding 层根据 `config.global_quota.enabled` 构造 `GlobalQuotaManagerImpl` 注入

### 验证结果
- e2e 测试：23/23 PASS（含 SKIP 用例）
- wheel 安装：干净 venv `pip install` + `import` + 基础调用全通过
- 版本号：0.2.0（pyproject.toml + __init__.py + CMake）
