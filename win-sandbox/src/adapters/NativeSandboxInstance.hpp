// =============================================================================
// NativeSandboxInstance - 沙箱实例（pybind11/native 形态多进程管理器）
//
// pybind11/native 形态的沙箱实例，per-process Job 模式：
// 用 NativeSandboxedProcess 启动隔离进程，无 IEventEmitter 依赖，
// 无 StatsCollector（Python 端轮询替代）。
//
// 本类是唯一的沙箱实例实现。
//
// StartProcess 返回 NativeProcessHandle（含 usecase shared_ptr + 句柄 + process_id），
// pybind11 ProcessBinding 包装为 Python Process 对象。
//
// usecase 用 shared_ptr（而非 unique_ptr）：Python 端 Process 对象可能独立于
// SandboxInstance 存在（如 sb.shutdown() 后 proc 仍可 wait/close），需共享所有权。
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"
#include "core/entities/SandboxedProcess.hpp"
#include "core/entities/SandboxConfig.hpp"
#include "core/entities/StartProcessRequest.hpp"
#include "core/ports/IGlobalQuotaManager.hpp"
#include "core/ports/ILogger.hpp"
#include "core/ports/ISilo.hpp"
#include "core/usecases/NativeSandboxedProcess.hpp"

#include <atomic>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <unordered_map>
#include <vector>

namespace winsandbox {

// 前向声明（infra 具体类型，NativeSandboxInstance 持有其 unique_ptr）
class JobObjectImpl;
class ProcessLauncherImpl;
class TokenIsolatorImpl;
class WriteAreaImpl;
class WfpEngineImpl;
class EtwMonitorImpl;  // ETW 行为监控

// per-process 资源集合（NativeSandboxedProcess 专用）
//
// 资源用 shared_ptr 并由 usecase 持有副本：Python 端 Process 对象在
// sb.shutdown() 之后仍可 wait/close，usecase 存活期间依赖必存活（防 UAF）；
// 资源实际释放发生在 usecase（及其 Python 引用）全部销毁时。
struct NativeProcessEntry {
    std::shared_ptr<JobObjectImpl> job;
    std::shared_ptr<ProcessLauncherImpl> launcher;
    std::shared_ptr<TokenIsolatorImpl> token_isolator;
    std::shared_ptr<WriteAreaImpl> write_area;
    std::shared_ptr<WfpEngineImpl> wfp_engine;
    std::shared_ptr<NativeSandboxedProcess> usecase;  // shared_ptr：pybind11 Process 持有
    // 全局配额占用记录（Acquire 后记录，退出时 Release）
    uint64_t quota_memory_mb = 0;
    uint32_t quota_cpu_rate = 0;
    uint32_t quota_process_count = 0;
    bool quota_acquired = false;
};

// StartProcess 返回值：pybind11 ProcessBinding 包装为 Python Process 对象
struct NativeProcessHandle {
    std::shared_ptr<NativeSandboxedProcess> usecase;
    NativeExecuteResult exec_result;
    uint32_t process_id = 0;
};

class NativeSandboxInstance {
public:
    // 构造：注入共享依赖
    // silo / global_quota 可选注入，nullptr = 不启用
    // monitoring 配置控制 ETW 行为监控启用
    NativeSandboxInstance(std::shared_ptr<ILogger> logger,
                          ISilo* silo = nullptr,
                          IGlobalQuotaManager* global_quota = nullptr,
                          const MonitoringConfig& monitoring = {});
    ~NativeSandboxInstance();

    NativeSandboxInstance(const NativeSandboxInstance&) = delete;
    NativeSandboxInstance& operator=(const NativeSandboxInstance&) = delete;
    NativeSandboxInstance(NativeSandboxInstance&&) = delete;
    NativeSandboxInstance& operator=(NativeSandboxInstance&&) = delete;

    // 启动新进程：创建 per-process 资源 → Execute → 返回 NativeProcessHandle
    // 成功：返回 handle（含 usecase + 句柄 + process_id），失败：返回 Err
    Result<NativeProcessHandle> StartProcess(StartProcessRequest req);

    // 路由方法（按 process_id 找到 usecase）
    Result<void> WriteStdin(uint32_t process_id, const void* data, size_t size);
    Result<void> SignalProcess(uint32_t process_id, ProcessSignal sig);
    Result<void> TerminateProcess(uint32_t process_id, uint32_t exit_code);

    // 查询方法
    std::vector<SandboxedProcess> ListProcesses() const;
    Result<std::vector<uint32_t>> QueryProcessList(uint32_t process_id) const;
    Result<uint32_t> QueryProcessExitCode(uint32_t process_id, uint32_t pid) const;

    // 清理已退出的 usecase（释放 per-process 资源）
    // 由 StartProcess 入口自动调用 + 绑定为 Python 方法（start_process/cleanup_finished）
    void CleanupFinished();

    // 终止所有进程并清理
    void ShutdownAll();

    // 停止 ETW monitor（仅停止，不析构 usecase）
    // 调用方应在释放 GIL 后调用（Stop 内部 join ETW 线程，线程需获 GIL 完成回调）
    // 幂等：已停止时安全调用
    void StopEtwMonitor();

    // 清空所有 usecase 的回调（on_behavior_event 等）
    // 调用方必须在持有 GIL 时调用（销毁 py::function 捕获需要 GIL）
    // 在 StopEtwMonitor 之后调用：ETW 线程已 join，不会有并发回调访问
    void ClearAllCallbacks();

    // 当前管理的进程数
    size_t ProcessCount() const;

private:
    uint32_t AllocateProcessId();
    // 返回 usecase 的 shared_ptr（锁内拷贝，防调用方持锁外使用裸指针的 UAF）
    std::shared_ptr<NativeSandboxedProcess> FindByProcessId(uint32_t process_id) const;
    void ReleaseQuota(NativeProcessEntry& entry);

    std::shared_ptr<ILogger> logger_;

    ISilo* silo_ = nullptr;
    IGlobalQuotaManager* global_quota_ = nullptr;

    // ETW 行为监控（可选，monitoring.etw_enabled=true 时创建）
    std::unique_ptr<EtwMonitorImpl> etw_monitor_;
    MonitoringConfig monitoring_config_;
    std::atomic<bool> etw_started_{false};

    // OS pid → usecase 映射（ETW 事件按 pid 路由到对应进程的回调）
    // weak_ptr：usecase 生命周期由 processes_ 管理，此处仅路由用
    std::mutex etw_route_mutex_;
    std::unordered_map<uint32_t, std::weak_ptr<NativeSandboxedProcess>> pid_to_usecase_;

    std::atomic<uint32_t> next_process_id_{1};
    std::atomic<bool> shutting_down_{false};
    std::mutex shutdown_mutex_;

    mutable std::shared_mutex mutex_;
    std::unordered_map<uint32_t, NativeProcessEntry> processes_;
};

} // namespace winsandbox
