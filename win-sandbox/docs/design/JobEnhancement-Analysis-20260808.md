# win-sandbox Job 功能增强分析报告

**文档类型**: 技术分析报告  
**项目**: win-sandbox  
**分析日期**: 2026-08-08  
**作者**: rikka  
**状态**: 待评审

---

## 1. 文档概述

### 1.1 背景

win-sandbox 当前的 Job Object 实现主要聚焦于资源限制（CPU/内存/IO/进程数）和级联终止。在实际运维和监控场景中，还需要更细粒度的进程管理能力，如进程列表查询、精确的退出状态判断、崩溃静默等。本报告分析当前 Job 功能的不足，并提出功能增强方案。

### 1.2 分析目标

- 评估当前 win-sandbox Job 功能的完整性
- 识别缺失的关键进程管理能力
- 设计功能增强方案，保持架构独立性
- 提供详细的 Phase 实施计划

### 1.3 适用范围

本报告仅针对 Windows 平台的 Job Object 功能增强，不涉及跨平台兼容性。

---

## 2. 当前 Job 功能分析

### 2.1 核心接口

**端口接口**: `src/core/ports/IJobObject.hpp`

**实现类**: `src/infra/job/JobObjectImpl.cpp`

**当前提供的能力**:
```cpp
class IJobObject {
    virtual Result<void> Create() = 0;
    virtual Result<void> SetResourceLimits(const ResourceQuota& quota) = 0;
    virtual Result<void> SetUiLimits(bool no_ui) = 0;
    virtual Result<void> AssignProcess(void* process_handle) = 0;
    virtual Result<void> TerminateAll(uint32_t exit_code) = 0;
    virtual Result<void> TerminateProcess(void* process_handle, uint32_t exit_code) = 0;
    virtual Result<JobAccountingInfo> QueryAccounting() const = 0;
    virtual Result<uint64_t> QueryPeakMemory() const = 0;
    virtual Result<void> RegisterNotificationSink(IJobNotificationSink& sink) = 0;
    virtual Result<void> Shutdown() = 0;
    virtual void* GetHandle() const = 0;
};
```

### 2.2 当前 Job 通知类型

win-sandbox 的 `JobNotification` 实体支持的通知类型：

| 消息类型 | Win32 常量 | 说明 |
|----------|-----------|------|
| `EndOfJobTime` | `JOB_OBJECT_MSG_END_OF_JOB_TIME` | Job CPU 时间耗尽 |
| `EndOfProcessTime` | `JOB_OBJECT_MSG_END_OF_PROCESS_TIME` | 单进程 CPU 时间耗尽 |
| `ActiveProcessLimit` | `JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT` | 进程数超限 |
| `ProcessMemoryLimit` | `JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT` | 单进程内存超限 |
| `JobMemoryLimit` | `JOB_OBJECT_MSG_JOB_MEMORY_LIMIT` | Job 内存超限 |
| `ProcessExit` | `JOB_OBJECT_MSG_EXIT_PROCESS` | 进程退出 |
| `ActiveProcessEmpty` | `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO` | Job 内无进程 |
| `NewProcess` | `JOB_OBJECT_MSG_NEW_PROCESS` | 新进程加入 Job |
| `Unknown` | 未识别的消息 | - |

### 2.3 当前进程查询功能

win-sandbox 提供的进程查询功能：

**`QueryAccounting()`**:
- 返回 Job 会计信息（CPU/IO/进程数/页错误）
- 使用 `JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION` 结构体
- 包含进程总数、活动进程数、已终止进程数

**`QueryPeakMemory()`**:
- 返回单进程峰值内存
- 使用 `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` 结构体

### 2.4 当前 Job 限制标志

win-sandbox 设置的 Job 限制标志：

```cpp
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE    // 关闭句柄时终止所有进程
JOB_OBJECT_LIMIT_JOB_TIME             // CPU 时间限制
JOB_OBJECT_LIMIT_PROCESS_MEMORY      // 单进程内存限制
JOB_OBJECT_LIMIT_JOB_MEMORY           // Job 内存限制
JOB_OBJECT_LIMIT_ACTIVE_PROCESS      // 进程数限制
JOB_OBJECT_LIMIT_BREAKAWAY_OK         // 允许子进程逃逸（可选）
JOB_OBJECT_UILIMIT_*                  // UI 限制（可选）
```

---

## 3. 功能缺失分析

### 3.1 缺失功能识别

在实际运维和监控场景中，当前 Job 实现存在以下功能缺失：

#### 缺失 1: 进程列表查询

**需求场景**: 
- 运维人员需要查看沙箱内当前运行的所有进程
- 监控系统需要获取进程列表进行健康检查
- 调试时需要确认特定进程是否在沙箱内

**当前现状**: 仅提供进程数量统计，不提供具体 PID 列表

**影响**: 无法进行精细化的进程管理和监控

#### 缺失 2: 进程退出码查询

**需求场景**:
- 精确判断进程退出状态（成功/失败）
- 崩溃分析需要具体的退出码
- 自动化脚本需要根据退出码决定后续操作

**当前现状**: 不支持查询单个进程的退出码

**影响**: 无法精确判断进程退出原因

#### 缺失 3: 进程路径获取

**需求场景**:
- 日志记录需要进程的完整路径信息
- 调试时需要确认启动的是哪个可执行文件
- 安全审计需要追踪进程来源

**当前现状**: 通知中仅包含 PID，不包含路径信息

**影响**: 日志信息不完整，调试困难

#### 缺失 4: 区分正常/异常退出

**需求场景**:
- 监控系统需要区分正常退出和崩溃
- 告警系统需要对异常退出触发告警
- 统计分析需要区分不同退出类型

**当前现状**: 统一为 `ProcessExit`，不区分正常/异常

**影响**: 无法精确区分进程退出类型

#### 缺失 5: 崩溃静默

**需求场景**:
- 自动化测试场景需要崩溃时不弹对话框
- 批量任务执行需要避免阻塞
- 无头服务器环境需要静默模式

**当前现状**: 不设置 `DIE_ON_UNHANDLED_EXCEPTION` 标志

**影响**: 崩溃时可能弹出 Windows 错误对话框，影响自动化场景

### 3.2 功能优先级

| 功能 | 优先级 | 理由 |
|------|--------|------|
| 进程列表查询 | P0 | 运维和监控的基础需求 |
| 进程退出码查询 | P0 | 精确状态判断的基础需求 |
| 区分正常/异常退出 | P0 | 监控和告警的基础需求 |
| 进程路径获取 | P1 | 增强日志和调试能力 |
| 崩溃静默 | P1 | 自动化场景需求 |

---

## 4. 功能增强方案设计

### 4.1 设计原则

1. **架构独立**: 增强功能不破坏现有架构，保持 win-sandbox 的独立性
2. **向后兼容**: 新增功能不影响现有沙箱功能
3. **干净架构**: 遵循 win-sandbox 的分层架构（core/infra/ports）
4. **性能优先**: 进程路径查询等操作需考虑性能影响
5. **按需暴露**: 通过 IPC 选择性暴露需要的功能

### 4.2 接口扩展方案

#### 4.2.1 新增接口方法

在 `IJobObject` 端口接口中新增以下方法：

```cpp
class IJobObject {
    // 现有方法保持不变...

    // 获取 Job 内所有进程的 PID 列表
    // 返回: 成功返回 PID 列表，失败返回错误码
    virtual Result<std::vector<uint32_t>> QueryProcessList() const = 0;

    // 查询单个进程的退出码
    // 参数: pid - 进程 PID
    // 返回: 成功返回退出码，失败返回错误码
    // 注意: 进程仍在运行时返回 STILL_ACTIVE (259)
    virtual Result<uint32_t> QueryProcessExitCode(uint32_t pid) const = 0;

    // 设置崩溃静默标志
    // 参数: silent - true 启用 DIE_ON_UNHANDLED_EXCEPTION，false 禁用
    // 返回: 成功返回 Ok，失败返回错误码
    virtual Result<void> SetCrashSilent(bool silent) = 0;
};
```

#### 4.2.2 通知实体扩展

扩展 `JobNotification` 实体，增加进程路径和退出码信息：

```cpp
struct JobNotification {
    JobNotificationType type = JobNotificationType::Unknown;
    uint32_t pid = 0;
    uint64_t timestamp_ms = 0;

    // 新增字段
    std::string process_name;              // 进程名称（如 "cmd.exe"）
    std::string process_path;              // 进程完整路径
    std::optional<uint32_t> exit_code;     // 退出码（仅退出类通知有效）
};
```

新增通知类型：

```cpp
enum class JobNotificationType {
    // 现有类型保持不变...
    EndOfJobTime,
    EndOfProcessTime,
    ActiveProcessLimit,
    ProcessMemoryLimit,
    JobMemoryLimit,
    ProcessExit,              // 保留以兼容现有代码
    ActiveProcessEmpty,
    NewProcess,
    Unknown,

    // 新增类型
    ProcessExitNormal,        // 进程正常退出（退出码为 0）
    ProcessExitAbnormal,      // 进程异常退出（退出码非零）
};
```

### 4.3 实现方案

#### 4.3.1 进程列表查询实现

使用 `JOBOBJECT_BASIC_PROCESS_ID_LIST` 结构体：

```cpp
Result<std::vector<uint32_t>> JobObjectImpl::QueryProcessList() const {
    if (!job_handle_) {
        return Result<std::vector<uint32_t>>::Err(ErrorCode::InternalError, "Job not created");
    }

    // 第一次调用获取所需缓冲区大小
    DWORD return_length = 0;
    QueryInformationJobObject(job_handle_.get(),
                              JobObjectBasicProcessIdList,
                              nullptr, 0, &return_length);

    // 分配缓冲区
    std::vector<uint8_t> buffer(return_length);
    JOBOBJECT_BASIC_PROCESS_ID_LIST* info =
        reinterpret_cast<JOBOBJECT_BASIC_PROCESS_ID_LIST*>(buffer.data());

    // 第二次调用获取实际数据
    if (!QueryInformationJobObject(job_handle_.get(),
                                    JobObjectBasicProcessIdList,
                                    info, return_length, &return_length)) {
        DWORD err = GetLastError();
        return Result<std::vector<uint32_t>>::Err(
            ErrorCode::JobQueryFailed,
            std::format("QueryInformationJobObject(ProcessIdList) failed: err={}", err));
    }

    // 提取 PID 列表
    std::vector<uint32_t> pids;
    DWORD count = info->NumberOfProcessIdsInList;
    pids.reserve(count);
    for (DWORD i = 0; i < count; ++i) {
        pids.push_back(info->ProcessIdList[i]);
    }

    return Result<std::vector<uint32_t>>::Ok(std::move(pids));
}
```

#### 4.3.2 进程退出码查询实现

使用 `GetExitCodeProcess` API：

```cpp
Result<uint32_t> JobObjectImpl::QueryProcessExitCode(uint32_t pid) const {
    HANDLE hProcess = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!hProcess) {
        DWORD err = GetLastError();
        return Result<uint32_t>::Err(
            ErrorCode::JobQueryFailed,
            std::format("OpenProcess failed: pid={} err={}", pid, err));
    }

    DWORD exit_code = 0;
    if (!GetExitCodeProcess(hProcess, &exit_code)) {
        DWORD err = GetLastError();
        CloseHandle(hProcess);
        return Result<uint32_t>::Err(
            ErrorCode::JobQueryFailed,
            std::format("GetExitCodeProcess failed: pid={} err={}", pid, err));
    }

    CloseHandle(hProcess);
    return Result<uint32_t>::Ok(exit_code);
}
```

#### 4.3.3 进程路径获取实现

在 IOCP 通知线程中，收到 `NEW_PROCESS` 消息时查询进程路径：

```cpp
void JobObjectImpl::IocpLoop() {
    // ... 现有代码 ...

    if (message == JOB_OBJECT_MSG_NEW_PROCESS) {
        JobNotification notif = TranslateMessage(message, pid);
        
        // 新增：查询进程路径
        auto path_result = QueryProcessPath(pid);
        if (path_result) {
            notif.process_path = *path_result;
            notif.process_name = ExtractFileName(*path_result);
        }

        // 投递通知
        std::lock_guard<std::mutex> lock(sink_mutex_);
        if (sink_) {
            sink_->OnNotification(notif);
        }
    }
}

Result<std::string> JobObjectImpl::QueryProcessPath(uint32_t pid) const {
    HANDLE hProcess = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!hProcess) {
        return Result<std::string>::Err(ErrorCode::JobQueryFailed, "OpenProcess failed");
    }

    wchar_t buffer[MAX_PATH];
    DWORD size = MAX_PATH;
    if (!QueryFullProcessImageNameW(hProcess, 0, buffer, &size)) {
        DWORD err = GetLastError();
        CloseHandle(hProcess);
        return Result<std::string>::Err(
            ErrorCode::JobQueryFailed,
            std::format("QueryFullProcessImageNameW failed: err={}", err));
    }

    CloseHandle(hProcess);
    
    // 转换为 UTF-8
    int utf8_size = WideCharToMultiByte(CP_UTF8, 0, buffer, -1, nullptr, 0, nullptr, nullptr);
    std::string utf8_path(utf8_size, 0);
    WideCharToMultiByte(CP_UTF8, 0, buffer, -1, &utf8_path[0], utf8_size, nullptr, nullptr);
    
    return Result<std::string>::Ok(utf8_path);
}
```

#### 4.3.4 崩溃静默实现

设置 `JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION` 标志：

```cpp
Result<void> JobObjectImpl::SetCrashSilent(bool silent) {
    if (!job_handle_) {
        return Result<void>::Err(ErrorCode::InternalError, "Job not created");
    }

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION ext = {};
    
    // 先查询当前设置
    DWORD return_length = 0;
    QueryInformationJobObject(job_handle_.get(),
                              JobObjectExtendedLimitInformation,
                              &ext, sizeof(ext), &return_length);

    if (silent) {
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
    } else {
        ext.BasicLimitInformation.LimitFlags &= ~JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
    }

    if (!SetInformationJobObject(job_handle_.get(),
                                 JobObjectExtendedLimitInformation,
                                 &ext, sizeof(ext))) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobSetLimitFailed,
            std::format("SetInformationJobObject(DieOnUnhandledException) failed: err={}", err));
    }

    logger_->Log(LogLevel::Info,
                 std::format("crash silent mode: {}", silent ? "enabled" : "disabled"));
    return Result<void>::Ok();
}
```

#### 4.3.5 通知类型区分

修改 `TranslateMessage` 方法，区分正常退出和异常退出：

```cpp
JobNotification JobObjectImpl::TranslateMessage(DWORD message, DWORD pid) {
    JobNotification notif;
    notif.pid = pid;
    notif.timestamp_ms = NowUnixMs();

    switch (message) {
        case JOB_OBJECT_MSG_EXIT_PROCESS:
            // 查询退出码判断是否为异常退出
            auto exit_code_result = QueryProcessExitCode(pid);
            if (exit_code_result && *exit_code_result != 0) {
                notif.type = JobNotificationType::ProcessExitAbnormal;
                notif.exit_code = *exit_code_result;
            } else {
                notif.type = JobNotificationType::ProcessExitNormal;
                if (exit_code_result) {
                    notif.exit_code = *exit_code_result;
                }
            }
            break;
            
        case JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS:
            notif.type = JobNotificationType::ProcessExitAbnormal;
            auto exit_code_result = QueryProcessExitCode(pid);
            if (exit_code_result) {
                notif.exit_code = *exit_code_result;
            }
            break;

        // 其他消息类型保持不变...
        default:
            notif.type = JobNotificationType::Unknown;
            break;
    }

    return notif;
}
```

### 4.4 IPC 暴露策略

**原则**: 按需通过 IPC 暴露功能，避免过度暴露

**建议暴露的功能**:
- `QueryProcessList()` - 通过新增 `QUERY_PROCESS_LIST` 命令暴露
- `SetCrashSilent()` - 通过配置文件或 `START_PROCESS` 的 `isolation_policy` 暴露

**不建议暴露的功能**:
- `QueryProcessExitCode()` - 退出码已通过 `PROCESS_EXITED` 事件暴露
- 进程路径信息 - 已通过 `PROCESS_STARTED`/`BEHAVIOR_LOG` 事件暴露

---

## 5. 性能优化考虑

### 5.1 进程路径查询优化

**策略**: 仅在 `NEW_PROCESS` 通知时查询一次

**理由**:
- 进程路径在进程生命周期内不变
- 避免重复查询带来的性能开销
- IOCP 线程中查询，不影响主线程

### 5.2 进程退出码查询优化

**策略**: 仅在 TranslateMessage 中查询一次

**理由**:
- 退出码在进程退出后不变
- 避免在通知消费方重复查询

### 5.3 进程列表查询优化

**策略**: 按需查询，不缓存

**理由**:
- 进程列表动态变化，缓存容易过期
- 调用频率不高（运维场景按需查询）
- 查询开销可接受（约 1-2ms）

---

## 6. 风险与挑战

### 6.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 进程路径查询失败 | 通知信息不完整 | 失败时记录警告，不阻塞通知投递 |
| 进程退出码查询失败 | 无法区分正常/异常退出 | 退化为统一 ProcessExit，保持兼容 |
| DIE_ON_UNHANDLED_EXCEPTION 不生效 | 崩溃仍弹对话框 | 验证系统版本支持，降级时记录警告 |
| 性能影响 | IOCP 线程阻塞 | 异步查询，设置超时 |
| 权限不足 | 查询失败 | 自适应降级，记录 CapabilityReport |

### 6.2 兼容性风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 现有沙箱功能受影响 | 资源限制功能异常 | 新增功能独立实现，不修改现有逻辑 |
| 通知接口变更 | 现有消费者失效 | 保持向后兼容，新增字段可选 |
| 嵌套 Job 限制 | 沙箱内无法创建 Job | 检测父进程是否在 Job 中，记录警告 |

### 6.3 测试挑战

| 挑战 | 说明 | 解决方案 |
|------|------|----------|
| 崩溃场景测试 | 需要真实触发进程崩溃 | 编写测试程序主动崩溃 |
| 进程路径查询 | 需要验证路径准确性 | 对比多个进程的路径查询结果 |
| 性能影响评估 | 需要量化性能开销 | 压力测试对比增强前后性能 |

---

## 7. 实施建议

### 7.1 实施顺序

1. **Phase 1**: 接口扩展与进程列表查询
2. **Phase 2**: 进程退出码查询与通知类型区分
3. **Phase 3**: 进程路径获取
4. **Phase 4**: 崩溃静默功能
5. **Phase 5**: IPC 协议扩展（按需）
6. **Phase 6**: 集成测试与优化

### 7.2 测试策略

**单元测试**:
- `QueryProcessList()` - 测试空 Job、单进程、多进程场景
- `QueryProcessExitCode()` - 测试正常退出、异常退出、进程不存在场景
- `QueryProcessPath()` - 测试系统进程、用户进程场景
- `SetCrashSilent()` - 测试标志设置与生效

**集成测试**:
- 模拟运维监控场景（进程列表查询）
- 模拟崩溃检测场景（通知类型区分）
- 模拟自动化场景（崩溃静默）

**E2E 测试**:
- 完整的沙箱功能验证
- 验证所有增强功能端到端正常工作

### 7.3 文档更新

需要更新的文档：
- `docs/LLD-01-JobObject-202607261600.md` - 更新接口定义
- `docs/API_REFERENCE.md` - 更新 IPC 消息格式（如需暴露）
- `docs/USER_GUIDE.md` - 更新配置说明（如 `crash_silent` 配置项）

---

## 8. 总结

### 8.1 核心结论

win-sandbox 当前的 Job 实现在资源限制方面已经比较完善，但在进程管理的细粒度能力上存在不足。主要缺失：

1. **进程查询能力**: 缺少进程列表和退出码查询
2. **通知信息**: 缺少进程路径和区分正常/异常退出
3. **崩溃静默**: 缺少 DIE_ON_UNHANDLED_EXCEPTION 支持

### 8.2 增强必要性

这些功能缺失会影响 win-sandbox 在运维监控、自动化测试、安全审计等场景的使用。补充这些功能可以显著提升 win-sandbox 的实用性和完整性。

### 8.3 实施可行性

所有缺失功能均可通过标准 Win32 API 实现，技术风险可控。实施需要遵循 win-sandbox 的干净架构原则，确保向后兼容。

### 8.4 后续工作

1. 编写详细的 Phase 实施文档
2. 按照实施顺序逐步实现功能
3. 编写完整的测试用例
4. 更新相关文档
5. 评估 IPC 暴露需求（按需）

---

## 附录

### A. Win32 API 参考

| API | 用途 | 备注 |
|-----|------|------|
| `QueryInformationJobObject` | 查询 Job 信息 | 使用 `JobObjectBasicProcessIdList` 获取进程列表 |
| `OpenProcess` | 打开进程句柄 | 使用 `PROCESS_QUERY_LIMITED_INFORMATION` 权限 |
| `GetExitCodeProcess` | 查询进程退出码 | 返回 STILL_ACTIVE 表示进程仍在运行 |
| `QueryFullProcessImageNameW` | 查询进程路径 | Win Vista+ 支持 |
| `SetInformationJobObject` | 设置 Job 信息 | 使用 `JobObjectExtendedLimitInformation` 设置崩溃静默 |

### B. 相关文件清单

**win-sandbox**:
- `src/core/ports/IJobObject.hpp` - Job 端口接口
- `src/core/entities/JobNotification.hpp` - 通知实体
- `src/infra/job/JobObjectImpl.hpp` - Job 实现
- `src/infra/job/JobObjectImpl.cpp` - Job 实现

### C. 参考资料

- Microsoft Docs: Job Objects
- Microsoft Docs: JOBOBJECT_BASIC_PROCESS_ID_LIST
- Microsoft Docs: QueryFullProcessImageNameW
- win-sandbox HLD 文档
- win-sandbox LLD-01 文档
