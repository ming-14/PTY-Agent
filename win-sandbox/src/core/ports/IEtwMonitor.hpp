// =============================================================================
// IEtwMonitor - ETW 行为监控端口（core 层）
//
// 定义 ETW 监控的生命周期接口：Start → 事件回调 → Stop。
// 实现位于 infra/etw/EtwMonitorImpl。
//
// 权限降级：
//   - 管理员：3 个内核 session（进程/文件/网络）
//   - 非管理员：仅用户态 provider（进程事件），文件/注册表/网络降级
//
// 线程模型：
//   - Start 非阻塞，内部启动消费线程
//   - 事件通过 callback 投递（多线程调用，调用方需保证线程安全）
//   - Stop 同步等待所有线程退出
// =============================================================================
#pragma once

#include "core/entities/BehaviorEvent.hpp"
#include "core/entities/EtwConfig.hpp"
#include "core/entities/Result.hpp"

#include <functional>
#include <vector>

namespace winsandbox {

// 行为事件回调（批量投递）
// 参数：事件数组
using BehaviorEventCallback = std::function<void(const std::vector<BehaviorEvent>&)>;

class IEtwMonitor {
public:
    virtual ~IEtwMonitor() = default;

    // 启动 ETW 监控
    // config: ETW 配置
    // callback: 事件回调（不可为 nullptr）
    // 失败场景：参数非法 / 已在运行 / session 创建失败
    virtual Result<void> Start(const EtwConfig& config, BehaviorEventCallback callback) = 0;

    // 停止监控（同步等待线程退出）
    virtual Result<void> Stop() = 0;

    // 查询运行状态
    virtual bool IsRunning() const = 0;

    // 查询丢包统计
    virtual uint64_t GetDroppedCount() const = 0;

    // 查询已采集事件总数
    virtual uint64_t GetTotalEventCount() const = 0;

    // 查询序列跳跃事件数（RingBuffer 满丢弃之外的可检测丢包）
    virtual uint64_t GetGapCount() const = 0;
};

} // namespace winsandbox
