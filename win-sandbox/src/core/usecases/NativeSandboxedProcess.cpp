// =============================================================================
// NativeSandboxedProcess 实现（pybind11/native 形态）
//
// 隔离准备 + Launch + Assign + IOCP 通知逻辑；
// 无 StreamReader / wait 线程 / wall_clock 线程 / IEventEmitter。
// 隔离准备：TokenIsolator + WriteArea（Low IL 模型）。
// =============================================================================
#include "core/usecases/NativeSandboxedProcess.hpp"

#include <chrono>
#include <format>
#include <string>
#include <string_view>

namespace winsandbox {

// ----- local helper：命令行脱敏 -----
static std::string RedactCommandLine(std::string_view cmdline) {
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

// =============================================================================
// 构造 / 析构
// =============================================================================

NativeSandboxedProcess::NativeSandboxedProcess(std::shared_ptr<ILogger> logger,
                                               std::shared_ptr<IJobObject> job_object,
                                               std::shared_ptr<IProcessLauncher> process_launcher,
                                               std::shared_ptr<ITokenIsolator> token_isolator,
                                               std::shared_ptr<IWriteArea> write_area,
                                               std::shared_ptr<IWfpEngine> wfp_engine)
    : logger_(std::move(logger))
    , job_object_(std::move(job_object))
    , process_launcher_(std::move(process_launcher))
    , token_isolator_(std::move(token_isolator))
    , write_area_(std::move(write_area))
    , wfp_engine_(std::move(wfp_engine)) {
    logger_->Log(LogLevel::Debug, "NativeSandboxedProcess created (Low IL mode)");
}

NativeSandboxedProcess::~NativeSandboxedProcess() {
    Close();
}

uint64_t NativeSandboxedProcess::NowUnixMs() {
    using namespace std::chrono;
    return static_cast<uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
}

// =============================================================================
// Execute - 启动隔离进程并返回句柄
//
// 流程（Low IL 模型）：
//   1. 隔离准备：TokenIsolator::Prepare()（Low IL token）+ WriteArea::Create()
//      + %TEMP%/%TMP% 重定向 + Allowlist 时 SOCKS5 代理注入
//   2. Launch（CreateProcessAsUserW(isolated_token)）
//   3. AssignProcess（加入 Job）
//   4. 关闭 thread_handle（不需要）
//   5. 填充 NativeExecuteResult（句柄转 Python）
//   6. stdin_data 处理（非 interactive 时一次性写入 + 关闭）
//
// 不做（Python 自己做）：
//   - 启动 StreamReader（Python 自己 ReadFile）
//   - 启动 wait 线程（Python 自己 WaitForSingleObject）
//   - 启动 wall_clock 定时器（Python 自己管）
//   - 发 ProcessStarted 事件（Python 拿到返回值就知道启动成功了）
// =============================================================================
Result<NativeExecuteResult> NativeSandboxedProcess::Execute(const StartProcessRequest& req) {
    if (started_.exchange(true)) {
        return Result<NativeExecuteResult>::Err(
            ErrorCode::InvalidArgument,
            "NativeSandboxedProcess::Execute already called (one-shot instance)");
    }

    if (job_object_ == nullptr || process_launcher_ == nullptr) {
        return Result<NativeExecuteResult>::Err(
            ErrorCode::InvalidArgument,
            "NativeSandboxedProcess dependencies not set (job/launcher is null)");
    }

    // 1. 隔离准备：token 派生 + 可写区 + 环境注入
    LaunchRequest launch_req;
    launch_req.command_line = req.command_line;
    launch_req.working_dir = req.working_dir;
    launch_req.env_vars = req.env_vars;
    launch_req.inherit_env = req.inherit_env;
    launch_req.create_no_window = !req.interactive;
    launch_req.hpcon = req.hpcon;  // ConPTY 句柄透传
    launch_req.isolated_token = nullptr;

    quota_ = req.quota;

    // 1a. 派生 Low IL 隔离 token（时间点：任意，幂等）
    if (token_isolator_ != nullptr && write_area_ != nullptr) {
        // 1a-1 Low IL token
        auto tok_r = token_isolator_->Prepare();
        if (!tok_r) {
            return Result<NativeExecuteResult>::Err(tok_r.Code(), tok_r.Message());
        }
        launch_req.isolated_token = token_isolator_->GetToken();
        logger_->Log(LogLevel::Info, "isolated token prepared (IL=S-1-16-4096)");

        // 1a-2 可写区（Low 进程唯一可写目录）
        auto area_r = write_area_->Create(req.process_id);
        if (!area_r) {
            return Result<NativeExecuteResult>::Err(area_r.Code(), area_r.Message());
        }
        launch_req.env_vars.emplace_back("TEMP", write_area_->Path());
        launch_req.env_vars.emplace_back("TMP", write_area_->Path());
        logger_->Log(LogLevel::Info,
                     std::format("write area ready + TEMP redirected: {}", write_area_->Path()));

        // 1a-3 cwd：请求方未传 working_dir 时默认落到可写区（可读可写，状态隔离）
        if (launch_req.working_dir.empty()) {
            launch_req.working_dir = write_area_->Path();
            logger_->Log(LogLevel::Info,
                         "working_dir empty, defaulted to write area");
        }
    } else {
        logger_->Log(LogLevel::Warn,
                     "token_isolator/write_area not injected, running WITHOUT isolation");
    }

    // 1b. SOCKS5 代理环境变量注入（net_policy=Allowlist；与 token 无关，WFP 复用）
    if (req.isolation_policy.net_policy == NetworkPolicy::Allowlist &&
        wfp_engine_ != nullptr && !wfp_engine_->IsOpen()) {
        auto open_r = wfp_engine_->Open();
        if (open_r) {
            auto logger = logger_;
            auto on_blocked = [logger](const std::string& ip, uint16_t port,
                                       uint8_t protocol, const std::string& reason) {
                logger->Log(LogLevel::Info,
                            std::format("network blocked (native): ip={} port={} proto={} reason={}",
                                        ip, port, protocol, reason));
            };
            uint64_t instance_id = process_launcher_->CurrentProcessId() * 1000 + NowUnixMs() % 1000;
            auto reg_r = wfp_engine_->RegisterConnectFilter(
                req.isolation_policy.net_allowlist, on_blocked, instance_id);
            if (!reg_r) {
                logger_->Log(LogLevel::Warn,
                             std::format("SOCKS5 proxy start failed: [{}] {}",
                                         static_cast<int>(reg_r.Code()), reg_r.Message()));
                wfp_engine_->Close();
            }
        } else {
            logger_->Log(LogLevel::Warn,
                         std::format("SOCKS5 proxy open failed: [{}] {}",
                                     static_cast<int>(open_r.Code()), open_r.Message()));
        }
    }
    if (req.isolation_policy.net_policy == NetworkPolicy::Allowlist &&
        wfp_engine_ != nullptr && wfp_engine_->IsOpen()) {
        std::string proxy = std::string("socks5://127.0.0.1:") +
                            std::to_string(wfp_engine_->ProxyPort());
        auto already = [&](const std::string& key) {
            for (const auto& [k, v] : launch_req.env_vars) {
                if (k == key) return true;
            }
            return false;
        };
        for (const char* key : {"ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY"}) {
            if (!already(key)) {
                launch_req.env_vars.emplace_back(key, proxy);
            }
        }
        logger_->Log(LogLevel::Info,
                     std::string("allowlist: injected SOCKS5 proxy env for child: ") + proxy);
    }

    // 2. 启动进程
    auto launch_r = process_launcher_->Launch(launch_req);
    if (!launch_r) {
        return Result<NativeExecuteResult>::Err(launch_r.Code(), launch_r.Message());
    }

    auto& launch_result = launch_r.Value();
    process_handle_.store(launch_result.process_handle, std::memory_order_release);
    thread_handle_ = launch_result.thread_handle;
    stdin_write_.store(launch_result.stdin_write);

    process_.pid = launch_result.process.pid;
    process_.process_id = req.process_id;
    process_.command_line = req.command_line;
    process_.working_dir = launch_req.working_dir;
    process_.request_id = req.request_id;
    process_.start_time_ms = launch_result.process.start_time_ms;
    process_.state = ProcessState::Running;

    // 3. 分配进程到 Job
    auto assign_r = job_object_->AssignProcess(process_handle_.load(std::memory_order_acquire));
    if (!assign_r) {
        logger_->Log(LogLevel::Error,
                     std::format("AssignProcess failed: pid={} err={} msg={}",
                                 process_.pid, static_cast<int>(assign_r.Code()),
                                 assign_r.Message()));
        // 立即终止未隔离的进程 + 关闭所有句柄
        void* ph = process_handle_.load(std::memory_order_acquire);
        process_launcher_->Terminate(ph, 1);
        process_handle_.store(nullptr, std::memory_order_release);
        process_launcher_->CloseHandle(ph);
        process_launcher_->CloseHandle(thread_handle_);
        thread_handle_ = nullptr;
        process_launcher_->CloseHandle(stdin_write_.load());
        stdin_write_.store(nullptr);
        process_launcher_->CloseHandle(launch_result.stdout_read);
        process_launcher_->CloseHandle(launch_result.stderr_read);
        return Result<NativeExecuteResult>::Err(assign_r.Code(), assign_r.Message());
    }

    // 4. 关闭 thread_handle（不需要）
    if (thread_handle_ != nullptr) {
        process_launcher_->CloseHandle(thread_handle_);
        thread_handle_ = nullptr;
    }

    // 5. stdin_data 处理
    //    ConPTY 模式（hpcon 非空）：stdin_write_ 为 nullptr，跳过（I/O 由外部 ConPTY 管理）
    //    interactive=true：先写 stdin_data（如有），再保留 stdin_write_ 给 Python
    //    interactive=false：写 stdin_data 后关闭 stdin_write_，让子进程 ReadFile(stdin) EOF
    if (stdin_write_.load() != nullptr) {
        if (!req.stdin_data.empty()) {
            auto write_r = process_launcher_->WriteStdin(
                stdin_write_.load(), req.stdin_data.data(), req.stdin_data.size());
            if (!write_r) {
                logger_->Log(LogLevel::Warn,
                             std::format("initial stdin_data write failed: [{}] {}",
                                         static_cast<int>(write_r.Code()),
                                         write_r.Message()));
            } else {
                logger_->Log(LogLevel::Info,
                             std::format("stdin_data write completed: {} bytes",
                                         req.stdin_data.size()));
            }
        }
        if (!req.interactive) {
            // 非交互：关闭 stdin_write，让子进程 ReadFile(stdin) 立即 EOF
            process_launcher_->CloseStdin(stdin_write_.load());
            stdin_write_.store(nullptr);
        }
        // 交互：保留 stdin_write_，Python 后续 WriteStdin
    }

    logger_->Log(LogLevel::Info,
                 std::format("process started: pid={} cmd={}",
                             process_.pid, RedactCommandLine(req.command_line)));
    logger_->Log(LogLevel::Debug,
                 std::format("process started (full): pid={} cmd={}",
                             process_.pid, req.command_line));

    // 6. 构造返回值（句柄转 Python）
    NativeExecuteResult result;
    result.process = process_;
    result.process_handle = process_handle_.load(std::memory_order_acquire);
    result.stdin_write = stdin_write_.load();  // interactive=true 时非空，Python 拥有
    result.stdout_read = launch_result.stdout_read;  // Python 拥有
    result.stderr_read = launch_result.stderr_read;  // Python 拥有
    result.is_pty = (req.hpcon != nullptr);   // ConPTY 模式标记
    return Result<NativeExecuteResult>::Ok(std::move(result));
}

// =============================================================================
// 回调注册 / Invoke / 清空 - 线程安全回调管理
//
// SetXxx/ClearAllCallbacks：Python 主线程调用（Clear 须持 GIL）
// InvokeXxx：IOCP/ETW 线程调用；锁内拷贝副本，锁外执行（防回调阻塞持锁）
// =============================================================================
void NativeSandboxedProcess::SetOnResourceLimit(std::function<void(const ResourceLimitInfo&)> cb) {
    std::lock_guard<std::mutex> lk(cb_mutex_);
    on_resource_limit_ = std::move(cb);
}

void NativeSandboxedProcess::SetOnJobProcessStarted(std::function<void(const JobProcessStartedInfo&)> cb) {
    std::lock_guard<std::mutex> lk(cb_mutex_);
    on_job_process_started_ = std::move(cb);
}

void NativeSandboxedProcess::SetOnJobProcessExited(std::function<void(const JobProcessExitedInfo&)> cb) {
    std::lock_guard<std::mutex> lk(cb_mutex_);
    on_job_process_exited_ = std::move(cb);
}

void NativeSandboxedProcess::SetOnBehaviorEvent(std::function<void(const BehaviorEventInfo&)> cb) {
    std::lock_guard<std::mutex> lk(cb_mutex_);
    on_behavior_event_ = std::move(cb);
}

void NativeSandboxedProcess::SetOnAccessDenied(std::function<void(const AccessDeniedInfo&)> cb) {
    std::lock_guard<std::mutex> lk(cb_mutex_);
    on_access_denied_ = std::move(cb);
}

void NativeSandboxedProcess::ClearAllCallbacks() {
    std::lock_guard<std::mutex> lk(cb_mutex_);
    on_resource_limit_ = nullptr;
    on_job_process_started_ = nullptr;
    on_job_process_exited_ = nullptr;
    on_behavior_event_ = nullptr;
    on_access_denied_ = nullptr;
}

void NativeSandboxedProcess::InvokeResourceLimit(const ResourceLimitInfo& info) const {
    std::function<void(const ResourceLimitInfo&)> cb;
    {
        std::lock_guard<std::mutex> lk(cb_mutex_);
        cb = on_resource_limit_;
    }
    if (cb) {
        cb(info);
    }
}

void NativeSandboxedProcess::InvokeJobProcessStarted(const JobProcessStartedInfo& info) const {
    std::function<void(const JobProcessStartedInfo&)> cb;
    {
        std::lock_guard<std::mutex> lk(cb_mutex_);
        cb = on_job_process_started_;
    }
    if (cb) {
        cb(info);
    }
}

void NativeSandboxedProcess::InvokeJobProcessExited(const JobProcessExitedInfo& info) const {
    std::function<void(const JobProcessExitedInfo&)> cb;
    {
        std::lock_guard<std::mutex> lk(cb_mutex_);
        cb = on_job_process_exited_;
    }
    if (cb) {
        cb(info);
    }
}

void NativeSandboxedProcess::InvokeBehaviorEvent(const BehaviorEventInfo& info) const {
    std::function<void(const BehaviorEventInfo&)> cb;
    {
        std::lock_guard<std::mutex> lk(cb_mutex_);
        cb = on_behavior_event_;
    }
    if (cb) {
        cb(info);
    }
}

void NativeSandboxedProcess::InvokeAccessDenied(const AccessDeniedInfo& info) const {
    std::function<void(const AccessDeniedInfo&)> cb;
    {
        std::lock_guard<std::mutex> lk(cb_mutex_);
        cb = on_access_denied_;
    }
    if (cb) {
        cb(info);
    }
}

// =============================================================================
// OnNotification - IOCP 通知翻译为回调 payload
// =============================================================================
void NativeSandboxedProcess::OnNotification(const JobNotification& notif) {
    switch (notif.type) {
        case JobNotificationType::EndOfJobTime:
        case JobNotificationType::EndOfProcessTime:
            pending_exit_reason_.store(ExitReason::KilledByCpuLimit);
            {
                ResourceLimitInfo info;
                info.type = "cpu_limit";
                info.pid = notif.pid;
                info.timestamp_ms = notif.timestamp_ms;
                InvokeResourceLimit(info);
            }
            TerminateAllOnLimit();
            break;
        case JobNotificationType::ProcessMemoryLimit:
        case JobNotificationType::JobMemoryLimit:
            pending_exit_reason_.store(ExitReason::KilledByMemoryLimit);
            {
                ResourceLimitInfo info;
                info.type = "memory_limit";
                info.pid = notif.pid;
                info.timestamp_ms = notif.timestamp_ms;
                InvokeResourceLimit(info);
            }
            TerminateAllOnLimit();
            break;
        case JobNotificationType::ActiveProcessLimit:
            // 进程数超限：仅通知，不 TerminateAll（创建时拒绝，既有进程未违规）
            pending_exit_reason_.store(ExitReason::KilledByProcessLimit);
            {
                ResourceLimitInfo info;
                info.type = "process_count_limit";
                info.pid = notif.pid;
                info.timestamp_ms = notif.timestamp_ms;
                InvokeResourceLimit(info);
            }
            break;
        case JobNotificationType::ProcessExit:
            logger_->Log(LogLevel::Debug,
                         std::format("IOCP ProcessExit: pid={}", notif.pid));
            if (notif.pid != process_.pid) {
                JobProcessExitedInfo info;
                info.pid = notif.pid;
                info.exit_kind = "unknown";
                info.timestamp_ms = notif.timestamp_ms;
                InvokeJobProcessExited(info);
            }
            break;
        case JobNotificationType::ProcessExitNormal:
            logger_->Log(LogLevel::Debug,
                         std::format("IOCP ProcessExitNormal: pid={}", notif.pid));
            if (notif.pid != process_.pid) {
                JobProcessExitedInfo info;
                info.pid = notif.pid;
                info.exit_kind = "normal";
                info.exit_code = static_cast<int32_t>(notif.exit_code.value_or(0));
                info.timestamp_ms = notif.timestamp_ms;
                InvokeJobProcessExited(info);
            }
            break;
        case JobNotificationType::ProcessExitAbnormal:
            logger_->Log(LogLevel::Warn,
                         std::format("IOCP ProcessExitAbnormal: pid={} exit_code={}",
                                     notif.pid, notif.exit_code.value_or(0)));
            if (notif.pid != process_.pid) {
                JobProcessExitedInfo info;
                info.pid = notif.pid;
                info.exit_kind = "abnormal";
                info.exit_code = static_cast<int32_t>(notif.exit_code.value_or(0));
                info.timestamp_ms = notif.timestamp_ms;
                InvokeJobProcessExited(info);
            }
            break;
        case JobNotificationType::ActiveProcessEmpty:
            logger_->Log(LogLevel::Debug, "IOCP ActiveProcessEmpty");
            break;
        case JobNotificationType::NewProcess:
            logger_->Log(LogLevel::Debug,
                         std::format("IOCP NewProcess: pid={}", notif.pid));
            if (notif.pid != process_.pid) {
                JobProcessStartedInfo info;
                info.pid = notif.pid;
                info.process_name = notif.process_name;
                info.process_path = notif.process_path;
                info.parent_pid = notif.parent_pid;
                info.timestamp_ms = notif.timestamp_ms;
                InvokeJobProcessStarted(info);
            }
            break;
        case JobNotificationType::Unknown:
            break;
    }
}

// =============================================================================
// TerminateAllOnLimit - Job 资源限制通知后强制终止
// =============================================================================
void NativeSandboxedProcess::TerminateAllOnLimit() {
    if (finished_.load()) {
        return;
    }
    void* ph = process_handle_.load(std::memory_order_acquire);
    if (ph == nullptr) {
        return;
    }
    if (process_launcher_->WaitForExit(ph, 0)) {
        return;
    }
    auto r = job_object_->TerminateAll(1);
    if (!r) {
        logger_->Log(LogLevel::Warn,
                     std::format("TerminateAllOnLimit failed: [{}] {}",
                                 static_cast<int>(r.Code()), r.Message()));
    }
}

// =============================================================================
// Terminate - 主动终止 Job 内所有进程
// =============================================================================
Result<void> NativeSandboxedProcess::Terminate(uint32_t exit_code, ExitReason reason) {
    if (finished_.load()) {
        return Result<void>::Ok();
    }
    void* ph = process_handle_.load(std::memory_order_acquire);
    if (!started_.load() || ph == nullptr) {
        return Result<void>::Err(
            ErrorCode::InvalidArgument,
            "Terminate called on non-started process");
    }

    // race 修复：检查进程是否已退出
    if (process_launcher_->WaitForExit(ph, 0)) {
        logger_->Log(LogLevel::Debug,
                     std::format("Terminate: process already exited (pid={}), no-op",
                                 process_.pid));
        return Result<void>::Ok();
    }

    pending_exit_reason_.store(reason);

    // TerminateAll 杀整个 Job（含子进程），保证 stdout 写端全部关闭
    auto r = job_object_->TerminateAll(exit_code);
    if (!r) {
        pending_exit_reason_.store(ExitReason::NormalExit);
        return r;
    }
    return Result<void>::Ok();
}

// =============================================================================
// SignalProcess - 发送信号（CtrlBreak / Kill）
// =============================================================================
Result<void> NativeSandboxedProcess::SignalProcess(ProcessSignal sig) {
    if (!started_.load()) {
        return Result<void>::Err(ErrorCode::InvalidArgument,
                                  "SignalProcess called on non-started process");
    }
    if (finished_.load()) {
        return Result<void>::Err(ErrorCode::ProcessAlreadyExited,
                                  "process already exited, cannot signal");
    }
    void* ph = process_handle_.load(std::memory_order_acquire);
    if (ph == nullptr) {
        return Result<void>::Err(ErrorCode::InvalidArgument,
                                  "process_handle_ is null");
    }

    if (sig == ProcessSignal::Kill) {
        if (process_launcher_->WaitForExit(ph, 0)) {
            logger_->Log(LogLevel::Debug,
                         std::format("SignalProcess(Kill): process already exited (pid={}), no-op",
                                     process_.pid));
            return Result<void>::Err(ErrorCode::ProcessAlreadyExited,
                                     "process already exited before Signal(Kill)");
        }
        pending_exit_reason_.store(ExitReason::KilledByUser);
    }

    return process_launcher_->Signal(ph, process_.pid, sig);
}

// =============================================================================
// WriteStdin / CloseStdinWrite / CloseProcessHandle
// =============================================================================
Result<void> NativeSandboxedProcess::WriteStdin(const void* data, size_t size) {
    if (!started_.load()) {
        return Result<void>::Err(ErrorCode::InvalidArgument,
                                  "WriteStdin called on non-started process");
    }
    if (finished_.load()) {
        return Result<void>::Err(ErrorCode::ProcessAlreadyExited,
                                  "process already exited, stdin closed");
    }
    if (stdin_write_.load() == nullptr) {
        return Result<void>::Err(ErrorCode::InvalidArgument,
                                  "stdin_write is null (interactive=false or already closed)");
    }
    return process_launcher_->WriteStdin(stdin_write_.load(), data, size);
}

void NativeSandboxedProcess::CloseStdinWrite() {
    void* old = stdin_write_.exchange(nullptr);
    if (old != nullptr) {
        process_launcher_->CloseStdin(old);
        logger_->Log(LogLevel::Debug, "stdin_write closed");
    }
}

void NativeSandboxedProcess::CloseProcessHandle() {
    void* old = process_handle_.exchange(nullptr, std::memory_order_acq_rel);
    if (old != nullptr) {
        process_launcher_->CloseHandle(old);
    }
}

// =============================================================================
// Query* 方法 - 路由到 IJobObject
// =============================================================================
Result<JobAccountingInfo> NativeSandboxedProcess::QueryAccounting() const {
    return job_object_->QueryAccounting();
}

Result<uint64_t> NativeSandboxedProcess::QueryPeakMemory() const {
    return job_object_->QueryPeakMemory();
}

Result<std::vector<uint32_t>> NativeSandboxedProcess::QueryProcessList() const {
    return job_object_->QueryProcessList();
}

Result<uint32_t> NativeSandboxedProcess::QueryProcessExitCode(uint32_t pid) const {
    return job_object_->QueryProcessExitCode(pid);
}

// =============================================================================
// Wait - 等待进程退出（Python proc.wait() 调用）
//
// pybind11 层在调用本方法前释放 GIL（让其他 Python 线程跑）。
// 本方法阻塞在 WaitForSingleObject，不持 GIL。
//
// 副作用：
//   - 设置 finished_=true（Terminate/Close 后续调用变为 no-op）
//   - 更新 process_.exit_code / exit_time_ms / state / exit_reason / resource_usage
// =============================================================================
Result<NativeWaitResult> NativeSandboxedProcess::Wait(uint64_t timeout_ms) {
    void* ph = process_handle_.load(std::memory_order_acquire);
    if (ph == nullptr) {
        return Result<NativeWaitResult>::Err(
            ErrorCode::InvalidArgument, "process_handle is null");
    }

    auto wait_r = process_launcher_->WaitForExit(ph, timeout_ms);
    if (!wait_r) {
        if (wait_r.Code() == ErrorCode::ProcessStillRunning) {
            return Result<NativeWaitResult>::Err(
                ErrorCode::ProcessStillRunning, "wait timed out");
        }
        return Result<NativeWaitResult>::Err(wait_r.Code(), wait_r.Message());
    }
    int32_t exit_code = wait_r.Value();

    // 构造返回结果
    NativeWaitResult wr;
    wr.exit_code = exit_code;
    wr.exit_reason = pending_exit_reason_.load();

    // 崩溃识别：pending 未标记特定原因且退出码为 NTSTATUS 异常段
    //（0xC0000000-0xCFFFFFFF，未处理异常/致命错误）→ 分类为 crash。
    // 排除 STATUS_CONTROL_C_EXIT (0xC000013A)：它是 Ctrl+C/Ctrl+Break 的
    // 控制事件统一终止状态（文档 §4.3），语义是"被信号终止"而非崩溃。
    if (wr.exit_reason == ExitReason::NormalExit) {
        const uint32_t ucode = static_cast<uint32_t>(wr.exit_code);
        if ((ucode & 0xC0000000u) == 0xC0000000u && ucode != 0xC000013Au) {
            wr.exit_reason = ExitReason::Crashed;
            logger_->Log(LogLevel::Warn,
                         std::format("process crashed: pid={} exit_code=0x{:08X}",
                                     process_.pid, ucode));
        }
    }

    // 采集 resource_usage（best-effort，失败不致命）
    auto acc_r = job_object_->QueryAccounting();
    if (acc_r) {
        wr.resource_usage = acc_r.Value();
    }

    // 更新 process_ 状态
    process_.exit_code = wr.exit_code;
    process_.exit_reason = wr.exit_reason;
    process_.state = (wr.exit_reason == ExitReason::NormalExit)
                         ? ProcessState::Exited
                         : ProcessState::Terminated;
    using namespace std::chrono;
    process_.exit_time_ms = static_cast<uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
    process_.resource_usage = wr.resource_usage;

    finished_.store(true);

    return Result<NativeWaitResult>::Ok(std::move(wr));
}

// =============================================================================
// Close - 显式清理（Python proc.close() 调用，析构兜底）
//
// 流程：
//   1. 停止 IOCP 通知线程（防 use-after-free）
//   2. 若进程仍在运行 → TerminateAll（Job kill-on-close 兜底，但显式终止更可控）
//   3. 关闭 C++ 端持有的句柄（process_handle_ / thread_handle_ / stdin_write_）
//   4. 清理可写区（WriteArea Teardown，会话目录整体删除）
//   5. 释放隔离 token；清理 WFP
//
// 幂等：closed_ 原子标志保证只执行一次
// =============================================================================
void NativeSandboxedProcess::Close() {
    if (closed_.exchange(true)) {
        return;
    }

    // 1. 停止 IOCP 通知线程（防悬垂 sink）
    if (job_object_ != nullptr) {
        job_object_->Shutdown();
    }

    // 2. 若进程仍在运行 → TerminateAll
    if (started_.load() && !finished_.load()) {
        logger_->Log(LogLevel::Debug,
                     std::format("Close: terminating running process pid={}", process_.pid));
        if (job_object_ != nullptr) {
            job_object_->TerminateAll(1);
        }
        finished_.store(true);
    }

    // 3. 关闭 C++ 端句柄
    CloseProcessHandle();
    if (thread_handle_ != nullptr) {
        process_launcher_->CloseHandle(thread_handle_);
        thread_handle_ = nullptr;
    }
    CloseStdinWrite();

    // 4. 清理可写区（Low 标签目录整体删除；失败：StartupCleanup 启动期兜底）
    if (write_area_ != nullptr) {
        auto r = write_area_->Teardown();
        if (!r) {
            logger_->Log(LogLevel::Warn,
                         std::format("Close: WriteArea Teardown failed: [{}] {}",
                                     static_cast<int>(r.Code()), r.Message()));
        }
    }

    // 5. 释放隔离 token（实现层拥有）
    if (token_isolator_ != nullptr) {
        token_isolator_->Close();
    }

    // 6. 清理 WFP
    if (wfp_engine_ != nullptr && wfp_engine_->IsOpen()) {
        wfp_engine_->UnregisterAll();
        wfp_engine_->Close();
        logger_->Log(LogLevel::Debug, "Close: WFP engine cleaned");
    }
}

} // namespace winsandbox
