// =============================================================================
// SiloImpl - Server Silo 隔离实现（infra 层）
//
// 通过 ntdll 动态加载方式访问未文档化的 Server Silo API（JobObjectCreateSilo
// 信息类），把现有 Job 就地升级为 Server Silo。平台不支持（Win10 客户端）或
// 非管理员时优雅降级：IsAvailable() 返回 false，ElevateJob() 返回 SiloUnavailable，
// 调用方继续用现有 Job + Low IL 隔离（语义不变）。
//
// 线程安全：
//   - IsAvailable() 内部用 mutex 保护单次探测，结果缓存
//   - ElevateJob() 无共享状态（仅使用加载的函数指针 + 传入句柄）
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"
#include "core/ports/ILogger.hpp"
#include "core/ports/ISilo.hpp"

#include <atomic>
#include <memory>
#include <mutex>

namespace winsandbox {

class SiloImpl : public ISilo {
public:
    explicit SiloImpl(std::shared_ptr<ILogger> logger);
    ~SiloImpl() override;

    SiloImpl(const SiloImpl&) = delete;
    SiloImpl& operator=(const SiloImpl&) = delete;

    // ---- ISilo 实现 ----
    bool IsAvailable() const override;
    Result<void> ElevateJob(void* job_handle) override;

private:
    std::shared_ptr<ILogger> logger_;

    // 单次探测缓存
    mutable std::mutex probe_mutex_;
    mutable std::atomic<bool> probe_done_{false};
    mutable std::atomic<bool> probe_available_{false};
};

} // namespace winsandbox
