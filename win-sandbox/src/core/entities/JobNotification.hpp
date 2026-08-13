// =============================================================================
// JobNotification - Job 事件通知（core 层）
//
// 由 IOCP 线程从 JOB_OBJECT_MSG_* 翻译而来，投递给 IJobNotificationSink。
// 全部使用可移植类型，不依赖 windows.h。
//
// 消息类型对应表（实现层 JobObjectImpl::TranslateMessage 负责映射）：
//   EndOfJobTime        ← JOB_OBJECT_MSG_END_OF_JOB_TIME
//   EndOfProcessTime    ← JOB_OBJECT_MSG_END_OF_PROCESS_TIME
//   ActiveProcessLimit  ← JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT
//   ProcessMemoryLimit ← JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT
//   JobMemoryLimit     ← JOB_OBJECT_MSG_JOB_MEMORY_LIMIT
//   ProcessExit        ← JOB_OBJECT_MSG_EXIT_PROCESS（保留：退出码查询失败时的兜底）
//   ProcessExitNormal  ← JOB_OBJECT_MSG_EXIT_PROCESS 且退出码 == 0
//   ProcessExitAbnormal← JOB_OBJECT_MSG_EXIT_PROCESS 且退出码 != 0
//   ActiveProcessEmpty ← JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO
//   NewProcess         ← JOB_OBJECT_MSG_NEW_PROCESS（携带进程路径）
//   Unknown            ← 未识别的 JOB_OBJECT_MSG_*（不应发生，记日志后由上层决定忽略/告警）
//
// 扩展字段：
//   - process_name / process_path：NEW_PROCESS 通知时由 IOCP 线程查询填充
//   - exit_code：退出类通知由 TranslateMessage 查询填充
//   - parent_pid：NEW_PROCESS 通知时由 IOCP 线程尽力填充（Toolhelp 快照）；
//     父进程已退出/快照失败时为 nullopt（best-effort，不阻塞通知投递）
// =============================================================================
#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace winsandbox {

enum class JobNotificationType {
    EndOfJobTime,           // Job CPU 时间耗尽
    EndOfProcessTime,       // 单进程 CPU 时间耗尽
    ActiveProcessLimit,     // 进程数超限
    ProcessMemoryLimit,     // 单进程内存超限
    JobMemoryLimit,         // Job 内存超限
    ProcessExit,            // 进程退出（退出码查询失败时的兜底类型）
    ProcessExitNormal,      // 进程正常退出（退出码 == 0）
    ProcessExitAbnormal,    // 进程异常退出（退出码 != 0，含崩溃）
    ActiveProcessEmpty,     // Job 内无进程
    NewProcess,             // 新进程加入 Job
    Unknown,                // 未识别的 Job 消息（默认分支，上层应忽略或告警）
};

struct JobNotification {
    JobNotificationType type = JobNotificationType::Unknown;
    uint32_t pid = 0;              // 相关进程 PID（ProcessExit/NewProcess 等）
    uint64_t timestamp_ms = 0;     // 事件投递时间（Unix ms）

    // ----- 扩展字段 -----
    std::string process_name;                   // 进程名称（如 "cmd.exe"），NEW_PROCESS 填充
    std::string process_path;                   // 进程完整路径，NEW_PROCESS 填充
    std::optional<uint32_t> exit_code;          // 退出码（仅退出类通知有效）

    std::optional<uint32_t> parent_pid;         // 父进程 PID（NEW_PROCESS 尽力填充；父进程已退出则省略）
};

} // namespace winsandbox
