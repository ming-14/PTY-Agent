// =============================================================================
// IJobNotificationSink - Job 通知回调端口（core 层）
//
// 实现方（如 StartProcessUseCase、StatsCollector）注册到 IJobObject，
// 由 JobObjectImpl 的 IOCP 线程在 Job 事件发生时回调。
//
// 线程安全约定：
//   - OnNotification 由 IOCP 线程调用，可能并发
//   - 实现方需自行加锁保护内部状态
//   - 回调内禁止阻塞（IOCP 线程阻塞会延迟后续通知）
// =============================================================================
#pragma once

#include "core/entities/JobNotification.hpp"

namespace winsandbox {

class IJobNotificationSink {
public:
    virtual ~IJobNotificationSink() = default;
    virtual void OnNotification(const JobNotification&) = 0;
};

} // namespace winsandbox
