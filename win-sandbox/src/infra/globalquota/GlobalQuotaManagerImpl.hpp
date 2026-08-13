// =============================================================================
// GlobalQuotaManagerImpl - 全局资源配额实现（infra 层）
//
// 通过命名共享内存 + Mutex 实现跨进程全局配额池。
// 详情见 .cpp 头部注释。
// =============================================================================
#pragma once

#include "core/entities/GlobalQuota.hpp"
#include "core/entities/Result.hpp"
#include "core/ports/IGlobalQuotaManager.hpp"
#include "core/ports/ILogger.hpp"

#include <atomic>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>

namespace winsandbox {

class GlobalQuotaManagerImpl : public IGlobalQuotaManager {
public:
    explicit GlobalQuotaManagerImpl(std::shared_ptr<ILogger> logger);
    ~GlobalQuotaManagerImpl() override;

    GlobalQuotaManagerImpl(const GlobalQuotaManagerImpl&) = delete;
    GlobalQuotaManagerImpl& operator=(const GlobalQuotaManagerImpl&) = delete;

    // ---- IGlobalQuotaManager 实现 ----
    Result<void> Register(const GlobalQuotaConfig& config) override;
    Result<void> Unregister() override;
    Result<void> Acquire(uint64_t memory_mb, uint32_t cpu_rate_percent,
                         uint32_t process_count) override;
    Result<void> Release(uint64_t memory_mb, uint32_t cpu_rate_percent,
                         uint32_t process_count) override;
    Result<GlobalQuotaUsage> Query() const override;

private:
    // 实例槽位上限（超出拒绝注册，防御池被恶意撑爆）
    static constexpr uint32_t kMaxInstanceSlots = 32;

    // 实例台账槽位（跨进程共享，必须 POD）：记录每个活跃实例的占用与心跳，
    // 宿主崩溃后由其他实例在 Acquire/Query 时回收（防配额永久泄漏）
    struct InstanceSlot {
        uint64_t token = 0;             // 实例令牌（pid<<32 | 序号），0 = 空槽
        uint32_t pid = 0;               // 宿主进程 PID（调试信息）
        uint32_t last_heartbeat_s = 0;  // 最后心跳时间（epoch 秒）
        uint64_t mem_mb = 0;            // 本实例占用内存（MB）
        uint32_t cpu_rate = 0;          // 本实例占用 CPU 速率（%）
        uint32_t process_count = 0;     // 本实例占用进程数
    };

    // 共享内存状态结构（跨进程共享，必须 POD）
    struct SharedState {
        uint32_t magic;
        uint32_t max_cpu_rate_percent;
        uint64_t max_memory_mb;
        uint32_t max_processes;
        uint64_t used_memory_mb;
        uint32_t used_cpu_rate;
        uint32_t active_processes;
        InstanceSlot slots[kMaxInstanceSlots];
    };

    // 扫描并回收心跳超时的陈旧实例槽（必须在命名互斥锁内调用）
    void ReclaimStaleSlotsLocked(SharedState* state) const;

    // 释放共享内存句柄（不加内部锁，供 Register 错误路径在持 mutex_ 时调用）
    void CleanupHandlesLocked();

    std::shared_ptr<ILogger> logger_;
    mutable std::mutex mutex_;

    bool registered_ = false;
    std::string pool_name_;
    GlobalQuotaConfig config_;

    // 本实例槽位信息（registered_ 为 true 时有效）
    uint64_t own_token_ = 0;
    uint32_t own_slot_ = 0;

    // Win32 句柄（以 void* 形式存储，避免头文件依赖 windows.h）
    void* mapping_handle_ = nullptr;
    void* mutex_handle_ = nullptr;
    void* shared_base_ = nullptr;
};

} // namespace winsandbox
