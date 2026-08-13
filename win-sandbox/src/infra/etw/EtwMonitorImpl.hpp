// =============================================================================
// EtwMonitorImpl - ETW 行为监控实现（infra 层）
//
// 权限自适应策略：
//   - 管理员模式：创建 3 个 ETW 内核 session（进程/文件/网络）
//     使用 StartTraceW + EnableTraceEx2 + ProcessTrace
//   - 非管理员模式：降级为"模拟监控"
//     通过 JobObject 通知 + 定时轮询进程列表，生成 ProcessStart/ProcessStop 事件
//     文件/注册表/网络事件不可用
//
// 降级时仍保证：
//   - RingBuffer + Dispatch 线程正常工作
//   - 序号递增 + 丢包检测
//   - BehaviorLog IPC 事件正常发送
//   - CapabilityReport 会标记降级状态
//
// 线程模型：
//   - Start：创建 session + 启动消费线程（每个 session 一个）
//   - 消费线程：ProcessTrace 阻塞 → EventRecordCallback → Push 到 RingBuffer
//   - Dispatch 线程：从 RingBuffer PopBatch → callback
//   - Stop：ControlTrace(STOP) 解除 ProcessTrace 阻塞 → join 所有线程
// =============================================================================
#pragma once

#include "core/entities/BehaviorEvent.hpp"
#include "core/entities/EtwConfig.hpp"
#include "core/entities/Result.hpp"
#include "core/ports/IEtwMonitor.hpp"
#include "core/ports/ILogger.hpp"  // 包含 LogLevel 定义
#include "infra/etw/EventRecordParser.hpp"
#include "infra/etw/RingBuffer.hpp"

#include <atomic>
#include <memory>
#include <thread>
#include <vector>
#include <mutex>
#include <map>
#include <set>

// Windows ETW 头文件
// 注意：winsock2.h 必须最先包含（先于 windows.h），否则 MIB_TCP6*_OWNER_PID 不可见
#include <winsock2.h>
#include <ws2ipdef.h>
#include <initguid.h>
#include <windows.h>
#include <evntrace.h>
#include <tdh.h>
#include <iphlpapi.h>
#include <tcpmib.h>

#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "tdh.lib")
#pragma comment(lib, "iphlpapi.lib")
#pragma comment(lib, "ws2_32.lib")

namespace winsandbox {

class EtwMonitorImpl : public IEtwMonitor {
public:
    EtwMonitorImpl(std::shared_ptr<ILogger> logger);
    ~EtwMonitorImpl() override;

    EtwMonitorImpl(const EtwMonitorImpl&) = delete;
    EtwMonitorImpl& operator=(const EtwMonitorImpl&) = delete;

    // ---- IEtwMonitor 实现 ----
    Result<void> Start(const EtwConfig& config, BehaviorEventCallback callback) override;
    Result<void> Stop() override;
    bool IsRunning() const override { return running_.load(std::memory_order_acquire); }
    uint64_t GetDroppedCount() const override;
    uint64_t GetTotalEventCount() const override { return total_events_.load(std::memory_order_relaxed); }
    uint64_t GetGapCount() const override { return gap_count_.load(std::memory_order_relaxed); }

private:
    // ETW session 内部表示
    struct EtwSession {
        std::string name;
        bool is_kernel = false;
        TRACEHANDLE session_handle = 0;
        TRACEHANDLE consumer_handle = INVALID_PROCESSTRACE_HANDLE;
        std::thread consumer_thread;
        std::vector<EtwProviderConfig> providers;
        uint32_t enable_flags = 0;
    };

    // 启动单个 session
    Result<void> StartSession(EtwSession& session);

    // 停止单个 session
    void StopSession(EtwSession& session);

    // ETW 事件回调（静态，通过 EVENT_TRACE_LOGFILEW.Context → record->UserContext
    // 路由到具体实例，替代进程级静态单例：多沙箱实例共存互不干扰）
    static void WINAPI EventRecordCallback(PEVENT_RECORD record);

    // 处理单个 EventRecord
    void ProcessEventRecord(PEVENT_RECORD record);

    // Dispatch 线程主循环
    void DispatchLoop();

    // 降级模式：模拟进程监控
    void DegradedMonitorLoop();

    // 降级模式：文件系统监控（ReadDirectoryChangesW）
    void DegradedFileMonitorLoop();

    // 降级模式：解析 ReadDirectoryChangesW 的 FILE_NOTIFY_INFORMATION 链
    // base_path: 被监控目录（UTF-8），data/len: notify 缓冲区
    void ParseFileNotifyEvents(const std::string& base_path, const BYTE* data,
                               DWORD len, std::vector<BehaviorEvent>& out);

    // 降级模式：网络连接轮询（GetExtendedTcpTable / GetExtendedUdpTable）
    // 返回新产生的网络事件（TcpConnect/UdpSend），首次调用只建基线
    std::vector<BehaviorEvent> PollNetworkEvents();

    // 线程安全 Push（降级模式下 degraded_thread 与 degraded_file_thread 并发）
    void PushEvent(BehaviorEvent&& ev);

    // 检查是否管理员
    static bool IsElevated();

    std::shared_ptr<ILogger> logger_;
    std::unique_ptr<RingBuffer> ring_buffer_;
    BehaviorEventCallback callback_;
    EventRecordParser parser_;

    // 事件类型过滤（空 = 全部通过）
    std::vector<int> filter_types_;

    // PID 白名单（空 = 不过滤；非空时只处理这些进程的事件，源头减噪）
    std::vector<uint32_t> filter_pids_;

    // Dispatch 批量大小（EtwConfig.dispatch_batch_size，0 = 默认 100）
    uint32_t dispatch_batch_size_ = 0;

    std::atomic<bool> running_{false};
    std::atomic<bool> started_{false};

    // seq 跳跃检测（Dispatch 线程统计：RingBuffer 满之外的丢包信号）
    std::atomic<uint64_t> gap_count_{0};

    std::vector<EtwSession> sessions_;
    std::thread dispatch_thread_;
    std::thread degraded_thread_;
    std::thread degraded_file_thread_;

    // 降级模式：跟踪已知进程
    std::mutex degraded_mutex_;
    std::map<uint32_t, BehaviorEvent> degraded_known_procs_;

    // 降级模式：文件监控目录（来自 EtwConfig.degraded_monitor_dirs）
    std::vector<std::string> degraded_monitor_dirs_;
    // 降级模式：网络轮询开关
    bool degraded_net_polling_ = true;

    // 降级模式：网络连接跟踪（上次快照，用于检测新连接）
    std::mutex degraded_net_mutex_;
    // TCP: key = local_addr:port -> remote_addr:port + pid
    std::set<std::string> degraded_known_tcp_;
    // UDP: key = local addr:port -> remote addr:port + pid
    std::set<std::string> degraded_known_udp_;
    // 网络基线是否已建立（首次轮询只建基线，不产生事件）
    bool degraded_net_baseline_ready_ = false;

    // 降级模式文件监控句柄（用于 Shutdown 时唤醒等待）
    std::atomic<HANDLE> degraded_file_wake_{nullptr};

    // Push 互斥锁：降级模式下多个生产者线程（进程轮询 / 文件监控）并发写 RingBuffer
    std::mutex push_mutex_;

    std::atomic<uint64_t> total_events_{0};

    // thread-local 标记：当前线程是否为 NT Kernel Logger consumer
    static thread_local bool tl_is_kernel_consumer_;
};

} // namespace winsandbox
