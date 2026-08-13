// =============================================================================
// GlobalQuota - 多沙箱全局资源配额实体（core 层）
//
// 描述多个沙箱实例（= 多个 SandboxInstance）间共享的资源上限。
// 由 infra/globalquota/GlobalQuotaManagerImpl 通过命名共享内存 + Mutex
// 跨进程维护实际计数。
//
// 多沙箱 = 多进程（每实例独立对象），跨进程全局配额只能通过共享内存 +
// 命名 Mutex 协调，无法用嵌套 Job（跨进程不可共享）。
//
// 字段单位（与 ResourceQuota 一致）：
//   - CPU 速率：百分比（1-100）
//   - 内存：兆字节（MB）
//   - 进程数：个
// =============================================================================
#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace winsandbox {

// 全局配额配置（SandboxConfig.global_quota）
struct GlobalQuotaConfig {
    bool enabled = false;                 // 是否启用全局配额
    std::string pool_name = "win-sandbox-quota";  // 共享内存池名（跨进程唯一）

    std::optional<uint32_t> max_cpu_rate_percent;  // 全局 CPU 速率上限（所有实例合计）
    std::optional<uint64_t> max_memory_mb;         // 全局内存上限（所有实例合计）
    std::optional<uint32_t> max_processes;         // 全局进程数上限（所有实例合计）
};

// 全局配额当前使用量统计（Query 返回）
struct GlobalQuotaUsage {
    uint32_t active_instances = 0;   // 当前活跃 sandbox 实例数
    uint64_t used_memory_mb = 0;     // 当前已占用内存（MB）
    uint32_t active_processes = 0;   // 当前活跃进程数
    uint32_t used_cpu_rate = 0;      // 当前已占用 CPU 速率（%）
};

} // namespace winsandbox
