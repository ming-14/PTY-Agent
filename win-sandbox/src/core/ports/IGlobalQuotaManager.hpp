// =============================================================================
// IGlobalQuotaManager - 多沙箱全局资源配额端口（core 层）
//
// 抽象跨进程全局配额池。多个沙箱实例进程通过命名共享内存 + Mutex
// 共享同一配额池，实现 CPU/内存/进程数的全局上限。
//
// 典型流程：
//   1. 进程启动时 Register(quota) 登记本实例（首次创建者初始化上限）
//   2. StartProcess 前 Acquire(memory_mb, cpu_rate) 检查并占用额度
//   3. 进程退出后 Release(memory_mb, cpu_rate)
//   4. 实例退出时 Unregister()（末实例释放共享内存）
//   5. 任意时刻 Query() 查看全局使用量
//
// 超限：Acquire 返回 GlobalQuotaExceeded，调用方拒绝启动新进程。
// 实现层保证线程安全（内部 Mutex 串行化所有操作）。
// =============================================================================
#pragma once

#include "core/entities/GlobalQuota.hpp"
#include "core/entities/Result.hpp"

namespace winsandbox {

class IGlobalQuotaManager {
public:
    virtual ~IGlobalQuotaManager() = default;

    // 登记本实例到全局配额池（幂等，重复调用返回 Ok）
    // 首次创建者（共享内存不存在时）初始化配置中的上限
    virtual Result<void> Register(const GlobalQuotaConfig& config) = 0;

    // 注销本实例（末实例释放共享内存池）
    virtual Result<void> Unregister() = 0;

    // 申请资源额度（启动进程前调用）
    // 返回 GlobalQuotaExceeded 表示全局已满，调用方不应启动进程
    virtual Result<void> Acquire(uint64_t memory_mb, uint32_t cpu_rate_percent,
                                 uint32_t process_count) = 0;

    // 释放资源额度（进程退出后调用）
    virtual Result<void> Release(uint64_t memory_mb, uint32_t cpu_rate_percent,
                                 uint32_t process_count) = 0;

    // 查询全局当前使用量
    virtual Result<GlobalQuotaUsage> Query() const = 0;
};

} // namespace winsandbox
