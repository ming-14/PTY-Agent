// =============================================================================
// IJobNotificationSink - Job 通知回调端口（core 层）
//
// 实现方（如 NativeSandboxedProcess）注册到 IJobObject，
// 由 JobObjectImpl 的 IOCP 线程在 Job 事件发生时回调。
//
// 线程安全约定：
//   - OnNotification 由 IOCP 线程串行调用（单通知线程，不会并发）
//   - 实现方注册的回调（std::function）由 sink 内部同步后再调用，
//     与 Python 线程的 set/clear 并发安全
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
