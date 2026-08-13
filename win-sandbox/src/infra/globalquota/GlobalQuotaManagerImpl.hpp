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
    // 共享内存状态结构（跨进程共享，必须 POD）
    struct SharedState {
        uint32_t magic;
        uint32_t max_cpu_rate_percent;
        uint64_t max_memory_mb;
        uint32_t max_processes;
        uint32_t active_instances;
        uint64_t used_memory_mb;
        uint32_t used_cpu_rate;
        uint32_t active_processes;
    };

    std::shared_ptr<ILogger> logger_;
    mutable std::mutex mutex_;

    bool registered_ = false;
    std::string pool_name_;
    GlobalQuotaConfig config_;

    // Win32 句柄（以 void* 形式存储，避免头文件依赖 windows.h）
    void* mapping_handle_ = nullptr;
    void* mutex_handle_ = nullptr;
    void* shared_base_ = nullptr;
};

} // namespace winsandbox
