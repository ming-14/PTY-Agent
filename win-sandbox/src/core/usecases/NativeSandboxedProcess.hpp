// =============================================================================
// NativeSandboxedProcess - 启动进程用例（core 层，pybind11/native 形态）
//
// 去 IPC 的进程用例（in-process 形态）。负责隔离准备 + Launch + Assign + IOCP
// 通知处理；C++ 端不读流 / 不等退出 / 不管 wall_clock，Python 端自己读句柄、
// 等退出、管 wall_clock。
//
// 隔离准备（Low IL 模型）：
//   Execute = TokenIsolator::Prepare()（Low IL token）
//           + WriteArea::Create()（%LOCALAPPDATA%\...\writable，Low 标签）
//           + %TEMP%/%TMP% 重定向到可写区
//           + Launch(isolated_token) → CreateProcessAsUserW
//   Close  = WriteArea::Teardown()（会话目录整体删除）
//
// 职责：
//   1. Execute(req)：隔离准备 → Launch → AssignProcess
//                    → 返回 ExecuteResult（含句柄，Python 拿去自己读/写/等）
//   2. Terminate(code)：主动终止 Job 内所有进程
//   3. OnNotification（IJobNotificationSink）：翻译 Job 通知为回调 payload
//      → 调 std::function 回调（pybind11 层桥接到 Python callable）
//   4. WriteStdin / CloseStdinWrite / SignalProcess / CloseProcessHandle：句柄操作
//   5. Query*：路由到 IJobObject 查询会计/峰值/PID 列表/退出码
//   6. Close()：显式清理（Python proc.close() 触发），析构兜底调 Close()
//
// 职责边界（与 Python 端分工）：
//   - stdout/stderr 读取：Python 自己 ReadFile（无 C++ StreamReader）
//   - 等待退出：Python 自己 WaitForSingleObject（无 C++ wait 线程）
//   - wall_clock 定时：Python 自己管（无 C++ 定时器线程）
//   - 事件上报：std::function 回调（无 IEventEmitter / Emit* 方法）
//   - stderr AccessDenied 扫描：Python 端（contains_access_denied_keyword）
//
// 句柄所有权约定（单一关闭点，避免双重 CloseHandle）：
//   - process_handle：C++ 端持有，Close() 时 CloseHandle；
//     Python 端只读值用于 WaitForSingleObject，禁止自行关闭
//   - stdin_write：C++ 端持有，CloseStdinWrite()/Close() 关闭；
//     Python 端只读值用于 WriteFile，禁止自行关闭
//   - stdout_read / stderr_read：C++ 端 Execute 后不再持有，Python 独占所有权
//     （ReadFile 到 EOF 后自行 CloseHandle）
//   - thread_handle：C++ 端 Execute 内立即 CloseHandle（不需要）
//   - isolated_token：TokenIsolator 实现层拥有（本类只借用于 Launch），Close 时释放
//
// 线程模型：
//   - Execute / Terminate / WriteStdin / SignalProcess / Close：Python 主线程调用
//   - IOCP 线程：JobObject 内部，回调 OnNotification → 调回调（内部持 cb_mutex_
//     拷贝副本后锁外调用；Python 线程 setter 与之并发安全）
//   - 回调内持 GIL（pybind11 层包装），回调内禁止调 C++ 方法（防死锁）
//
// 生命周期：
//   - 一个 NativeSandboxedProcess 实例管理一个进程
//   - Close()：停止 IOCP 通知 + 关闭 C++ 端持有的句柄（process_handle）+ WriteArea Teardown
//   - 析构：兜底调 Close()，若进程仍在运行会 TerminateAll（Job kill-on-close 兜底）
// =============================================================================
#pragma once

#include "core/entities/Callbacks.hpp"
#include "core/entities/Result.hpp"
#include "core/entities/SandboxedProcess.hpp"
#include "core/entities/StartProcessRequest.hpp"
#include "core/ports/IJobNotificationSink.hpp"
#include "core/ports/IJobObject.hpp"
#include "core/ports/ILogger.hpp"
#include "core/ports/IProcessLauncher.hpp"
#include "core/ports/ITokenIsolator.hpp"
#include "core/ports/IWriteArea.hpp"
#include "core/ports/IWfpEngine.hpp"

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>

namespace winsandbox {

// Execute 返回结果：句柄所有权转 Python（in-process 直接共享 HANDLE 值）
struct NativeExecuteResult {
    SandboxedProcess process;            // 进程领域信息（pid/process_id 等）
    void* process_handle = nullptr;      // 主进程句柄（共享：C++ 查询 + Python wait）
    void* stdin_write = nullptr;         // stdin 管道写端（Python 拥有；interactive=true 时非空）
    void* stdout_read = nullptr;         // stdout 管道读端（Python 拥有）
    void* stderr_read = nullptr;         // stderr 管道读端（Python 拥有）
    bool is_pty = false;                 // ConPTY 模式标记：true 时 stdio 句柄为 nullptr，I/O 走外部 ConPTY
};

// Wait 返回结果：退出码 + 退出原因 + 资源使用统计
struct NativeWaitResult {
    int32_t exit_code = 0;
    ExitReason exit_reason = ExitReason::NormalExit;
    JobAccountingInfo resource_usage;
};

class NativeSandboxedProcess : public IJobNotificationSink {
public:
    // 构造：注入依赖（shared_ptr：usecase 存活期间依赖必存活，
    // 解决 sb.shutdown() 释放依赖后 Python 侧 proc 继续 wait/close 的 UAF）
    // token_isolator / write_area / wfp_engine 为空时退化为无隔离
    NativeSandboxedProcess(std::shared_ptr<ILogger> logger,
                           std::shared_ptr<IJobObject> job_object,
                           std::shared_ptr<IProcessLauncher> process_launcher,
                           std::shared_ptr<ITokenIsolator> token_isolator = nullptr,
                           std::shared_ptr<IWriteArea> write_area = nullptr,
                           std::shared_ptr<IWfpEngine> wfp_engine = nullptr);
    ~NativeSandboxedProcess() override;

    NativeSandboxedProcess(const NativeSandboxedProcess&) = delete;
    NativeSandboxedProcess& operator=(const NativeSandboxedProcess&) = delete;
    NativeSandboxedProcess(NativeSandboxedProcess&&) = delete;
    NativeSandboxedProcess& operator=(NativeSandboxedProcess&&) = delete;

    // 执行 StartProcess：准备隔离 → Launch → AssignProcess → 返回句柄
    // 不启动 StreamReader / wait 线程 / wall_clock 线程（Python 自己做）
    // 成功：返回 NativeExecuteResult（含句柄），失败：返回 Err
    Result<NativeExecuteResult> Execute(const StartProcessRequest& req);

    // 等待进程退出（Python proc.wait() 调用，pybind11 层释放 GIL）
    // timeout_ms: 0 = 不阻塞立即返回；UINT64_MAX = INFINITE
    // 成功：返回 NativeWaitResult（exit_code + exit_reason + resource_usage）
    // 超时：返回 ProcessStillRunning（进程仍在运行）
    // 失败：返回 ProcessWaitFailed
    // 副作用：设置 finished_=true，更新 process_.exit_code/exit_time_ms/state
    Result<NativeWaitResult> Wait(uint64_t timeout_ms);

    // 主动终止 Job 内所有进程（TerminateAll 语义，杀子进程）
    // reason 参数：wall_clock 超时传 KilledByTimeout（Python 端管 wall_clock，调本方法）
    Result<void> Terminate(uint32_t exit_code,
                           ExitReason reason = ExitReason::KilledByUser);

    // 写入子进程 stdin（interactive=true 时可用）
    Result<void> WriteStdin(const void* data, size_t size);

    // 关闭 stdin 写端（让子进程 ReadFile(stdin) 返回 EOF），幂等
    void CloseStdinWrite();

    // 发送信号到子进程（CtrlBreak 软中断 / Kill 强制终止）
    Result<void> SignalProcess(ProcessSignal sig);

    // 原子关闭进程句柄（C++ 端持有的共享句柄）
    void CloseProcessHandle();

    bool HasStdinWrite() const { return stdin_write_.load() != nullptr; }

    // 查询方法（路由到 IJobObject）
    Result<JobAccountingInfo> QueryAccounting() const;
    Result<uint64_t> QueryPeakMemory() const;
    Result<std::vector<uint32_t>> QueryProcessList() const;
    Result<uint32_t> QueryProcessExitCode(uint32_t pid) const;

    // 状态
    const SandboxedProcess& Process() const { return process_; }
    bool IsFinished() const { return finished_.load(); }

    // 显式清理（Python proc.close() 调用）
    // 停止 IOCP 通知线程 + 关闭 C++ 端持有的句柄
    // 幂等：重复调用安全
    void Close();

    // 回调注册（线程安全：IOCP/ETW 线程 invoke 与 Python 线程 set/clear 并发安全；
    // 内部 cb_mutex_ 拷贝副本后锁外调用）
    // 未设置的回调（空 std::function）被跳过
    void SetOnResourceLimit(std::function<void(const ResourceLimitInfo&)> cb);
    void SetOnJobProcessStarted(std::function<void(const JobProcessStartedInfo&)> cb);
    void SetOnJobProcessExited(std::function<void(const JobProcessExitedInfo&)> cb);
    // ETW 行为监控回调（由 NativeSandboxInstance ETW 路由调用）
    // on_behavior_event：ETW 检测到文件/注册表/进程/网络行为事件
    // on_access_denied：ETW 检测到 STATUS_ACCESS_DENIED
    void SetOnBehaviorEvent(std::function<void(const BehaviorEventInfo&)> cb);
    void SetOnAccessDenied(std::function<void(const AccessDeniedInfo&)> cb);

    // 清空全部回调（shutdown() 时调用；清空 std::function 会释放 py::function
    // 捕获，调用方必须持有 GIL）
    void ClearAllCallbacks();

    // 事件注入（与 SetOn* 配对）：由 NativeSandboxInstance 的 ETW/Job 路由
    // 线程调用，锁外壳拷贝回调副本后锁外执行
    void InvokeResourceLimit(const ResourceLimitInfo& info) const;
    void InvokeJobProcessStarted(const JobProcessStartedInfo& info) const;
    void InvokeJobProcessExited(const JobProcessExitedInfo& info) const;
    void InvokeBehaviorEvent(const BehaviorEventInfo& info) const;
    void InvokeAccessDenied(const AccessDeniedInfo& info) const;

    // ----- IJobNotificationSink 实现 -----
    // 由 JobObject IOCP 线程调用，翻译 Job 通知为回调 payload
    void OnNotification(const JobNotification& notif) override;

private:
    // Job 资源限制通知到达后强制终止整个 Job
    void TerminateAllOnLimit();

    // 当前 Unix 毫秒时间戳
    static uint64_t NowUnixMs();

    std::shared_ptr<ILogger> logger_;
    std::shared_ptr<IJobObject> job_object_;
    std::shared_ptr<IProcessLauncher> process_launcher_;
    std::shared_ptr<ITokenIsolator> token_isolator_;  // 可空
    std::shared_ptr<IWriteArea> write_area_;          // 可空
    std::shared_ptr<IWfpEngine> wfp_engine_;          // 可空

    // 回调同步：IOCP/ETW 线程 invoke 与 Python 线程 set/clear 并发安全
    mutable std::mutex cb_mutex_;
    std::function<void(const ResourceLimitInfo&)> on_resource_limit_;
    std::function<void(const JobProcessStartedInfo&)> on_job_process_started_;
    std::function<void(const JobProcessExitedInfo&)> on_job_process_exited_;
    std::function<void(const BehaviorEventInfo&)> on_behavior_event_;
    std::function<void(const AccessDeniedInfo&)> on_access_denied_;

    // 进程状态
    SandboxedProcess process_;
    ResourceQuota quota_;  // 当前请求配额（OnNotification 翻译用）

    // process_handle_ 原子化（防竞态）
    std::atomic<void*> process_handle_{nullptr};  // 共享所有权，Close() 时 CloseHandle
    void* thread_handle_ = nullptr;     // Execute 后立即 CloseHandle（不需要）
    std::atomic<void*> stdin_write_{nullptr};  // Python 拥有（interactive=true）；否则 Execute 内关闭

    // 句柄转 Python 后，C++ 端不再持有的标记（防 Close() 重复关闭 Python 的句柄）
    // Execute 成功后 stdout_read_/stderr_read_ 所有权转 Python，C++ 端不持有
    // process_handle_ 仍由 C++ 端持有（IOCP 查询用），Close() 时关闭

    std::atomic<bool> started_{false};
    std::atomic<bool> terminated_{false};
    std::atomic<bool> finished_{false};
    std::atomic<bool> closed_{false};   // Close() 是否已调用（幂等防护）
    std::atomic<ExitReason> pending_exit_reason_{ExitReason::NormalExit};
};

} // namespace winsandbox
