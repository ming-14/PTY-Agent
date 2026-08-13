# Phase 2 候选功能评估：Server Silo 更强隔离 + 多沙箱全局资源配额

| 项目 | 内容 |
|------|------|
| 创建日期 | 2026-08-06 |
| 状态 | 评估完成，实现方案确定 |
| 关联文档 | PRD §9.2 开放问题（2 项）、TDD D1 方案 B |
| 前置 | Phase 0-7 全部完成 |

---

## 1. 背景

PRD §9.2 列出的两个开放问题，Phase 7 交付时标记为"Phase 2 候选"，本次评估并落地：

1. **Server Silo 更强隔离**：是否引入 Server Silo（进程隔离容器）作为更强隔离层？
2. **多沙箱全局资源配额**：多个沙箱实例间的 CPU/内存全局配额。

---

## 2. 评估一：Server Silo 更强隔离

### 2.1 技术调研结论

通过微软官方文档检索 + 本机 PoC 实测，结论如下：

| 维度 | 结论 |
|------|------|
| 用户态 API 可用性 | **未文档化**。`NtCreateServerSilo` / `NtAssignProcessToSiloObject` 不在 ntdll 导出表 |
| 本机实测（Win10 22H2 19045 Pro for Workstations） | `JobObjectCreateSilo`(35) 返回 `STATUS_INVALID_PARAMETER`（即使启用 SeTcbPrivilege）；`JobObjectSiloBasicInformation`(36) 返回 `STATUS_JOB_NO_CONTAINER`(0xC0000509) |
| 权限要求 | 创建 Silo 需 SeTcbPrivilege / SeAssignPrimaryTokenPrivilege（等价系统权限），普通用户不可行 |
| 官方支持 | Win10 客户端**不支持进程隔离容器**（仅 Win11 预览 / Server 版），微软文档明确 |
| SDK 支持 | `winnt.h` 有 `JobObjectCreateSilo`/`JobObjectSiloBasicInformation` 枚举，但**无用户态封装**，需自行声明 ntdll 函数 |
| 隔离能力 | Silo = 带 SILO 标志的 Job，新增**视图级**隔离：对象命名空间、注册表 hivestack、文件系统/挂载重定向、网络 compartment |
| 与现有方案关系 | 与 Job（资源限制）+ AppContainer（ACL 限制）**正交可叠加**；Silo 内仍需 Job 做资源限制 |

### 2.2 PoC 实测记录

编写并运行了 `silo_poc.cpp`（临时文件，未入库）：

```
NtCreateJobObject: 0x00000000 handle=00000000000000D4
NtSetInformationJobObject(JobObjectCreateSilo): 0xC000000D   <- STATUS_INVALID_PARAMETER
NtQueryInformationJobObject(SiloBasic): 0xC0000509 IsInServerSilo=0  <- STATUS_JOB_NO_CONTAINER
```

即使在管理员权限下启用 `SeTcbPrivilege` / `SeAssignPrimaryTokenPrivilege` / `SeIncreaseQuotaPrivilege`，
`JobObjectCreateSilo` 仍然返回 `STATUS_INVALID_PARAMETER`。**结论：本机 Win10 22H2 客户端不支持用户态创建 Server Silo**，
与官方文档"Win10 客户端不支持进程隔离容器"一致。

### 2.3 设计决策

**采用"可插拔 Silo 适配器"方案（条件启用，失败优雅降级）**：

- 新增 `ISilo` 端口 + `SiloImpl` 实现（infra 层）。
- `SiloImpl::Start()` 动态探测可用性：
  - 检查 `IsElevated()`（非管理员直接不可用）；
  - 尝试 `NtSetInformationJobObject(JobObjectCreateSilo)`，成功 → Silo 可用；返回 `STATUS_INVALID_PARAMETER` → 平台不支持，标记不可用。
- 配置开关 `silo.enabled`（默认 false）：仅在显式开启且平台支持时启用。
- 不可用时：CapabilityReport 标记 `silo = unavailable`，沙箱继续用现有 Job+AppContainer，不影响任何功能。
- 隔离增强点：把沙箱 Job 转换为 Silo Job（命名空间/注册表/文件系统视图隔离），资源限制逻辑复用现有 JobObject。

**理由**：完整启用 Silo 在本机无验证条件（平台不支持），因此不默认开启；提供能力探测与条件启用路径，
在支持的平台（Win Server / Win11 预览）可通过配置开启，本机优雅跳过。这与现有 PermissionDetector 权限自适应模式一致。

### 2.4 风险

- ntdll 未文档化 API 无版本契约，跨 Windows 版本行为可能漂移 → 通过"探测失败即降级"缓解，不承诺不兼容版本可用。
- Win10 客户端永不支持 → 本功能实际价值主要在 Server / 特殊环境；文档明确此边界。

---

## 3. 评估二：多沙箱全局资源配额

### 3.1 现状调研

- 当前架构：**一个 sandbox.exe 进程 = 一个沙箱实例**（main.cpp 单实例，`SandboxInstance` 内多进程由 Job 各自管理）。
  "多沙箱"实际是多个 sandbox.exe 进程（各自 pipe）。
- 资源限制：每进程独立 `ResourceQuota` → 每 Job 独立 `SetInformationJobObject`（CPU rate / memory / 进程数）。
- **无任何跨实例共享的配额机制**。

### 3.2 方案对比

| 方案 | 说明 | 跨进程共享 | 复杂度 |
|------|------|-----------|--------|
| A. 嵌套 Job | parent job 设全局限制，child job 挂入 | 仅单进程内 | 低 |
| B. 共享内存配额池 | 命名共享内存 + Mutex，多 sandbox.exe 进程登记 | ✅ 跨进程 | 中 |
| C. 软件层聚合汇总 | 定时 QueryAccounting 汇总 + 节流 | ✅ | 高 |

**决策：方案 B（跨进程共享内存配额池）**，理由：
- 当前"多沙箱 = 多进程"，只有跨进程机制才能真正覆盖全局配额；
- 共享内存 + 命名 Mutex 是 Win32 标准跨进程同步，无需内核驱动；
- 与现有"每实例独立 Job"正交，可叠加。

### 3.3 设计

**新增配置段** `global_quota`（SandboxConfig）：

```json
{
  "global_quota": {
    "enabled": false,
    "pool_name": "win-sandbox-quota",     // 共享内存池名（跨进程唯一）
    "max_cpu_rate_percent": 100,           // 全局 CPU 速率上限（所有实例合计）
    "max_memory_mb": 2048,                 // 全局内存上限（所有实例合计）
    "max_processes": 256                   // 全局进程数上限（所有实例合计）
  }
}
```

**核心组件**：
- `core/entities/GlobalQuota.hpp`：配置实体 + 配额统计实体。
- `core/ports/IGlobalQuotaManager.hpp`：端口（Register/Unregister/Acquire/Release/Query）。
- `infra/globalquota/GlobalQuotaManagerImpl.hpp/cpp`：
  - 命名 `CreateFileMapping` + `MapViewOfFile` 保存全局计数（进程共享）；
  - 命名 `CreateMutex` 保护读写；
  - 首次创建者初始化上限，后续实例只读；末实例释放时清理。
- `SandboxInstance` 接入：启动进程前 `Acquire`（检查并占用 CPU/内存/进程数额度），进程退出后 `Release`。

**超限行为**：`StartProcess` 返回明确错误码（`GlobalQuotaExceeded`），Python 端收到 `Error` 事件并提示，不启动进程。

### 3.4 影响面

- `SandboxConfig` / `ConfigLoader`：新增 `global_quota` 段解析。
- `SandboxInstance`：构造注入 `IGlobalQuotaManager`，StartProcess 前后 acquire/release。
- `main.cpp`：管理员进程可选创建 GlobalQuotaManagerImpl 注入。
- 测试：e2e 验证跨实例共享（两个 sandbox.exe 同时启动，额度耗尽后第三个被拒）。

---

## 4. 实施清单

| 任务 | 文件 | 状态 |
|------|------|------|
| 评估文档 | `docs/design/Phase2-Candidates-Evaluation-20260806.md` | ✅ 本文档 |
| ISilo 端口 + SiloImpl | `src/core/ports/ISilo.hpp`、`src/infra/silo/SiloImpl.{hpp,cpp}` | ✅ 已实现 |
| Silo 配置 + main 接入 | `SandboxConfig.silo`、`ConfigLoader`、`main.cpp` | ✅ 已实现 |
| GlobalQuota 实体 + 端口 | `src/core/entities/GlobalQuota.hpp`、`src/core/ports/IGlobalQuotaManager.hpp` | ✅ 已实现 |
| GlobalQuotaManagerImpl | `src/infra/globalquota/GlobalQuotaManagerImpl.{hpp,cpp}` | ✅ 已实现 |
| ConfigLoader 解析 | `src/adapters/ConfigLoader.cpp` | ✅ 已实现 |
| SandboxInstance 接入 | `src/adapters/SandboxInstance.cpp` | ✅ 已实现（Acquire/Release + Silo Elevate） |
| 测试 | `tests/e2e/test_silo.py`（4 用例）、`tests/e2e/test_global_quota.py`（5 用例） | ✅ 已实现 |

**验证结果（2026-08-06）**：
- `test_silo.py`：4/4 PASS（Win10 客户端验证降级路径不破坏功能）
- `test_global_quota.py`：5/5 PASS（含跨进程共享池验证）

---

## 5. 结论

1. **Server Silo**：本机 Win10 22H2 客户端实测不可用（API 未导出 + 创建返回 STATUS_INVALID_PARAMETER）。
   实现"条件启用的可插拔适配器"，支持的平台可开启，本机优雅跳过并报告 unavailable。不作为默认隔离路径。
2. **多沙箱全局资源配额**：完全可行，采用跨进程共享内存配额池（方案 B），实现后可跨多个 sandbox.exe 进程共享
   CPU/内存/进程数上限。
