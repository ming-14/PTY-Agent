// =============================================================================
// NativeSandboxInstance 实现（pybind11/native 形态）
//
// per-process Job 模式 + 资源管理，
// 用 NativeSandboxedProcess 启动隔离进程，无 IEventEmitter / StatsCollector。
// =============================================================================
#include "adapters/NativeSandboxInstance.hpp"

#include "infra/etw/EtwMonitorImpl.hpp"
#include "infra/job/JobObjectImpl.hpp"
#include "infra/process/ProcessLauncherImpl.hpp"
#include "infra/token/TokenIsolatorImpl.hpp"
#include "infra/wfp/WfpEngineImpl.hpp"
#include "infra/writearea/WriteAreaImpl.hpp"

#include <format>
#include <string_view>
#include <utility>

namespace winsandbox {

namespace {

// 命令行脱敏
std::string RedactCommandLine(std::string_view cmdline) {
    if (cmdline.empty()) {
        return "(empty)";
    }
    size_t start = 0;
    while (start < cmdline.size() && (cmdline[start] == ' ' || cmdline[start] == '\t')) {
        ++start;
    }
    if (start >= cmdline.size()) {
        return "(empty)";
    }
    if (cmdline[start] == '"') {
        size_t end = cmdline.find('"', start + 1);
        if (end == std::string_view::npos) {
            return std::string(cmdline);
        }
        return std::string(cmdline.substr(start, end - start + 1));
    }
    size_t end = cmdline.find_first_of(" \t", start);
    if (end == std::string_view::npos) {
        return std::string(cmdline);
    }
    return std::string(cmdline.substr(start, end - start));
}

// BehaviorEventType → 字符串（ETW 回调 payload 映射用）
std::string BehaviorTypeToString(BehaviorEventType type) {
    switch (type) {
        case BehaviorEventType::ProcessStart:    return "process_start";
        case BehaviorEventType::ProcessStop:     return "process_stop";
        case BehaviorEventType::FileCreate:      return "file_access";
        case BehaviorEventType::FileWrite:       return "file_access";
        case BehaviorEventType::FileDelete:      return "file_access";
        case BehaviorEventType::RegistrySetKey:  return "registry_access";
        case BehaviorEventType::RegistryCreateKey: return "registry_access";
        case BehaviorEventType::RegistryDeleteKey: return "registry_access";
        case BehaviorEventType::TcpConnect:      return "tcp_connect";
        case BehaviorEventType::UdpSend:         return "udp_send";
        case BehaviorEventType::AccessDenied:    return "access_denied";
        default:                                 return "unknown";
    }
}

// BehaviorEvent → BehaviorEventInfo 转换（ETW 回调路由用）
BehaviorEventInfo ToBehaviorEventInfo(const BehaviorEvent& ev, bool degraded) {
    BehaviorEventInfo info;
    info.event_type = BehaviorTypeToString(ev.type);
    info.pid = ev.pid;
    // 路径：优先 file_path，其次 key_path，其次 remote_addr
    if (!ev.file_path.empty()) {
        info.path = ev.file_path;
    } else if (!ev.key_path.empty()) {
        info.path = ev.key_path;
    } else if (!ev.remote_addr.empty()) {
        info.path = ev.remote_addr;
    } else if (!ev.image_path.empty()) {
        info.path = ev.image_path;
    }
    info.operation = ev.operation;
    info.status = (ev.type == BehaviorEventType::AccessDenied) ? "access_denied" : "success";
    info.timestamp_ms = ev.timestamp_ms;
    info.source = degraded ? "degraded" : "etw";
    return info;
}

} // namespace

NativeSandboxInstance::NativeSandboxInstance(std::shared_ptr<ILogger> logger,
                                             ISilo* silo,
                                             IGlobalQuotaManager* global_quota,
                                             const MonitoringConfig& monitoring)
    : logger_(std::move(logger))
    , silo_(silo)
    , global_quota_(global_quota)
    , monitoring_config_(monitoring) {
    // ETW 行为监控（可选）
    if (monitoring_config_.etw_enabled) {
        etw_monitor_ = std::make_unique<EtwMonitorImpl>(logger_);
        logger_->Log(LogLevel::Info, "ETW monitor created (enabled in config)");
    }
}

NativeSandboxInstance::~NativeSandboxInstance() {
    ShutdownAll();
}

uint32_t NativeSandboxInstance::AllocateProcessId() {
    return next_process_id_.fetch_add(1);
}

// =============================================================================
// StartProcess - 启动新隔离进程
//
// 流程：
//   1. 分配 process_id
//   2. 创建 per-process Job/Launcher/TokenIsolator/WriteArea/NativeSandboxedProcess
//   3. Job->Create + SetResourceLimits + RegisterNotificationSink
//   4. [可选] Silo ElevateJob / GlobalQuota Acquire
//   5. usecase->Execute → 拿到 NativeExecuteResult（含句柄）
//   6. 插入 processes_ map，返回 NativeProcessHandle
// =============================================================================
Result<NativeProcessHandle> NativeSandboxInstance::StartProcess(StartProcessRequest req) {
    // 入口自动清理已退出进程（防 processes_ 无限增长 / 配额永久占用）
    CleanupFinished();

    uint32_t process_id = AllocateProcessId();
    req.process_id = process_id;

    logger_->Log(LogLevel::Info,
                 std::format("NativeSandboxInstance::StartProcess: process_id={} cmd={}",
                             process_id, RedactCommandLine(req.command_line)));
    logger_->Log(LogLevel::Debug,
                 std::format("NativeSandboxInstance::StartProcess (full): process_id={} cmd={}",
                             process_id, req.command_line));

    // 1. 创建 per-process 资源（TokenIsolator + WriteArea）
    NativeProcessEntry entry;
    entry.job = std::make_shared<JobObjectImpl>(logger_);
    entry.launcher = std::make_shared<ProcessLauncherImpl>(logger_);
    entry.token_isolator = std::make_shared<TokenIsolatorImpl>(logger_);
    entry.write_area = std::make_shared<WriteAreaImpl>(logger_);
    if (req.isolation_policy.net_policy == NetworkPolicy::Allowlist) {
        entry.wfp_engine = std::make_shared<WfpEngineImpl>(logger_);
    }
    entry.usecase = std::make_shared<NativeSandboxedProcess>(
        logger_, entry.job, entry.launcher,
        entry.token_isolator, entry.write_area, entry.wfp_engine);

    // 2. 创建 Job Object
    auto create_r = entry.job->Create();
    if (!create_r) {
        logger_->Log(LogLevel::Error,
                     std::format("Job Create failed: process_id={} [{}] {}",
                                 process_id, static_cast<int>(create_r.Code()),
                                 create_r.Message()));
        return Result<NativeProcessHandle>::Err(create_r.Code(), create_r.Message());
    }

    // 3. 全局配额（可选）
    if (global_quota_) {
        uint64_t mem = req.quota.memory_mb.value_or(0);
        uint32_t cpu = req.quota.cpu_rate_percent.value_or(0);
        uint32_t proc = 1;
        auto gq_r = global_quota_->Acquire(mem, cpu, proc);
        if (!gq_r) {
            logger_->Log(LogLevel::Warn,
                         std::format("GlobalQuota Acquire failed: process_id={} [{}] {}",
                                     process_id, static_cast<int>(gq_r.Code()),
                                     gq_r.Message()));
            return Result<NativeProcessHandle>::Err(gq_r.Code(), gq_r.Message());
        }
        entry.quota_memory_mb = mem;
        entry.quota_cpu_rate = cpu;
        entry.quota_process_count = proc;
        entry.quota_acquired = true;
        logger_->Log(LogLevel::Info,
                     std::format("GlobalQuota acquired: process_id={} mem={} cpu={} proc={}",
                                 process_id, mem, cpu, proc));
    }

    // 失败清理：Acquire 成功后任何失败路径必须释放配额（防额度永久占用）
    auto release_quota_on_failure = [&]() {
        if (entry.quota_acquired) {
            ReleaseQuota(entry);
        }
    };

    // 4. Server Silo（可选）
    if (silo_ && silo_->IsAvailable()) {
        auto silo_r = silo_->ElevateJob(entry.job->GetHandle());
        if (!silo_r) {
            logger_->Log(LogLevel::Warn,
                         std::format("Silo ElevateJob failed (degraded to Job+TokenIsolator): "
                                     "process_id={} [{}] {}",
                                     process_id, static_cast<int>(silo_r.Code()),
                                     silo_r.Message()));
        }
    }

    // 5. 设置 Job 级资源限制
    auto quota_r = entry.job->SetResourceLimits(req.quota);
    if (!quota_r) {
        logger_->Log(LogLevel::Error,
                     std::format("Job SetResourceLimits failed: process_id={} [{}] {}",
                                 process_id, static_cast<int>(quota_r.Code()),
                                 quota_r.Message()));
        release_quota_on_failure();
        return Result<NativeProcessHandle>::Err(quota_r.Code(), quota_r.Message());
    }

    // 5a. 剪贴板隔离（clipboard_isolate=true → Job UI 限制）
    if (req.isolation_policy.clipboard_isolate) {
        auto ui_r = entry.job->SetUiLimits(true);
        if (!ui_r) {
            logger_->Log(LogLevel::Error,
                         std::format("Job SetUiLimits failed: process_id={} [{}] {}",
                                     process_id, static_cast<int>(ui_r.Code()),
                                     ui_r.Message()));
            release_quota_on_failure();
            return Result<NativeProcessHandle>::Err(ui_r.Code(), ui_r.Message());
        }
        logger_->Log(LogLevel::Info,
                     std::format("clipboard isolation enabled (Job UI limits): process_id={}",
                                 process_id));
    }

    // 6. 注册 usecase 为 Job 通知 sink
    auto reg_r = entry.job->RegisterNotificationSink(*entry.usecase);
    if (!reg_r) {
        logger_->Log(LogLevel::Error,
                     std::format("Job RegisterNotificationSink failed: process_id={} [{}] {}",
                                 process_id, static_cast<int>(reg_r.Code()),
                                 reg_r.Message()));
        release_quota_on_failure();
        return Result<NativeProcessHandle>::Err(reg_r.Code(), reg_r.Message());
    }

    // 7. Execute（返回句柄）
    auto exec_r = entry.usecase->Execute(req);
    if (!exec_r) {
        logger_->Log(LogLevel::Error,
                     std::format("Execute failed: process_id={} [{}] {}",
                                 process_id, static_cast<int>(exec_r.Code()),
                                 exec_r.Message()));
        // Execute 失败后先停止通知线程再析构
        if (entry.job) {
            entry.job->Shutdown();
        }
        release_quota_on_failure();
        return Result<NativeProcessHandle>::Err(exec_r.Code(), exec_r.Message());
    }

    // 8. 成功 → 插入 map（锁内二次校验 shutdown 状态，防 TOCTOU）
    {
        std::unique_lock lock(mutex_);
        if (shutting_down_.load(std::memory_order_acquire)) {
            logger_->Log(LogLevel::Warn,
                         std::format("StartProcess rejected during shutdown: process_id={}",
                                     process_id));
            if (!entry.usecase->IsFinished()) {
                entry.usecase->Terminate(1);
            }
            if (entry.job) {
                entry.job->Shutdown();
            }
            entry.usecase->Close();
            release_quota_on_failure();
            return Result<NativeProcessHandle>::Err(
                ErrorCode::InvalidArgument,
                "start_process rejected: sandbox is shutting down");
        }
        processes_.emplace(process_id, std::move(entry));
    }

    logger_->Log(LogLevel::Info,
                 std::format("NativeSandboxInstance::StartProcess success: process_id={} pid={}",
                             process_id, exec_r.Value().process.pid));

    // ETW 路由注册 + 首次启动
    uint32_t os_pid = exec_r.Value().process.pid;
    if (etw_monitor_) {
        // 注册 pid → usecase 路由
        {
            std::shared_ptr<NativeSandboxedProcess> usecase_shared;
            {
                std::shared_lock lock(mutex_);
                auto it = processes_.find(process_id);
                if (it != processes_.end()) {
                    usecase_shared = it->second.usecase;
                }
            }
            if (usecase_shared) {
                std::lock_guard route_lock(etw_route_mutex_);
                pid_to_usecase_[os_pid] = usecase_shared;
            }
        }

        // 首次 start_process 时启动 ETW monitor
        if (!etw_started_.exchange(true)) {
            auto cb = [this](const std::vector<BehaviorEvent>& events) {
                // ETW 事件线程：按 pid 路由到对应进程的回调
                std::lock_guard route_lock(etw_route_mutex_);
                for (const auto& ev : events) {
                    auto it = pid_to_usecase_.find(ev.pid);
                    if (it == pid_to_usecase_.end()) continue;
                    auto usecase = it->second.lock();
                    if (!usecase) continue;

                    bool degraded = (ev.type == BehaviorEventType::ProcessStart ||
                                     ev.type == BehaviorEventType::ProcessStop);
                    auto info = ToBehaviorEventInfo(ev, degraded);

                    usecase->InvokeBehaviorEvent(info);
                    if (ev.type == BehaviorEventType::AccessDenied) {
                        AccessDeniedInfo ad;
                        ad.pid = ev.pid;
                        ad.path = info.path;
                        ad.operation = info.event_type;
                        ad.source = info.source;
                        ad.timestamp_ms = ev.timestamp_ms;
                        usecase->InvokeAccessDenied(ad);
                    }
                }
            };
            auto etw_r = etw_monitor_->Start(monitoring_config_.etw, cb);
            if (!etw_r) {
                logger_->Log(LogLevel::Warn,
                             std::format("ETW monitor Start failed (degraded): [{}] {}",
                                         static_cast<int>(etw_r.Code()), etw_r.Message()));
            } else {
                logger_->Log(LogLevel::Info, "ETW monitor started");
            }
        }
    }

    // 9. 构造返回值
    NativeProcessHandle handle;
    // 从 map 取回 usecase shared_ptr（entry 已 move 进 map）
    {
        std::shared_lock lock(mutex_);
        auto it = processes_.find(process_id);
        if (it != processes_.end()) {
            handle.usecase = it->second.usecase;
        }
    }
    handle.exec_result = std::move(exec_r.Value());
    handle.process_id = process_id;
    return Result<NativeProcessHandle>::Ok(std::move(handle));
}

// =============================================================================
// 路由方法（按 process_id 找 usecase）
// 锁内拷贝 shared_ptr（防 CleanupFinished/ShutdownAll 并发擦除后 UAF），锁外调用
// =============================================================================
std::shared_ptr<NativeSandboxedProcess> NativeSandboxInstance::FindByProcessId(
    uint32_t process_id) const {
    std::shared_lock lock(mutex_);
    auto it = processes_.find(process_id);
    if (it == processes_.end()) {
        return nullptr;
    }
    return it->second.usecase;
}

Result<void> NativeSandboxInstance::WriteStdin(uint32_t process_id,
                                               const void* data,
                                               size_t size) {
    auto usecase = FindByProcessId(process_id);
    if (!usecase) {
        return Result<void>::Err(
            ErrorCode::ProcessNotFound,
            std::format("WriteStdin: process_id={} not found", process_id));
    }
    return usecase->WriteStdin(data, size);
}

Result<void> NativeSandboxInstance::SignalProcess(uint32_t process_id, ProcessSignal sig) {
    auto usecase = FindByProcessId(process_id);
    if (!usecase) {
        return Result<void>::Err(
            ErrorCode::ProcessNotFound,
            std::format("SignalProcess: process_id={} not found", process_id));
    }
    return usecase->SignalProcess(sig);
}

Result<void> NativeSandboxInstance::TerminateProcess(uint32_t process_id, uint32_t exit_code) {
    auto usecase = FindByProcessId(process_id);
    if (!usecase) {
        return Result<void>::Err(
            ErrorCode::ProcessNotFound,
            std::format("TerminateProcess: process_id={} not found", process_id));
    }
    return usecase->Terminate(exit_code);
}

std::vector<SandboxedProcess> NativeSandboxInstance::ListProcesses() const {
    std::shared_lock lock(mutex_);
    std::vector<SandboxedProcess> result;
    result.reserve(processes_.size());
    for (const auto& [pid, entry] : processes_) {
        if (entry.usecase) {
            result.push_back(entry.usecase->Process());
        }
    }
    return result;
}

Result<std::vector<uint32_t>> NativeSandboxInstance::QueryProcessList(uint32_t process_id) const {
    std::shared_lock lock(mutex_);
    auto it = processes_.find(process_id);
    if (it == processes_.end() || !it->second.job) {
        return Result<std::vector<uint32_t>>::Err(
            ErrorCode::ProcessNotFound,
            std::format("QueryProcessList: process_id={} not found", process_id));
    }
    return it->second.job->QueryProcessList();
}

Result<uint32_t> NativeSandboxInstance::QueryProcessExitCode(uint32_t process_id,
                                                             uint32_t pid) const {
    std::shared_lock lock(mutex_);
    auto it = processes_.find(process_id);
    if (it == processes_.end() || !it->second.job) {
        return Result<uint32_t>::Err(
            ErrorCode::ProcessNotFound,
            std::format("QueryProcessExitCode: process_id={} not found", process_id));
    }
    return it->second.job->QueryProcessExitCode(pid);
}

// =============================================================================
// CleanupFinished - 清理已退出的 usecase
// =============================================================================
void NativeSandboxInstance::CleanupFinished() {
    std::vector<NativeProcessEntry> to_destroy;
    std::vector<uint32_t> finished_pids;
    {
        std::unique_lock lock(mutex_);
        for (auto it = processes_.begin(); it != processes_.end();) {
            if (it->second.usecase && it->second.usecase->IsFinished()) {
                logger_->Log(LogLevel::Debug,
                             std::format("CleanupFinished: removing process_id={}",
                                         it->first));
                // 记录 OS pid，清理 ETW 路由
                if (it->second.usecase) {
                    finished_pids.push_back(it->second.usecase->Process().pid);
                }
                ReleaseQuota(it->second);
                to_destroy.push_back(std::move(it->second));
                it = processes_.erase(it);
            } else {
                ++it;
            }
        }
    }
    // 清理已退出进程的 ETW 路由
    if (!finished_pids.empty()) {
        std::lock_guard route_lock(etw_route_mutex_);
        for (uint32_t pid : finished_pids) {
            pid_to_usecase_.erase(pid);
        }
    }
    // 锁外析构：先停止 IOCP 通知线程（防悬垂 sink）
    for (auto& entry : to_destroy) {
        if (entry.job) {
            entry.job->Shutdown();
        }
    }
    to_destroy.clear();
}

// =============================================================================
// StopEtwMonitor - 仅停止 ETW monitor（不析构 usecase）
//
// shutdown() 三阶段 GIL 管理的第一步。
// 调用方释放 GIL 后调用本方法 → Stop() join ETW 线程 → 线程可获 GIL 完成末次回调。
// 幂等：已停止时 etw_started_ 为 false，直接返回。
// =============================================================================
void NativeSandboxInstance::StopEtwMonitor() {
    if (!etw_monitor_ || !etw_started_.load(std::memory_order_acquire)) {
        return;
    }
    auto r = etw_monitor_->Stop();
    if (!r) {
        logger_->Log(LogLevel::Warn,
                     std::format("ETW monitor Stop failed: [{}] {}",
                                 static_cast<int>(r.Code()), r.Message()));
    } else {
        logger_->Log(LogLevel::Info, "ETW monitor stopped");
    }
    etw_started_.store(false, std::memory_order_release);

    // 清理路由表（ETW 线程已 join，无并发访问）
    std::lock_guard route_lock(etw_route_mutex_);
    pid_to_usecase_.clear();
}

// =============================================================================
// ClearAllCallbacks - 清空所有 usecase 的回调
//
// shutdown() 三阶段 GIL 管理的第二步。
// 调用方必须持有 GIL（std::function 析构销毁 py::function 捕获需要 GIL）。
// 在 StopEtwMonitor 之后调用：ETW 线程已 join，无并发回调。
// 清空的回调：on_behavior_event / on_access_denied / on_resource_limit /
//             on_job_process_started / on_job_process_exited
// =============================================================================
void NativeSandboxInstance::ClearAllCallbacks() {
    std::shared_lock lock(mutex_);
    for (auto& [pid, entry] : processes_) {
        if (entry.usecase) {
            entry.usecase->ClearAllCallbacks();
        }
    }
}

// =============================================================================
// ShutdownAll - 终止所有进程并清理
// =============================================================================
void NativeSandboxInstance::ShutdownAll() {
    std::lock_guard lock(shutdown_mutex_);
    shutting_down_.store(true, std::memory_order_release);

    // 停止 ETW monitor（先停，防回调访问已析构的 usecase；StopEtwMonitor 幂等）
    StopEtwMonitor();

    std::vector<NativeProcessEntry> to_destroy;
    {
        std::unique_lock plock(mutex_);
        to_destroy.reserve(processes_.size());
        for (auto& [pid, entry] : processes_) {
            to_destroy.push_back(std::move(entry));
        }
        processes_.clear();
    }

    // 锁外逐个 Terminate + 析构
    // 资源为 shared_ptr 且被 usecase 持有：即使这里 reset，Python 端 Process
    // 引用存活期间 usecase 及其依赖不会析构（shutdown 后 proc 仍可 wait/close）。
    // 显式 Shutdown/Close 保证进程终止与 IOCP 线程停止及时发生。
    for (auto& entry : to_destroy) {
        if (entry.usecase && !entry.usecase->IsFinished()) {
            logger_->Log(LogLevel::Info,
                         std::format("ShutdownAll: terminating pid={}",
                                     entry.usecase->Process().pid));
            auto r = entry.usecase->Terminate(1);
            if (!r) {
                logger_->Log(LogLevel::Warn,
                             std::format("ShutdownAll: Terminate failed: [{}] {}",
                                         static_cast<int>(r.Code()), r.Message()));
            }
        }
        ReleaseQuota(entry);
        // 先停止 IOCP 通知线程（防 use-after-free）
        if (entry.job) {
            entry.job->Shutdown();
        }
        // usecase->Close() 会调 job->Shutdown()（幂等），这里显式调保证顺序
        if (entry.usecase) {
            entry.usecase->Close();
        }
        entry.usecase.reset();
        entry.wfp_engine.reset();
        entry.write_area.reset();
        entry.token_isolator.reset();
        entry.launcher.reset();
        entry.job.reset();
    }
}

size_t NativeSandboxInstance::ProcessCount() const {
    std::shared_lock lock(mutex_);
    return processes_.size();
}

void NativeSandboxInstance::ReleaseQuota(NativeProcessEntry& entry) {
    if (!global_quota_ || !entry.quota_acquired) {
        return;
    }
    auto r = global_quota_->Release(entry.quota_memory_mb, entry.quota_cpu_rate,
                                    entry.quota_process_count);
    if (!r) {
        logger_->Log(LogLevel::Warn,
                     std::format("GlobalQuota Release failed: [{}] {}",
                                 static_cast<int>(r.Code()), r.Message()));
    } else {
        logger_->Log(LogLevel::Debug,
                     std::format("GlobalQuota released: mem={} cpu={} proc={}",
                                 entry.quota_memory_mb, entry.quota_cpu_rate,
                                 entry.quota_process_count));
    }
    entry.quota_acquired = false;
}

} // namespace winsandbox
