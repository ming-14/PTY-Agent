// =============================================================================
// Callbacks - pybind11/native 形态回调 payload（core 层值对象）
//
// NativeSandboxedProcess（in-process 形态用例）通过 std::function 回调
// 通知 Python 端 Job 事件。本头文件定义回调 payload 结构体，类似 JobNotification
// 的角色：由 IOCP 线程填充，投递给回调函数。
//
// 本文件是唯一的 Job 事件回调契约。
//
// 线程安全约定：
//   - 回调由 IOCP 线程调用，可能并发
//   - pybind11 绑定层在回调内持 GIL 调 Python callable
//   - 回调内禁止阻塞（IOCP 线程阻塞会延迟后续通知）
//   - 回调内禁止调 C++ 方法（防死锁）
// =============================================================================
#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace winsandbox {

// Job 资源限制命中（CPU/内存/进程数/CPU 超时）
// 对应 resource_limit_hit 事件
struct ResourceLimitInfo {
    std::string type;        // "cpu_limit" / "memory_limit" / "process_count_limit" / "cpu_timeout"
    uint32_t pid = 0;        // 触发限制的进程 PID（best-effort）
    uint64_t timestamp_ms = 0;  // 事件时间（Unix ms）
};

// Job 内子/孙进程创建
// 对应 job_process_started 事件
struct JobProcessStartedInfo {
    uint32_t pid = 0;                    // 新进程 PID
    std::string process_name;            // 进程名称（如 "cl.exe"）
    std::string process_path;            // 进程完整路径
    std::optional<uint32_t> parent_pid;  // 父进程 PID（best-effort，可能省略）
    uint64_t timestamp_ms = 0;           // 事件时间（Unix ms）
};

// Job 内子/孙进程退出
// 对应 job_process_exited 事件
struct JobProcessExitedInfo {
    uint32_t pid = 0;                    // 退出进程 PID
    std::string exit_kind;               // "normal" / "abnormal" / "unknown"
    std::optional<int32_t> exit_code;    // 退出码（exit_kind="unknown" 时省略）
    uint64_t timestamp_ms = 0;           // 事件时间（Unix ms）
};

// -----------------------------------------------------------------------------
// ETW 行为监控回调 payload
// -----------------------------------------------------------------------------

// ETW 行为事件（文件/注册表/进程/网络访问）
// 由 EtwMonitorImpl 事件线程填充，投递给 on_behavior_event 回调
struct BehaviorEventInfo {
    std::string event_type;    // "file_access" / "registry_access" / "process_start" / "process_stop" / "tcp_connect" / "udp_send"
    uint32_t pid = 0;          // 事件相关 PID
    std::string path;          // 事件相关路径（文件/注册表/网络地址）
    std::string operation;     // "read" / "write" / "create" / "delete"
    std::string status;        // "success" / "access_denied"
    uint64_t timestamp_ms = 0; // 事件时间（Unix ms）
    std::string source;        // "etw" / "degraded"（ETW 内核 vs 降级轮询）
};

// AccessDenied 专项事件（ETW 或 stderr 关键字扫描检测到拒绝访问）
struct AccessDeniedInfo {
    uint32_t pid = 0;          // 被拒绝的进程 PID
    std::string path;          // 被拒绝访问的路径
    std::string operation;     // "file_access" / "registry_access"
    std::string source;        // "etw" / "stderr"（ETW vs stderr 关键字扫描）
    uint64_t timestamp_ms = 0; // 事件时间（Unix ms）
};

} // namespace winsandbox
