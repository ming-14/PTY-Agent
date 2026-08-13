# Phase 4：文件系统隔离（M4）

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| Phase | 4 |
| 对应里程碑 | M4 |
| 创建日期 | 2026-07-26 |
| 前置 Phase | Phase 3 |

---

## 2. 目标

在 Phase 2 路径白名单基础上，实现完整文件系统隔离。结束时支持 4 种模式（whitelist / temp-workspace / redirect / combined），写操作可隔离到临时区，结束后可保留/丢弃/归档。能跑通 PRD 场景 C 的文件部分。

---

## 3. 范围

### In Scope
- `IFileSystemIsolator` 接口 + `FileSystemIsolatorImpl`
- 4 种模式：
  - `whitelist`：仅白名单授权（Phase 2 已实现，本阶段复用）
  - `temp-workspace`：复制工作目录到临时区
  - `redirect`：NTFS Junction 重定向写操作
  - `combined`（默认）：whitelist 读 + temp-workspace 写
- 临时工作区管理（`%TEMP%\win-sandbox-<id>\`）
- 退出策略：`keep` / `discard` / `archive`（zip 打包）
- 路径规则配置（`read_paths` / `write_paths` / `execute_paths`，支持 `%ENV%` 与 glob）
- `FileSystemConfig` 实体
- `TranslatePath` 虚拟路径到真实路径映射

### Out of Scope
- minifilter 驱动重定向（Phase 2 候选，TDD O1 待决）
- 文件访问行为监控 ETW（Phase 6）
- 网络隔离（Phase 5）

---

## 4. 前置依赖

- Phase 3 全部交付物
- NTFS Junction API（`DeviceIoControl FSCTL_SET_REPARSE_POINT` 或 `CreateSymbolicLink` with SYMBOLIC_LINK_FLAG_DIRECTORY）
- zip 归档库（推荐 minizip 或 zipUtils）

---

## 5. 任务清单

### T4.1 IFileSystemIsolator 接口
- `src/core/ports/IFileSystemIsolator.hpp`
- 方法：`Setup`、`TranslatePath`、`Archive`、`Discard`、`Teardown`
- `src/core/entities/FileSystemConfig.hpp`：mode、read_paths、write_paths、execute_paths、temp_workspace

**验收**：接口定义完成。

### T4.2 FileSystemIsolatorImpl — whitelist 模式
- 复用 Phase 2 的 `GrantSidAccess` 逻辑
- 仅授权配置路径
- 无临时区

**验收**：白名单外访问被拒。

### T4.3 FileSystemIsolatorImpl — temp-workspace 模式
- 创建临时区目录：`%TEMP%\win-sandbox-<id>\workspace\`
- 复制 `temp_workspace.source` 到临时区
- 被隔离进程 working_dir 设为临时区
- 授予 AppContainer SID 对临时区完全控制
- 不重定向（被隔离程序看到的路径就是临时区路径）

**验收**：程序写入 `working_dir\out.txt`，实际落在临时区，宿主原目录不受影响。

### T4.4 FileSystemIsolatorImpl — redirect 模式（Junction）
- 在临时区创建 Junction 指向原工作目录：
  - 临时区：`%TEMP%\win-sandbox-<id>\root\`
  - Junction：`root\proj -> C:\original\proj`
- 授予 AppContainer SID 对 Junction 目标的 Read 权限
- 授予对临时区可写子目录的 Write 权限
- 被隔离进程 working_dir 设为 Junction 路径
- 程序看到的路径不变，但写操作可重定向到临时区可写目录

**验收**：程序写入 `C:\proj\out.txt`（Junction 重定向），实际落在临时区。

### T4.5 FileSystemIsolatorImpl — combined 模式（默认）
- whitelist 授予读路径
- temp-workspace 提供写区域
- 二者组合

**验收**：读白名单路径 + 写临时区均工作。

### T4.6 退出策略
- `keep`：保留临时区，返回路径
- `discard`：递归删除临时区
- `archive`：zip 打包到指定路径，然后删除临时区
- `Teardown` 时按配置执行

**验收**：`archive` 模式结束后生成 zip，临时区已清理。

### T4.7 路径规则引擎
- `src/adapters/PathRuleEngine.hpp/cpp`
- `%ENV%` 展开（`ExpandEnvironmentStringsW`）
- glob 匹配（`PathMatchSpecW` 或自实现）
- 路径规范化（`GetFullPathNameW`）

**验收**：`%PROJECT_DIR%\*.exe` 正确展开并匹配。

### T4.8 配置扩展
- `ConfigLoader` 支持 `filesystem` 段完整配置
- schema 校验：mode 枚举、路径非空、exit_strategy 枚举

**验收**：4 种模式配置均能加载。

### T4.9 e2e 测试
- `tests/e2e/test_filesystem.py`
- 用例 1（whitelist）：白名单外写失败
- 用例 2（temp-workspace）：写入隔离到临时区，宿主原路径不受影响
- 用例 3（redirect）：Junction 重定向生效
- 用例 4（combined）：读白名单 + 写临时区
- 用例 5（archive）：退出后生成 zip
- 用例 6（discard）：退出后临时区清理

**验收**：6 子用例全绿。

---

## 6. 风险

| 风险 | 缓解 |
|------|------|
| Junction 跨卷失败 | 配置约束临时区与原目录同卷，或降级为复制 |
| GetFinalPathNameByHandle 看穿 Junction | 接受此限制，文档说明 |
| 复制大目录慢 | temp-workspace 模式警告大目录；推荐 redirect |
| zip 归档内存占用 | 流式归档，不一次性加载 |
| AppContainer 拒绝 Junction 访问 | 授予临时区与 Junction 目标双重权限 |

---

## 7. 退出条件

- [x] 4 种文件系统模式均实现并测试
- [x] 写操作隔离到临时区
- [x] archive/discard/keep 退出策略工作
- [x] 路径规则（%ENV% + glob）工作
- [x] e2e 7 子用例全绿

---

## 8. 实现记录（2026-07-30）

### 实际完成情况

| 任务 | 状态 | 说明 |
|------|------|------|
| T4.1 IFileSystemIsolator 接口 | ✅ | `IFileSystemIsolator.hpp` + `FileSystemConfig.hpp` + `PathRule.hpp`（从 IsolationPolicy.hpp 提取以打破循环依赖）|
| T4.2 whitelist 模式 | ✅ | 复用 Phase 2 GrantSidAccess 逻辑 |
| T4.3 temp-workspace 模式 | ✅ | 复制工作目录到 `%TEMP%\win-sandbox-<id>\` |
| T4.4 redirect 模式（Junction） | ✅ | NTFS Junction 重定向 |
| T4.5 combined 模式 | ✅ | whitelist 读 + temp-workspace 写 |
| T4.6 退出策略 | ✅ | keep / discard / archive 实现 |
| T4.7 路径规则引擎 | ✅ | `PathRuleEngine.hpp/cpp`，`%ENV%` 展开 + glob 匹配 |
| T4.8 配置扩展 | ✅ | ConfigLoader 解析 `filesystem` 段，`IsolationPolicy.fs_config`（std::optional）|
| T4.9 e2e 测试 | ✅ | `test_filesystem.py` 7/7 PASS（比计划多 1 个用例）|

### 偏离计划

1. **PathRule.hpp 独立提取**：原计划 PathRule 定义在 IsolationPolicy.hpp 中，实际提取为独立头文件以打破循环依赖
2. **FileSystemConfig 通过 std::optional 集成**：`IsolationPolicy.fs_config` 使用 `std::optional<FileSystemConfig>`，未配置时为 nullopt（Phase 1-3 兼容）
3. **e2e 测试 7 个而非 6 个**：增加了 archive race condition 修复验证用例
4. **archive race 修复**：发现 archive 操作与进程退出存在竞态，通过 `_close_gracefully` 修复
5. **fs_isolator 传 nullptr 兼容**：SandboxInstance::StartProcess 根据 fs_config 有条件创建 FileSystemIsolatorImpl，未配置时传 nullptr 避免影响现有测试

### 集成点

- `SandboxInstance::StartProcess`：根据 `fs_config` 有条件创建 `FileSystemIsolatorImpl`
- `StartProcessUseCase`：在 AppContainer 路径内调用 `fs_isolator->Setup()`
- `SandboxInstance` 析构：调用 `fs_isolator->Teardown()`
- `ConfigLoader`：解析 `filesystem` 段（mode/read_paths/write_paths/execute_paths/temp_workspace/exit_strategy）
- `StartProcessPayloadParser`：解析 IPC payload 中的 filesystem 配置

### Commit

- `b1d5fe1` feat(phase4): filesystem isolation with temp_workspace/whitelist/redirect/combined modes
