// =============================================================================
// JobObjectImpl - Win32 Job Object 实现（infra 层）
//
// 实现 IJobObject 端口，封装 CreateJobObjectW / SetInformationJobObject /
// AssignProcessToJobObject / TerminateJobObject / QueryInformationJobObject /
// CreateIoCompletionPort / GetQueuedCompletionStatus。
//
// 设计要点：
//   1. kill-on-close：JOB_OBJECT_LIMIT_KILL_ON_JOB 标志，Job 句柄关闭时
//      自动终止所有进程（RAII 保证）
//   2. breakaway_ok 逻辑：
//      - true  → 设置 JOB_OBJECT_LIMIT_BREAKAWAY_OK，允许子进程逃逸
//      - false（默认）→ 不设置，子进程强制留在 Job 内（沙箱安全语义）
//   3. IOCP 通知：CreateIoCompletionPort 绑定 Job，独立线程 GetQueuedCompletionStatus
//   4. 自适应降级：CPU Rate Control（Win8+）、IO Rate Control（Win10+ 管理员）
//      不可用时记 warn 日志跳过，不返回错误
//   5. 线程安全：Win32 Job API 本身线程安全；sink_ 指针用 mutex 保护
//
// 句柄约定：
//   - IJobObject 接口用 void* 代替 HANDLE，本实现负责 reinterpret_cast
//   - wil::unique_handle 管理 Job / IOCP 句柄生命周期
// =============================================================================

#pragma once

#include "core/entities/JobAccountingInfo.hpp"
#include "core/entities/JobNotification.hpp"
#include "core/entities/ResourceQuota.hpp"
#include "core/entities/Result.hpp"
#include "core/ports/IJobNotificationSink.hpp"
#include "core/ports/IJobObject.hpp"
#include "core/ports/ILogger.hpp"

#include <wil/resource.h>

#include <atomic>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <unordered_set>

namespace winsandbox {

class JobObjectImpl : public IJobObject {
public:
    explicit JobObjectImpl(std::shared_ptr<ILogger> logger);
    ~JobObjectImpl() override;

    JobObjectImpl(const JobObjectImpl&) = delete;
    JobObjectImpl& operator=(const JobObjectImpl&) = delete;
    JobObjectImpl(JobObjectImpl&&) = delete;
    JobObjectImpl& operator=(JobObjectImpl&&) = delete;

    // ----- IJobObject 实现 -----
    Result<void> Create() override;
    Result<void> SetResourceLimits(const ResourceQuota& quota) override;
    Result<void> SetUiLimits(bool no_ui) override;
    Result<void> AssignProcess(void* process_handle) override;
    Result<void> TerminateAll(uint32_t exit_code) override;
    Result<void> TerminateProcess(void* process_handle, uint32_t exit_code) override;
    Result<JobAccountingInfo> QueryAccounting() const override;
    Result<uint64_t> QueryPeakMemory() const override;
    Result<std::vector<uint32_t>> QueryProcessList() const override;
    Result<uint32_t> QueryProcessExitCode(uint32_t pid) const override;
    Result<void> SetCrashSilent(bool silent) override;
    // 查询进程完整路径
    // 成功返回 UTF-8 路径；失败（进程已退出/权限不足）返回 JobQueryFailed
    Result<std::string> QueryProcessPath(uint32_t pid) const override;
    Result<void> RegisterNotificationSink(IJobNotificationSink& sink) override;
    Result<void> Shutdown() override;
    void* GetHandle() const override;

private:
    // ----- 资源限制内部方法 -----
    // 设置 ExtendedLimitInformation（CPU 时间/内存/进程数/breakaway）
    Result<void> SetExtendedLimits(const ResourceQuota& quota);

    // 设置 CPU Rate Control（Win8+，硬上限）
    // 不可用则记 warn 日志返回 Ok（降级）
    Result<void> SetCpuRateControl(uint32_t percent);

    // 设置 IO Rate Control（Win10+ 管理员）
    // 不可用则记 warn 日志返回 Ok（降级）
    Result<void> SetIoRateControl(uint64_t bytes_per_sec, uint64_t iops);

    // 设置 UI 限制（JOB_OBJECT_UILIMIT_* 组合）
    Result<void> SetUiRestrictions(DWORD ui_flags);

    // ----- IOCP 通知 -----
    // 创建 IOCP 并绑定到 Job
    Result<void> SetupIocp();

    // IOCP 等待线程主循环
    void IocpLoop();

    // 翻译 JOB_OBJECT_MSG_* → JobNotification
    // 非 static —— 内部需调 QueryProcessExitCode 区分正常/异常退出
    JobNotification TranslateMessage(DWORD message, DWORD pid);

    // 读取已退出进程的最终退出码
    //   1. 优先用 process_handles_ 缓存的查询句柄（NEW_PROCESS 时已以
    //      PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE 打开）
    //   2. WaitForSingleObject 等待进程完全终止（崩溃场景下退出码要等系统
    //      结束进程后才落定，直接读会得到 0/259 的中间值）
    //   3. 读成功后释放缓存句柄；失败返回 JobQueryFailed
    Result<uint32_t> ReadExitCodeSettled(uint32_t pid);

    // 查询进程父 PID（best-effort）
    // CreateToolhelp32Snapshot + Process32FirstW/NextW 遍历匹配 pid，
    // 返回 th32ParentProcessID。未命中/快照失败返回 nullopt（不阻塞通知投递）。
    // 父进程可能已随创建者退出（快照中无记录），调用方须容忍省略。
    std::optional<uint32_t> QueryParentPid(uint32_t pid) const;

    // 进程句柄缓存
    // NEW_PROCESS 时以 PROCESS_QUERY_LIMITED_INFORMATION 打开并缓存查询句柄，
    // EXIT_PROCESS 时用该句柄 GetExitCodeProcess（进程对象销毁后仍可查退出码），
    // 查询完即释放。仅 IocpLoop 线程访问。
    std::unordered_map<uint32_t, wil::unique_handle> process_handles_;

    // 已投递退出通知的 pid（去重，避免 ABNORMAL_EXIT + EXIT 双通知）
    // 仅 IocpLoop 线程访问。
    std::unordered_set<uint32_t> exited_pids_;

    // 出现过的 pid 集合
    // NEW_PROCESS 通知到达的所有 pid（含主进程 Assign 加入时），进程退出后保留
    // （退出码查询窗口依赖），Job 存续期间只增。供 QueryProcessExitCode 做
    // Job 归属校验（pid 必须属于本 Job，拒绝跨实例探测）。
    // 写：IocpLoop 线程；读：命令处理线程 → 用独立 mutex 保护。
    std::unordered_set<uint32_t> seen_pids_;
    mutable std::mutex seen_pids_mutex_;

    // 查询指定 pid 是否为本 Job 活进程
    // JobObjectBasicProcessIdList 实时枚举（不依赖通知处理，覆盖 NEW_PROCESS
    // 通知到达前的竞态窗口）。QueryProcessExitCode 的 seen_pids_ 兜底。
    bool IsPidInJobAlive(uint32_t pid) const;

    // 停止 IOCP 线程（析构用）
    void StopIocpThread();

    // ----- 成员 -----
    std::shared_ptr<ILogger> logger_;

    wil::unique_handle job_handle_;     // Job Object 句柄
    wil::unique_handle iocp_handle_;    // IOCP 句柄

    std::atomic<bool> running_{false};  // IOCP 线程运行标志
    std::thread iocp_thread_;           // IOCP 等待线程

    IJobNotificationSink* sink_{nullptr};  // 通知接收方（非拥有）
    std::mutex sink_mutex_;                // 保护 sink_
};

} // namespace winsandbox
