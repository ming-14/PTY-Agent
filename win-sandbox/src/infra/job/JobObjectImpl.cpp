// =============================================================================
// JobObjectImpl 实现
//
// 封装 Win32 Job Object API，提供资源限制、级联终止、IOCP 通知、会计查询。
//
// 关键 API 映射：
//   Create()              → CreateJobObjectW + SetupIocp
//   SetResourceLimits()   → SetExtendedLimits + SetCpuRateControl + SetIoRateControl + SetUiLimits
//   AssignProcess()       → AssignProcessToJobObject
//   TerminateAll()        → TerminateJobObject
//   TerminateProcess()    → ::TerminateProcess
//   QueryAccounting()     → QueryInformationJobObject(BasicAndIoAccountingInformation)
//   QueryPeakMemory()     → QueryInformationJobObject(ExtendedLimitInformation)
//   IocpLoop()            → GetQueuedCompletionStatus + TranslateMessage
//
// 单位转换：
//   ms  → 100ns：× 10000（1ms = 10^4 × 100ns）
//   MB  → bytes：× 1024 × 1024（二进制 MB / MiB）
//   percent → CpuRate：× 100（CpuRate 单位 0.01%）
// =============================================================================

#include "infra/job/JobObjectImpl.hpp"

#include <spdlog/spdlog.h>

#include <windows.h>
#include <jobapi2.h>  // SetIoRateControlInformationJobObject / JOBOBJECT_IO_RATE_CONTROL_INFORMATION
#include <tlhelp32.h> // CreateToolhelp32Snapshot / Process32FirstW / Process32NextW（parent_pid）

#include <chrono>
#include <format>

namespace winsandbox {

namespace {

// IOCP completion key，用于标识 Job 通知（区分其他 IOCP 来源）
constexpr ULONG_PTR kJobCompletionKey = 0x01;

// ms → 100ns（Win32 FILETIME 单位）
inline ULONGLONG MsTo100ns(uint64_t ms) {
    return static_cast<ULONGLONG>(ms) * 10000ULL;
}

// MB → bytes（二进制 MB）
inline ULONGLONG MbToBytes(uint64_t mb) {
    return static_cast<ULONGLONG>(mb) * 1024ULL * 1024ULL;
}

// 当前 Unix 毫秒时间戳
inline uint64_t NowUnixMs() {
    using namespace std::chrono;
    return static_cast<uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
}

} // namespace

JobObjectImpl::JobObjectImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger)) {}

JobObjectImpl::~JobObjectImpl() {
    StopIocpThread();
    // job_handle_ 与 iocp_handle_ 由 wil::unique_handle RAII 释放
    // JOB_OBJECT_LIMIT_KILL_ON_JOB 保证 Job 句柄关闭时所有进程被终止
    if (job_handle_) {
        logger_->Log(LogLevel::Info, "JobObject destroyed (processes cascade-killed)");
    }
}

// =============================================================================
// Create - 创建 Job Object 并绑定 IOCP
// =============================================================================
Result<void> JobObjectImpl::Create() {
    // 幂等：已创建则直接返回
    if (job_handle_) {
        return Result<void>::Ok();
    }

    // 1. 检测父进程是否已在 Job 中（影响嵌套 Job 能力）
    BOOL parent_in_job = FALSE;
    if (IsProcessInJob(GetCurrentProcess(), nullptr, &parent_in_job)) {
        if (parent_in_job) {
            logger_->Log(LogLevel::Warn,
                         "parent process already in a Job; nested Job capabilities limited");
        }
    } else {
        DWORD err = GetLastError();
        logger_->Log(LogLevel::Warn,
                     std::format("IsProcessInJob failed: err={}", err));
    }

    // 2. 创建 Job Object（暂不命名，避免名称冲突）
    HANDLE raw = CreateJobObjectW(nullptr, nullptr);
    if (!raw || raw == INVALID_HANDLE_VALUE) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobCreateFailed,
            std::format("CreateJobObjectW failed: err={}", err));
    }
    job_handle_.reset(raw);

    logger_->Log(LogLevel::Info, "Job Object created");

    // 3. 创建 IOCP 并绑定到 Job
    auto iocp_result = SetupIocp();
    if (!iocp_result) {
        job_handle_.reset();
        return iocp_result;
    }

    // 4. 启动 IOCP 等待线程
    running_.store(true, std::memory_order_release);
    iocp_thread_ = std::thread(&JobObjectImpl::IocpLoop, this);

    logger_->Log(LogLevel::Info, "IOCP notification thread started");
    return Result<void>::Ok();
}

// =============================================================================
// SetResourceLimits - 综合设置资源限制
// =============================================================================
Result<void> JobObjectImpl::SetResourceLimits(const ResourceQuota& quota) {
    if (!job_handle_) {
        return Result<void>::Err(ErrorCode::InternalError, "Job not created");
    }

    // 1. ExtendedLimits（CPU 时间/内存/进程数/breakaway/KILL_ON_JOB）
    auto ext_result = SetExtendedLimits(quota);
    if (!ext_result) return ext_result;

    // 2. CPU Rate Control（Win8+，自适应降级）
    if (quota.cpu_rate_percent.has_value()) {
        auto r = SetCpuRateControl(*quota.cpu_rate_percent);
        if (!r) return r;
    }

    // 3. IO Rate Control（Win10+ 管理员，自适应降级）
    if (quota.io_rate_bytes_per_sec.has_value() || quota.io_rate_iops.has_value()) {
        auto r = SetIoRateControl(
            quota.io_rate_bytes_per_sec.value_or(0),
            quota.io_rate_iops.value_or(0));
        if (!r) return r;
    }

    // 4. UI 限制（no_ui：窗口/系统级限制，不含剪贴板——
    //    剪贴板由 isolation_policy.clipboard_isolate 单独控制（StartProcess 显式设置），
    //    避免 no_ui 默认值误伤剪贴板可用性）
    if (quota.no_ui) {
        DWORD flags = JOB_OBJECT_UILIMIT_HANDLES           // 禁止访问其他进程窗口句柄
                    | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS  // 禁止改系统参数
                    | JOB_OBJECT_UILIMIT_DISPLAYSETTINGS   // 禁止改显示设置
                    | JOB_OBJECT_UILIMIT_GLOBALATOMS;      // 禁止全局原子表
        auto r = SetUiRestrictions(flags);
        if (!r) return r;
    }

    // 5. 崩溃静默（DIE_ON_UNHANDLED_EXCEPTION）
    if (quota.crash_silent) {
        auto r = SetCrashSilent(true);
        if (!r) return r;
    }

    logger_->Log(LogLevel::Info,
                 std::format("resource limits applied: cpu_ms={} cpu_rate={} mem_mb={} job_mem_mb={} max_proc={} crash_silent={}",
                             quota.cpu_ms.value_or(0),
                             quota.cpu_rate_percent.value_or(0),
                             quota.memory_mb.value_or(0),
                             quota.job_memory_mb.value_or(0),
                             quota.max_processes.value_or(0),
                             quota.crash_silent));
    return Result<void>::Ok();
}

// =============================================================================
// SetExtendedLimits - CPU 时间/内存/进程数/breakaway
// =============================================================================
Result<void> JobObjectImpl::SetExtendedLimits(const ResourceQuota& quota) {
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION ext = {};

    // 始终设置 KILL_ON_JOB_CLOSE：Job 句柄关闭时级联终止所有进程
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

    // CPU 时间限制（Job 总用户态 CPU 时间）
    // cpu_ms 和 cpu_timeout_ms 是同一语义的别名，取先设置的
    if (quota.cpu_ms.has_value() || quota.cpu_timeout_ms.has_value()) {
        // 优先 cpu_ms，缺失则退到 cpu_timeout_ms；两者皆空时不应进入此分支
        uint64_t cpu_val = quota.cpu_ms.value_or(
            quota.cpu_timeout_ms.value_or(0));
        ext.BasicLimitInformation.PerJobUserTimeLimit.QuadPart =
            static_cast<LONGLONG>(MsTo100ns(cpu_val));
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_TIME;
    }

    // 单进程内存限制
    if (quota.memory_mb.has_value()) {
        ext.ProcessMemoryLimit = MbToBytes(*quota.memory_mb);
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY;
    }

    // Job 整体内存限制
    if (quota.job_memory_mb.has_value()) {
        ext.JobMemoryLimit = MbToBytes(*quota.job_memory_mb);
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY;
    }

    // 最大进程数限制（Win8+）
    if (quota.max_processes.has_value()) {
        ext.BasicLimitInformation.ActiveProcessLimit = *quota.max_processes;
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    }

    // breakaway 控制
    // true  → 设置 BREAKAWAY_OK，允许子进程逃逸
    // false → 不设置，子进程强制留在 Job（沙箱默认安全语义）
    if (quota.breakaway_ok) {
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK;
    }

    if (!SetInformationJobObject(job_handle_.get(),
                                 JobObjectExtendedLimitInformation,
                                 &ext, sizeof(ext))) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobSetLimitFailed,
            std::format("SetInformationJobObject(ExtendedLimit) failed: err={}", err));
    }

    return Result<void>::Ok();
}

// =============================================================================
// SetCpuRateControl - CPU 占比硬上限（Win8+）
// =============================================================================
Result<void> JobObjectImpl::SetCpuRateControl(uint32_t percent) {
    if (percent == 0 || percent > 100) {
        return Result<void>::Err(
            ErrorCode::InvalidArgument,
            std::format("cpu_rate_percent out of range: {}", percent));
    }

    // CPU Rate Control 标志说明（MSDN）：
    //   ENABLE      必须，开启 CPU 速率控制
    //   HARD_CAP    硬上限（超出即挂起，而非调度延迟）
    //   WEIGHT_BASED 用 Weight 字段（1-9 权重），与 CpuRate 互斥
    //   不设 WEIGHT_BASED：默认使用 CpuRate 字段（0.01% 单位，100% = 10000）
    JOBOBJECT_CPU_RATE_CONTROL_INFORMATION cpu = {};
    cpu.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                     | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP;
    cpu.CpuRate = static_cast<DWORD>(percent) * 100;  // 单位 0.01%（50% → 5000）

    if (!SetInformationJobObject(job_handle_.get(),
                                 JobObjectCpuRateControlInformation,
                                 &cpu, sizeof(cpu))) {
        DWORD err = GetLastError();
        // 自适应降级：不支持则跳过，不返回错误
        logger_->Log(LogLevel::Warn,
                     std::format("CPU Rate Control not applied (err={}); degrading", err));
        return Result<void>::Ok();
    }

    logger_->Log(LogLevel::Info,
                 std::format("CPU Rate Control set: {}% hard cap", percent));
    return Result<void>::Ok();
}

// =============================================================================
// SetIoRateControl - IO 速率限制（Win10+ 管理员）
// 使用 SetIoRateControlInformationJobObject API（非 SetInformationJobObject）
// =============================================================================
Result<void> JobObjectImpl::SetIoRateControl(uint64_t bytes_per_sec, uint64_t iops) {
    if (bytes_per_sec == 0 && iops == 0) {
        return Result<void>::Err(
            ErrorCode::InvalidArgument,
            "io_rate requires bytes_per_sec or iops > 0");
    }

    JOBOBJECT_IO_RATE_CONTROL_INFORMATION io = {};
    io.ControlFlags = JOB_OBJECT_IO_RATE_CONTROL_ENABLE;
    io.MaxBandwidth = static_cast<LONG64>(bytes_per_sec);  // 字节/秒，0 表示不限
    io.MaxIops = static_cast<LONG64>(iops);                // IOPS，0 表示不限
    io.ReservationIops = 0;
    io.VolumeName = nullptr;  // 应用到所有卷
    io.BaseIoSize = 0;

    // SetIoRateControlInformationJobObject 返回 DWORD 错误码（非 BOOL）
    DWORD err = SetIoRateControlInformationJobObject(job_handle_.get(), &io);
    if (err != ERROR_SUCCESS) {
        // 自适应降级：Win10- 或非管理员会失败
        logger_->Log(LogLevel::Warn,
                     std::format("IO Rate Control not applied (err={}); degrading", err));
        return Result<void>::Ok();
    }

    logger_->Log(LogLevel::Info,
                 std::format("IO Rate Control set: bytes/s={} iops={}", bytes_per_sec, iops));
    return Result<void>::Ok();
}

// =============================================================================
// SetUiLimits / SetUiRestrictions - UI 限制
//
// SetUiLimits(true) = 全量限制（含剪贴板），供 clipboard_isolate 使用；
// 窗口/系统级限制（不含剪贴板）由 SetResourceLimits 的 no_ui 分支直接
// 拼 flags 调用 SetUiRestrictions。
// =============================================================================
Result<void> JobObjectImpl::SetUiLimits(bool no_ui) {
    DWORD flags = 0;
    if (no_ui) {
        flags |= JOB_OBJECT_UILIMIT_HANDLES           // 禁止访问其他进程窗口句柄
              |  JOB_OBJECT_UILIMIT_READCLIPBOARD     // 禁止读剪贴板
              |  JOB_OBJECT_UILIMIT_WRITECLIPBOARD    // 禁止写剪贴板
              |  JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS  // 禁止改系统参数
              |  JOB_OBJECT_UILIMIT_DISPLAYSETTINGS   // 禁止改显示设置
              |  JOB_OBJECT_UILIMIT_GLOBALATOMS;      // 禁止全局原子表
    }
    return SetUiRestrictions(flags);
}

Result<void> JobObjectImpl::SetUiRestrictions(DWORD ui_flags) {
    if (!SetInformationJobObject(job_handle_.get(),
                                 JobObjectBasicUIRestrictions,
                                 &ui_flags, sizeof(ui_flags))) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobSetLimitFailed,
            std::format("SetInformationJobObject(UIRestrictions) failed: err={}", err));
    }
    logger_->Log(LogLevel::Info,
                 std::format("UI restrictions set: flags=0x{:x}", ui_flags));
    return Result<void>::Ok();
}

// =============================================================================
// AssignProcess - 分配进程到 Job
// =============================================================================
Result<void> JobObjectImpl::AssignProcess(void* process_handle) {
    if (!job_handle_) {
        return Result<void>::Err(ErrorCode::InternalError, "Job not created");
    }
    if (!process_handle) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "process_handle is null");
    }

    HANDLE h = static_cast<HANDLE>(process_handle);
    if (!AssignProcessToJobObject(job_handle_.get(), h)) {
        DWORD err = GetLastError();
        if (err == ERROR_ACCESS_DENIED) {
            // 进程已隶属于其他 Job（或父进程在 Job 中且未允许嵌套）
            return Result<void>::Err(
                ErrorCode::JobProcessAlreadyInJob,
                std::format("AssignProcessToJobObject failed (already in job): err={}", err));
        }
        if (err == ERROR_NOT_CAPABLE) {
            return Result<void>::Err(
                ErrorCode::JobProcessAlreadyInJob,
                std::format("AssignProcessToJobObject failed (not capable): err={}", err));
        }
        return Result<void>::Err(
            ErrorCode::JobAssignFailed,
            std::format("AssignProcessToJobObject failed: err={}", err));
    }

    DWORD pid = GetProcessId(h);
    logger_->Log(LogLevel::Info, std::format("process assigned to Job: pid={}", pid));
    return Result<void>::Ok();
}

// =============================================================================
// TerminateAll - 级联终止 Job 内所有进程
// =============================================================================
Result<void> JobObjectImpl::TerminateAll(uint32_t exit_code) {
    if (!job_handle_) {
        return Result<void>::Err(ErrorCode::InternalError, "Job not created");
    }
    if (!TerminateJobObject(job_handle_.get(), exit_code)) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobTerminateFailed,
            std::format("TerminateJobObject failed: err={}", err));
    }
    logger_->Log(LogLevel::Info,
                 std::format("Job terminated all processes: exit_code={}", exit_code));
    return Result<void>::Ok();
}

// =============================================================================
// TerminateProcess - 终止单个进程
// =============================================================================
Result<void> JobObjectImpl::TerminateProcess(void* process_handle, uint32_t exit_code) {
    if (!process_handle) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "process_handle is null");
    }
    HANDLE h = static_cast<HANDLE>(process_handle);
    if (!::TerminateProcess(h, exit_code)) {
        DWORD err = GetLastError();
        if (err == ERROR_ACCESS_DENIED) {
            return Result<void>::Err(
                ErrorCode::JobTerminateFailed,
                std::format("TerminateProcess failed (access denied): err={}", err));
        }
        if (err == ERROR_INVALID_HANDLE || err == ERROR_INVALID_PARAMETER) {
            // 进程可能已退出
            return Result<void>::Err(
                ErrorCode::ProcessAlreadyExited,
                std::format("TerminateProcess failed (process exited?): err={}", err));
        }
        return Result<void>::Err(
            ErrorCode::JobTerminateFailed,
            std::format("TerminateProcess failed: err={}", err));
    }
    DWORD pid = GetProcessId(h);
    logger_->Log(LogLevel::Info,
                 std::format("process terminated: pid={} exit_code={}", pid, exit_code));
    return Result<void>::Ok();
}

// =============================================================================
// QueryAccounting - 查询 CPU/IO/进程数/页错误 会计信息
// =============================================================================
Result<JobAccountingInfo> JobObjectImpl::QueryAccounting() const {
    if (!job_handle_) {
        return Result<JobAccountingInfo>::Err(ErrorCode::InternalError, "Job not created");
    }

    JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION info = {};
    if (!QueryInformationJobObject(job_handle_.get(),
                                   JobObjectBasicAndIoAccountingInformation,
                                   &info, sizeof(info), nullptr)) {
        DWORD err = GetLastError();
        return Result<JobAccountingInfo>::Err(
            ErrorCode::JobQueryFailed,
            std::format("QueryInformationJobObject(Accounting) failed: err={}", err));
    }

    JobAccountingInfo result;
    result.sample_time_ms = NowUnixMs();
    result.total_user_time_100ns = static_cast<uint64_t>(info.BasicInfo.TotalUserTime.QuadPart);
    result.total_kernel_time_100ns = static_cast<uint64_t>(info.BasicInfo.TotalKernelTime.QuadPart);
    result.this_period_user_time_100ns = static_cast<uint64_t>(info.BasicInfo.ThisPeriodTotalUserTime.QuadPart);
    result.this_period_kernel_time_100ns = static_cast<uint64_t>(info.BasicInfo.ThisPeriodTotalKernelTime.QuadPart);

    result.read_operation_count = info.IoInfo.ReadOperationCount;
    result.write_operation_count = info.IoInfo.WriteOperationCount;
    result.other_operation_count = info.IoInfo.OtherOperationCount;
    result.read_transfer_count = info.IoInfo.ReadTransferCount;
    result.write_transfer_count = info.IoInfo.WriteTransferCount;
    result.other_transfer_count = info.IoInfo.OtherTransferCount;

    result.total_processes = info.BasicInfo.TotalProcesses;
    result.active_processes = info.BasicInfo.ActiveProcesses;
    result.terminated_processes = info.BasicInfo.TotalTerminatedProcesses;
    result.total_page_faults = info.BasicInfo.TotalPageFaultCount;

    // 内存峰值补充（best-effort：失败不影响会计主数据）
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION ext = {};
    if (QueryInformationJobObject(job_handle_.get(),
                                  JobObjectExtendedLimitInformation,
                                  &ext, sizeof(ext), nullptr)) {
        result.peak_process_memory = static_cast<uint64_t>(ext.PeakProcessMemoryUsed);
        result.peak_job_memory = static_cast<uint64_t>(ext.PeakJobMemoryUsed);
    } else {
        logger_->Log(LogLevel::Debug,
                     std::format("peak memory query failed: err={}", GetLastError()));
    }

    return Result<JobAccountingInfo>::Ok(std::move(result));
}

// =============================================================================
// QueryPeakMemory - 查询单进程峰值内存
// =============================================================================
Result<uint64_t> JobObjectImpl::QueryPeakMemory() const {
    if (!job_handle_) {
        return Result<uint64_t>::Err(ErrorCode::InternalError, "Job not created");
    }

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION ext = {};
    if (!QueryInformationJobObject(job_handle_.get(),
                                   JobObjectExtendedLimitInformation,
                                   &ext, sizeof(ext), nullptr)) {
        DWORD err = GetLastError();
        return Result<uint64_t>::Err(
            ErrorCode::JobQueryFailed,
            std::format("QueryInformationJobObject(ExtendedLimit) failed: err={}", err));
    }

    return Result<uint64_t>::Ok(static_cast<uint64_t>(ext.PeakProcessMemoryUsed));
}

// =============================================================================
// QueryProcessList - 获取 Job 内所有进程的 PID 列表
//
// 使用 JOBOBJECT_BASIC_PROCESS_ID_LIST（两次调用模式）：
//   1. 第一次调用（nullptr + 0）获取所需缓冲区大小
//   2. 分配缓冲区后第二次调用获取实际数据
// 竞态注意：第一次探测与第二次填充之间进程数可能增长（子进程新加入 Job），
//   第二次调用会以 ERROR_MORE_DATA(234) 失败；此时用返回的更新长度重试。
//   最多重试 8 次（理论上仅需 2 次，重试上限防御极端增长窗口）。
// 注：若返回的 NumberOfProcessIdsInList 大于缓冲区容纳数（进程数增长窗口），
//     仅返回已容纳的 PID（与 Win32 API 语义一致，外部"一次查询为快照"看待）。
// =============================================================================
Result<std::vector<uint32_t>> JobObjectImpl::QueryProcessList() const {
    if (!job_handle_) {
        return Result<std::vector<uint32_t>>::Err(ErrorCode::InternalError, "Job not created");
    }

    // 1. 第一次调用获取所需缓冲区大小
    DWORD required = 0;
    QueryInformationJobObject(job_handle_.get(),
                              JobObjectBasicProcessIdList,
                              nullptr, 0, &required);

    // 2. 分配缓冲区并第二次调用获取实际数据；
    //    进程数在两次调用之间增长 → ERROR_MORE_DATA → 按新长度重试
    for (int attempt = 0; attempt < 8; ++attempt) {
        DWORD buffer_len = std::max<DWORD>(required,
                                           sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST));
        std::vector<uint8_t> buffer(buffer_len);
        auto* info = reinterpret_cast<JOBOBJECT_BASIC_PROCESS_ID_LIST*>(buffer.data());

        DWORD written = 0;
        if (QueryInformationJobObject(job_handle_.get(),
                                      JobObjectBasicProcessIdList,
                                      info, buffer_len, &written)) {
            // 3. 成功：NumberProcesses 为实际生效数量
            std::vector<uint32_t> pids;
            DWORD count = info->NumberOfProcessIdsInList;
            pids.reserve(count);
            for (DWORD i = 0; i < count; ++i) {
                pids.push_back(static_cast<uint32_t>(info->ProcessIdList[i]));
            }

            logger_->Log(LogLevel::Debug,
                         std::format("QueryProcessList: {} process(es) in Job", pids.size()));
            return Result<std::vector<uint32_t>>::Ok(std::move(pids));
        }

        DWORD err = GetLastError();
        if (err != ERROR_MORE_DATA) {
            return Result<std::vector<uint32_t>>::Err(
                ErrorCode::JobQueryFailed,
                std::format("QueryInformationJobObject(ProcessIdList) failed: err={}", err));
        }
        // 进程数增长：用返回的更新长度重试
        required = std::max<DWORD>(written, sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST));
    }

    return Result<std::vector<uint32_t>>::Err(
        ErrorCode::JobQueryFailed,
        "QueryInformationJobObject(ProcessIdList) kept growing (8 attempts)");
}

// =============================================================================
// ReadExitCodeSettled - 读取已退出进程的最终退出码
//
// 退出消息（msg=7/8）送达时，进程处于终止初期，GetExitCodeProcess 可能返回:
//   - STILL_ACTIVE(259)：进程尚未完全终止
//   - 0：退出码尚未落定（终止初期的中间态，崩溃场景尤为常见）
// 本函数用缓存句柄（含 SYNCHRONIZE）等进程终止后再读退出码：
//   1. WaitForSingleObject 等进程完全终止（退出码此时必然落定；0 是合法
//      退出码，不再走重试路径）
//   2. 等待超时（2s 极端情况）时低延迟重试最多 40ms 兜底
// 读取成功后释放缓存句柄（进程已终止，句柄不再需要）。
// =============================================================================
Result<uint32_t> JobObjectImpl::ReadExitCodeSettled(uint32_t pid) {
    constexpr DWORD kStillActive = 259;

    // 1. 优先取缓存查询句柄
    auto it = process_handles_.find(pid);
    if (it != process_handles_.end()) {
        wil::unique_handle handle = std::move(it->second);
        process_handles_.erase(it);

        // 等待进程完全终止（退出码落定），最多 2s
        DWORD wait_r = ::WaitForSingleObject(handle.get(), 2000);

        DWORD code = 0;
        if (wait_r == WAIT_OBJECT_0) {
            // 进程已完全终止：退出码必然落定（0 是合法退出码，无需重试）
            if (!::GetExitCodeProcess(handle.get(), &code)) {
                DWORD err = GetLastError();
                return Result<uint32_t>::Err(
                    ErrorCode::JobQueryFailed,
                    std::format("GetExitCodeProcess failed: pid={} err={}", pid, err));
            }
            return Result<uint32_t>::Ok(code);
        }

        // 2s 超时（极端情况）：低延迟重试，最多 8 次（40ms）
        for (int attempt = 0; attempt < 8; ++attempt) {
            if (!::GetExitCodeProcess(handle.get(), &code)) {
                DWORD err = GetLastError();
                return Result<uint32_t>::Err(
                    ErrorCode::JobQueryFailed,
                    std::format("GetExitCodeProcess failed: pid={} err={}", pid, err));
            }
            if (code != kStillActive && code != 0) {
                break;
            }
            ::Sleep(5);
        }
        return Result<uint32_t>::Ok(code);
    }

    // 2. 无缓存句柄（NEW_PROCESS 时打开失败）兜底：现开现查
    return QueryProcessExitCode(pid);
}

// =============================================================================
// QueryProcessExitCode - 查询单个进程的退出码
//
// OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess。
// 进程仍在运行时返回 STILL_ACTIVE (259)；进程已不存在/权限不足返回 JobQueryFailed。
//
// Job 归属校验 —— pid 必须是本 Job 的进程
// （曾见 pid 集合，含已退出进程），否则返回 ProcessNotFound。
// 这拒绝跨 sandbox 实例的 pid 探测（实例 B 查不到实例 A 的进程状态），
// 且对"从未在本 Job 出现的 pid"（如系统进程 pid=1）返回语义更准确的
// process_not_found 而非 query_failed。
// =============================================================================
Result<uint32_t> JobObjectImpl::QueryProcessExitCode(uint32_t pid) const {
    // 归属校验：曾见集合优先（已退出进程也在集合中），
    // 未命中则兜底查 Job 活进程列表（覆盖 NEW_PROCESS 通知尚未处理的竞态窗口）。
    {
        std::lock_guard<std::mutex> lock(seen_pids_mutex_);
        if (!seen_pids_.contains(pid) && !IsPidInJobAlive(pid)) {
            return Result<uint32_t>::Err(
                ErrorCode::ProcessNotFound,
                std::format("QueryProcessExitCode: pid={} not in this Job", pid));
        }
    }

    wil::unique_handle h(::OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    if (!h) {
        DWORD err = GetLastError();
        return Result<uint32_t>::Err(
            ErrorCode::JobQueryFailed,
            std::format("OpenProcess failed: pid={} err={}", pid, err));
    }

    DWORD exit_code = 0;
    if (!GetExitCodeProcess(h.get(), &exit_code)) {
        DWORD err = GetLastError();
        return Result<uint32_t>::Err(
            ErrorCode::JobQueryFailed,
            std::format("GetExitCodeProcess failed: pid={} err={}", pid, err));
    }

    logger_->Log(LogLevel::Debug,
                 std::format("QueryProcessExitCode: pid={} exit_code={}", pid, exit_code));
    return Result<uint32_t>::Ok(exit_code);
}

// =============================================================================
// IsPidInJobAlive - 指定 pid 是否为本 Job 当前活进程
//
// JobObjectBasicProcessIdList 单次枚举遍历匹配（实时快照，不依赖通知处理时序）。
// 与 QueryProcessList 不同：不重试、不区分增长窗口——仅用于"是否存在"判断，
// 枚举快照中未命中即视为不在（后续 NEW_PROCESS 通知会将其登记进 seen_pids_，
// 由主校验路径接管）。
// =============================================================================
bool JobObjectImpl::IsPidInJobAlive(uint32_t pid) const {
    if (!job_handle_) {
        return false;
    }

    DWORD required = 0;
    QueryInformationJobObject(job_handle_.get(),
                              JobObjectBasicProcessIdList,
                              nullptr, 0, &required);

    DWORD buffer_len = std::max<DWORD>(required, sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST));
    std::vector<uint8_t> buffer(buffer_len);
    auto* info = reinterpret_cast<JOBOBJECT_BASIC_PROCESS_ID_LIST*>(buffer.data());

    DWORD written = 0;
    if (!QueryInformationJobObject(job_handle_.get(),
                                   JobObjectBasicProcessIdList,
                                   info, buffer_len, &written)) {
        // 枚举失败（如进程数在两次调用间增长）：不阻塞查询，视为未命中
        return false;
    }

    for (DWORD i = 0; i < info->NumberOfProcessIdsInList; ++i) {
        if (info->ProcessIdList[i] == pid) {
            return true;
        }
    }
    return false;
}

// =============================================================================
// QueryParentPid - 查询进程父 PID
//
// CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS) 全系统快照 + Process32FirstW/
// NextW 遍历，匹配 pid 取 th32ParentProcessID。文档化 Win32 API（避免未文档化
// 的 NtQueryInformationProcess）。
//
// best-effort 语义：快照失败或未命中（如父进程已随创建者退出）返回 nullopt，
// 调用方（IocpLoop）仅记警告，不阻塞通知投递。每次调用 O(系统进程数)，与
// 既有 QueryProcessPath 同量级，仅 NEW_PROCESS 时执行一次。
// =============================================================================
std::optional<uint32_t> JobObjectImpl::QueryParentPid(uint32_t pid) const {
    wil::unique_handle snap(::CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (!snap) {
        logger_->Log(LogLevel::Warn,
                     std::format("CreateToolhelp32Snapshot failed: pid={} err={}",
                                 pid, GetLastError()));
        return std::nullopt;
    }

    PROCESSENTRY32W entry = {};
    entry.dwSize = sizeof(entry);
    if (!Process32FirstW(snap.get(), &entry)) {
        logger_->Log(LogLevel::Warn,
                     std::format("Process32FirstW failed: pid={} err={}",
                                 pid, GetLastError()));
        return std::nullopt;
    }

    do {
        if (entry.th32ProcessID == pid) {
            return static_cast<uint32_t>(entry.th32ParentProcessID);
        }
    } while (Process32NextW(snap.get(), &entry));

    logger_->Log(LogLevel::Warn,
                 std::format("parent pid not found in snapshot: pid={}", pid));
    return std::nullopt;
}

// =============================================================================
// QueryProcessPath - 查询进程完整路径
//
// OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + QueryFullProcessImageNameW，
// 路径转换为 UTF-8 返回。失败（进程已退出/权限不足）返回 JobQueryFailed，
// 调用方（IocpLoop）失败时仅记警告，不阻塞通知投递。
// =============================================================================
Result<std::string> JobObjectImpl::QueryProcessPath(uint32_t pid) const {
    wil::unique_handle h(::OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    if (!h) {
        DWORD err = GetLastError();
        return Result<std::string>::Err(
            ErrorCode::JobQueryFailed,
            std::format("OpenProcess failed: pid={} err={}", pid, err));
    }

    // 路径长度动态增长：先 260 再翻倍重试（MAX_PATH 之外的长路径）
    DWORD size = 260;
    std::wstring path(size, L'\0');
    for (int attempt = 0; attempt < 4; ++attempt) {
        DWORD len = size;
        if (QueryFullProcessImageNameW(h.get(), 0, path.data(), &len)) {
            path.resize(len);
            // UTF-16 → UTF-8
            int utf8_len = ::WideCharToMultiByte(CP_UTF8, 0,
                                                 path.data(), static_cast<int>(path.size()),
                                                 nullptr, 0, nullptr, nullptr);
            if (utf8_len <= 0) {
                return Result<std::string>::Err(
                    ErrorCode::JobQueryFailed,
                    std::format("WideCharToMultiByte failed: pid={} err={}",
                                pid, GetLastError()));
            }
            std::string utf8(static_cast<size_t>(utf8_len), '\0');
            ::WideCharToMultiByte(CP_UTF8, 0,
                                  path.data(), static_cast<int>(path.size()),
                                  utf8.data(), utf8_len, nullptr, nullptr);
            return Result<std::string>::Ok(std::move(utf8));
        }
        DWORD err = GetLastError();
        if (err != ERROR_INSUFFICIENT_BUFFER) {
            return Result<std::string>::Err(
                ErrorCode::JobQueryFailed,
                std::format("QueryFullProcessImageNameW failed: pid={} err={}", pid, err));
        }
        size *= 2;
        path.resize(size);
    }
    return Result<std::string>::Err(
        ErrorCode::JobQueryFailed,
        std::format("QueryFullProcessImageNameW buffer exhausted: pid={}", pid));
}

// =============================================================================
// SetCrashSilent - 设置崩溃静默标志
//
// JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION：Job 内进程未处理异常时直接终止
// （不弹 Windows 错误对话框 / 不触发 WER），适用于无头自动化场景。
// 实现：先 QueryInformationJobObject 取当前限制，再按 silent 设置/清除标志位，
// 最后 SetInformationJobObject 写回（避免覆盖其他限制项）。
// =============================================================================
Result<void> JobObjectImpl::SetCrashSilent(bool silent) {
    if (!job_handle_) {
        return Result<void>::Err(ErrorCode::InternalError, "Job not created");
    }

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION ext = {};
    DWORD return_length = 0;
    if (!QueryInformationJobObject(job_handle_.get(),
                                   JobObjectExtendedLimitInformation,
                                   &ext, sizeof(ext), &return_length)) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobQueryFailed,
            std::format("QueryInformationJobObject(ExtendedLimit) failed: err={}", err));
    }

    if (silent) {
        ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
    } else {
        ext.BasicLimitInformation.LimitFlags &= ~JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
    }

    if (!SetInformationJobObject(job_handle_.get(),
                                 JobObjectExtendedLimitInformation,
                                 &ext, sizeof(ext))) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobSetLimitFailed,
            std::format("SetInformationJobObject(DieOnUnhandledException) failed: err={}", err));
    }

    logger_->Log(LogLevel::Info,
                 std::format("crash silent mode: {}", silent ? "enabled" : "disabled"));
    return Result<void>::Ok();
}

// =============================================================================
// RegisterNotificationSink - 注册通知回调
// =============================================================================
Result<void> JobObjectImpl::RegisterNotificationSink(IJobNotificationSink& sink) {
    {
        std::lock_guard<std::mutex> lock(sink_mutex_);
        // Shutdown 后 IOCP 线程已停止，注册永不生效 → 显式报错（防静默失效）
        if (!running_.load(std::memory_order_acquire) || !iocp_thread_.joinable()) {
            return Result<void>::Err(
                ErrorCode::InternalError,
                "RegisterNotificationSink called after Shutdown (IOCP thread stopped)");
        }
        sink_ = &sink;
    }
    logger_->Log(LogLevel::Info, "notification sink registered");
    return Result<void>::Ok();
}

// =============================================================================
// Shutdown - 停止通知线程并注销 sink（Shutdown 清理前调用）
//
// 关闭顺序（先停 IOCP 线程，再清 sink_）：
//   IOCP 线程持 sink_（非拥有）调 OnNotification；若 usercase 先于 Job 析构
//   （SandboxInstance::ShutdownAll 显式 reset 顺序：usecase → ... → job），
//   IOCP 线程可能仍在 usercase 析构后回调 → use-after-free → 0xC0000005。
//   先停 IOCP 线程让剩余通知（含子进程退出通知）被 sink_->OnNotification
//   处理完，再清 sink_，保证清理后不再有任何回调。
//   安全性：Close() 释放 GIL 调本方法，IOCP 线程可获 GIL 完成回调。
//   注意：不关闭 job_handle_（usercase 析构仍需 TerminateAll 兜底）。
// =============================================================================
Result<void> JobObjectImpl::Shutdown() {
    StopIocpThread();
    {
        std::lock_guard<std::mutex> lock(sink_mutex_);
        sink_ = nullptr;
    }
    logger_->Log(LogLevel::Info, "job notification shutdown complete");
    return Result<void>::Ok();
}

// =============================================================================
// GetHandle - 获取 Job 句柄（void* 形式）
// =============================================================================
void* JobObjectImpl::GetHandle() const {
    return static_cast<void*>(job_handle_.get());
}

// =============================================================================
// SetupIocp - 创建 IOCP 并绑定到 Job
// =============================================================================
Result<void> JobObjectImpl::SetupIocp() {
    // 1. 创建 IOCP
    HANDLE raw_iocp = CreateIoCompletionPort(INVALID_HANDLE_VALUE, nullptr, 0, 0);
    if (!raw_iocp || raw_iocp == INVALID_HANDLE_VALUE) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::JobIocpCreateFailed,
            std::format("CreateIoCompletionPort failed: err={}", err));
    }
    iocp_handle_.reset(raw_iocp);

    // 2. 绑定 Job 到 IOCP
    JOBOBJECT_ASSOCIATE_COMPLETION_PORT acp = {};
    acp.CompletionKey = reinterpret_cast<PVOID>(kJobCompletionKey);
    acp.CompletionPort = iocp_handle_.get();

    if (!SetInformationJobObject(job_handle_.get(),
                                 JobObjectAssociateCompletionPortInformation,
                                 &acp, sizeof(acp))) {
        DWORD err = GetLastError();
        iocp_handle_.reset();
        return Result<void>::Err(
            ErrorCode::JobIocpCreateFailed,
            std::format("SetInformationJobObject(AssociateCompletionPort) failed: err={}", err));
    }

    logger_->Log(LogLevel::Info, "IOCP bound to Job");
    return Result<void>::Ok();
}

// =============================================================================
// IocpLoop - IOCP 等待线程主循环
// =============================================================================
void JobObjectImpl::IocpLoop() {
    logger_->Log(LogLevel::Debug, "IOCP loop started");

    while (running_.load(std::memory_order_acquire)) {
        DWORD bytes_transferred = 0;
        ULONG_PTR completion_key = 0;
        LPOVERLAPPED overlapped = nullptr;

        BOOL ok = GetQueuedCompletionStatus(iocp_handle_.get(),
                                            &bytes_transferred,
                                            &completion_key,
                                            &overlapped,
                                            INFINITE);

        if (!running_.load(std::memory_order_acquire)) {
            break;  // 停止信号
        }

        if (!ok) {
            DWORD err = GetLastError();
            if (overlapped == nullptr) {
                // GetQueuedCompletionStatus 自身失败（超时/句柄关闭等）
                logger_->Log(LogLevel::Warn,
                             std::format("IOCP GetQueuedCompletionStatus failed: err={}", err));
                continue;
            }
            // overlapped != nullptr：失败的 I/O 完成包
            // Job 通知不会以失败 I/O 形式投递（GetQueuedCompletionStatus 对 Job 消息总返回 TRUE）
            // 直接跳过，避免把失败 I/O 的 overlapped 指针误当 PID 翻译
            logger_->Log(LogLevel::Warn,
                         std::format("IOCP failed I/O completion dropped: err={}", err));
            continue;
        }

        // 只处理 Job 通知（completion_key == kJobCompletionKey）
        if (completion_key != kJobCompletionKey) {
            continue;
        }

        // Job 通知约定：
        //   bytes_transferred = JOB_OBJECT_MSG_* 消息 ID
        //   overlapped = PID（reinterpret_cast，64 位下需经 ULONG_PTR 中转避免截断警告）
        DWORD message = bytes_transferred;
        DWORD pid = static_cast<DWORD>(reinterpret_cast<ULONG_PTR>(overlapped));

        // EXIT_PROCESS / ABNORMAL_EXIT_PROCESS 去重（提前到翻译之前）
        // 崩溃路径（DIE_ON_UNHANDLED_EXCEPTION）会先发 ABNORMAL_EXIT_PROCESS（msg=8）
        // 再发 EXIT_PROCESS（msg=7），避免同一进程通知两次。
        // 第一次已投递退出通知的 pid，后续退出消息直接跳过（跳过前释放查询句柄，
        // 不进入 TranslateMessage 的退出码查询路径，省去重复 OpenProcess/等待）。
        const bool is_exit_msg = (message == JOB_OBJECT_MSG_EXIT_PROCESS ||
                                  message == JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS);
        if (is_exit_msg && exited_pids_.count(pid) > 0) {
            // 释放查询句柄（退出通知已处置，句柄不再需要）
            process_handles_.erase(pid);
            logger_->Log(LogLevel::Debug,
                         std::format("duplicate exit notification suppressed: pid={}", pid));
            continue;
        }

        JobNotification notif = TranslateMessage(message, pid);

        // 打开查询句柄并缓存，供 EXIT_PROCESS 查退出码
        // 进程对象一旦销毁，OpenProcess 会失败；NEW_PROCESS 时进程仍在，
        // 缓存句柄（含 SYNCHRONIZE，供退出时等待终止）确保退出码查询必成功。
        if (notif.type == JobNotificationType::NewProcess) {
            auto query_handle = ::OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
            if (query_handle) {
                auto [it, inserted] =
                    process_handles_.emplace(pid, wil::unique_handle(query_handle));
                if (!inserted) {
                    // 同 pid 重复 NEW_PROCESS（不应发生）：覆盖旧句柄
                    it->second.reset(query_handle);
                }
            } else {
                logger_->Log(LogLevel::Warn,
                             std::format("open process query handle failed: pid={} err={}",
                                         pid, GetLastError()));
            }
        }

        // NEW_PROCESS 时查询进程路径（仅查询一次，进程存续期路径不变）
        // 失败不阻塞通知投递：仅记录警告，process_name/process_path 保持为空
        if (notif.type == JobNotificationType::NewProcess) {
            // 登记曾见 pid（含主进程 Assign 加入时），
            // 供 QueryProcessExitCode 做 Job 归属校验。仅 IocpLoop 线程写。
            {
                std::lock_guard<std::mutex> lock(seen_pids_mutex_);
                seen_pids_.insert(pid);
            }

            // pid 可能被系统复用：新进程接入需清除旧的退出去重记录
            exited_pids_.erase(pid);
            auto path_r = QueryProcessPath(pid);
            if (path_r) {
                notif.process_path = path_r.Value();
                // 取文件名（最后一个 '\' 之后的部分，忽略分隔差异）
                const std::string& p = notif.process_path;
                size_t slash = p.find_last_of("\\/");
                notif.process_name = (slash == std::string::npos)
                                         ? p : p.substr(slash + 1);
            } else {
                logger_->Log(LogLevel::Warn,
                             std::format("process path query failed: pid={} [{}] {}",
                                         pid, static_cast<int>(path_r.Code()),
                                         path_r.Message()));
            }

            // best-effort 填充 parent_pid
            // 父进程已退出/快照失败 → nullopt（事件中省略该字段），不阻塞投递
            notif.parent_pid = QueryParentPid(pid);
            logger_->Log(LogLevel::Debug,
                         std::format("new process: pid={} parent_pid={} name={}",
                                     pid,
                                     notif.parent_pid.has_value()
                                         ? std::to_string(*notif.parent_pid) : "(none)",
                                     notif.process_name));
        }

        // Unknown 不投递给 sink（避免污染上层事件流），仅 Debug 日志
        if (notif.type == JobNotificationType::Unknown) {
            logger_->Log(LogLevel::Warn,
                         std::format("unknown Job message dropped: msg={} pid={}", message, pid));
            continue;
        }

        // EXIT_PROCESS / ABNORMAL_EXIT_PROCESS 去重（在 TranslateMessage 之前完成，
        // 见上方；此处仅登记本次投递的退出 pid）
        if (is_exit_msg) {
            exited_pids_.insert(pid);
        }

        // sink 拷出锁外调用：Shutdown 先 join IOCP 线程再清 sink_，
        // 锁外调用不会与 sink 析构并发（join 已串行化）
        IJobNotificationSink* sink = nullptr;
        {
            std::lock_guard<std::mutex> lock(sink_mutex_);
            sink = sink_;
        }
        if (sink) {
            sink->OnNotification(notif);
        }
    }

    logger_->Log(LogLevel::Debug, "IOCP loop exited");
}

// =============================================================================
// TranslateMessage - 翻译 JOB_OBJECT_MSG_* → JobNotification
//
// 退出分类
//   - JOB_OBJECT_MSG_EXIT_PROCESS（msg=7）：进程正常退出路径，按退出码分类：
//     退出码 == 0 → ProcessExitNormal，否则 ProcessExitAbnormal。
//     GetExitCodeProcess 在进程终止初期可能短暂读到中间值，做短重试等待最终码。
//   - JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS（msg=8）：进程因未处理异常而终止
//     （DIE_ON_UNHANDLED_EXCEPTION 生效时 Windows 额外发送此消息），始终分类为
//     ProcessExitAbnormal；退出码同样短重试读取。
//   - 退出码查询彻底失败 → 兜底 ProcessExit（上层已知有进程退出但不知结果）。
// 双重通知抑制（msg=8 与 msg=7 针对同一进程都会发送）在 IocpLoop 侧做去重。
// =============================================================================
JobNotification JobObjectImpl::TranslateMessage(DWORD message, DWORD pid) {
    JobNotification notif;
    notif.pid = pid;
    notif.timestamp_ms = NowUnixMs();

    switch (message) {
        case JOB_OBJECT_MSG_END_OF_JOB_TIME:
            notif.type = JobNotificationType::EndOfJobTime;
            break;
        case JOB_OBJECT_MSG_END_OF_PROCESS_TIME:
            notif.type = JobNotificationType::EndOfProcessTime;
            break;
        case JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT:
            notif.type = JobNotificationType::ActiveProcessLimit;
            break;
        case JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT:
            notif.type = JobNotificationType::ProcessMemoryLimit;
            break;
        case JOB_OBJECT_MSG_JOB_MEMORY_LIMIT:
            notif.type = JobNotificationType::JobMemoryLimit;
            break;

        case JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS:
            // 未处理异常导致的终止 → 一律异常退出
            {
                auto code = ReadExitCodeSettled(pid);
                if (code) {
                    notif.exit_code = code.Value();
                    notif.type = JobNotificationType::ProcessExitAbnormal;
                } else {
                    // 退出码读不到也按 Abnormal 语义（消息本身已明确异常）
                    notif.type = JobNotificationType::ProcessExitAbnormal;
                    logger_->Log(LogLevel::Warn,
                                 std::format("abnormal exit code read failed: pid={} [{}] {}",
                                             pid, static_cast<int>(code.Code()),
                                             code.Message()));
                }
            }
            break;

        case JOB_OBJECT_MSG_EXIT_PROCESS:
            // 按退出码分类（0=正常，非 0=异常）
            {
                auto code_r = ReadExitCodeSettled(pid);
                if (code_r) {
                    notif.exit_code = code_r.Value();
                    notif.type = (code_r.Value() == 0)
                        ? JobNotificationType::ProcessExitNormal
                        : JobNotificationType::ProcessExitAbnormal;
                } else {
                    // 进程对象已销毁/权限不足：兜底 ProcessExit（无法分类）
                    notif.type = JobNotificationType::ProcessExit;
                    logger_->Log(LogLevel::Warn,
                                 std::format("exit code query failed: pid={} [{}] {}",
                                             pid, static_cast<int>(code_r.Code()),
                                             code_r.Message()));
                }
            }
            break;

        case JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO:
            notif.type = JobNotificationType::ActiveProcessEmpty;
            break;
        case JOB_OBJECT_MSG_NEW_PROCESS:
            notif.type = JobNotificationType::NewProcess;
            break;
        default:
            // 未知消息类型归为 Unknown：上层应忽略或告警，避免误以为有进程退出
            notif.type = JobNotificationType::Unknown;
            break;
    }

    return notif;
}

// =============================================================================
// StopIocpThread - 停止 IOCP 线程
// =============================================================================
void JobObjectImpl::StopIocpThread() {
    if (!iocp_thread_.joinable()) return;

    running_.store(false, std::memory_order_release);

    // 投递一个空完成包唤醒 GetQueuedCompletionStatus（completion_key=0，
    // IocpLoop 检查 running_ 后退出，不会误当 Job 通知处理）
    if (iocp_handle_) {
        if (!PostQueuedCompletionStatus(iocp_handle_.get(), 0, 0, nullptr)) {
            DWORD err = GetLastError();
            logger_->Log(LogLevel::Warn,
                         std::format("PostQueuedCompletionStatus failed: err={}", err));
            // 失败时线程可能仍阻塞在 GetQueuedCompletionStatus，
            // 但析构场景下进程即将退出，可接受
        }
    }

    iocp_thread_.join();
    logger_->Log(LogLevel::Info, "IOCP thread stopped");
}

} // namespace winsandbox
