// =============================================================================
// SandboxedProcess - 被隔离进程聚合（core 层）
//
// 描述一个由沙箱启动并托管于 Job Object 的进程的领域视图。
// 仅保存领域相关字段：pid、命令行、工作目录、生命周期时间戳、退出码、状态。
// 句柄等 Win32 资源由 ProcessLauncherImpl 在 infra 层持有，不暴露给 core。
// =============================================================================
#pragma once

#include "core/entities/JobAccountingInfo.hpp"

#include <cstdint>
#include <optional>
#include <string>

namespace winsandbox {

// 进程生命周期状态
enum class ProcessState {
    Pending,      // 已构造 LaunchRequest，尚未 CreateProcess
    Running,      // 已启动，未退出
    Exited,       // 自然退出（含 0 退出码）
    Terminated,   // 被沙箱强制终止（超时/超内存/手动 kill）
};

// 进程退出原因（用于事件上报与日志分类）
enum class ExitReason {
    NormalExit,            // 进程自行 return/exit
    Crashed,               // 进程未处理异常崩溃（NTSTATUS 异常码，如 0xC0000005）
    KilledByCpuLimit,      // CPU 时间超限被杀（Job END_OF_JOB_TIME / END_OF_PROCESS_TIME）
    KilledByMemoryLimit,   // 内存超限被杀（Job PROCESS_MEMORY_LIMIT / JOB_MEMORY_LIMIT）
    KilledByProcessLimit,  // 进程数超限被杀（Job ACTIVE_PROCESS_LIMIT）
    KilledByTimeout,       // wall_clock 超时被沙箱杀
    KilledByUser,          // Python 主动调用 TerminateProcess
    PipeClosed,            // stdio 管道断开
    Unknown,
};

// 退出原因 → 文档约定字符串（API_REFERENCE 6.4 process_exited.reason）
// 语义：
//   - 崩溃（未处理异常 NTSTATUS）→ "crash"
//   - CPU 超限  → "cpu_limit"（原笼统 "resource_limit" 无法区分）
//   - 内存超限  → "memory_limit"
//   - 进程数超限→ "process_count_limit"
//   - wall_clock 超时 → "wall_clock_timeout"
inline std::string ExitReasonToString(ExitReason reason) {
    switch (reason) {
        case ExitReason::NormalExit:            return "normal";
        case ExitReason::Crashed:               return "crash";
        case ExitReason::KilledByCpuLimit:      return "cpu_limit";
        case ExitReason::KilledByMemoryLimit:   return "memory_limit";
        case ExitReason::KilledByProcessLimit:  return "process_count_limit";
        case ExitReason::KilledByTimeout:       return "wall_clock_timeout";
        case ExitReason::KilledByUser:          return "killed_by_user";
        case ExitReason::PipeClosed:            return "pipe_closed";
        case ExitReason::Unknown:               return "unknown";
    }
    return "unknown";
}

struct SandboxedProcess {
    uint32_t process_id = 0;                // 沙箱内部进程 ID（自增分配，稳定，不复用）
    uint32_t pid = 0;                       // OS 进程 PID（可能被复用，仅作事件路由辅助）
    std::string command_line;               // 完整命令行（含可执行路径）
    std::string working_dir;                // 工作目录
    std::string request_id;                 // 关联的 StartProcess 请求 ID

    uint64_t start_time_ms = 0;             // 启动时间（Unix ms）
    uint64_t exit_time_ms = 0;              // 退出时间（Unix ms，0 表示未退出）

    int32_t exit_code = 0;                  // 进程退出码
    ProcessState state = ProcessState::Pending;
    ExitReason exit_reason = ExitReason::Unknown;

    // 退出时从 Job Object 采集的资源使用统计（可选）
    std::optional<JobAccountingInfo> resource_usage;
};

} // namespace winsandbox
