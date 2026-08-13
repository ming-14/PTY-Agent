// =============================================================================
// IJobObject - Job Object 端口接口（core 层）
//
// 抽象 Win32 Job Object 的资源限制、级联终止、IOCP 通知、会计查询。
// core 层依赖此接口，infra/job/JobObjectImpl 提供具体实现。
//
// 句柄约定（干净架构折中）：
//   - 接口使用 void* 代替 Win32 HANDLE，避免 core 层包含 windows.h
//   - 实现层负责 void* ↔ HANDLE 的 reinterpret_cast
//   - 调用方（如 ProcessLauncherImpl）从 IProcessLauncher 拿到 void* 句柄，
//     传给 AssignProcess；类型安全由实现层保证
//
// 生命周期：
//   1. Create() 创建 Job（kill-on-close）
//   2. SetResourceLimits() / SetUiLimits() 设置限制（启动前）
//   3. RegisterNotificationSink() 注册回调（启动前）
//   4. AssignProcess() 把启动后的进程加入 Job
//   5. 运行期可 QueryAccounting() / QueryPeakMemory()
//   6. TerminateAll() / TerminateProcess() 终止
//   7. 析构：Job 句柄释放，因 KILL_ON_JOB 标志所有进程被自动终止
// =============================================================================
#pragma once

#include "core/entities/JobAccountingInfo.hpp"
#include "core/entities/JobNotification.hpp"
#include "core/entities/ResourceQuota.hpp"
#include "core/entities/Result.hpp"
#include "core/ports/IJobNotificationSink.hpp"

#include <cstdint>
#include <vector>

namespace winsandbox {

class IJobObject {
public:
    virtual ~IJobObject() = default;

    // 创建 Job Object（kill-on-close 标志）
    // 幂等：重复调用返回 Ok 但不重复创建
    virtual Result<void> Create() = 0;

    // 设置资源限制（CPU/内存/IO/进程数/超时/breakaway）
    // 实现层自适应系统版本与权限：不可用的限制项记 warn 日志后跳过，不返回错误
    virtual Result<void> SetResourceLimits(const ResourceQuota& quota) = 0;

    // 单独设置 UI 限制（与 SetResourceLimits 中的 no_ui 等价，便于运行时切换）
    virtual Result<void> SetUiLimits(bool no_ui) = 0;

    // 分配进程到 Job
    // 入参：process_handle 是 IProcessLauncher::Launch 返回的 void* 进程句柄
    // 失败场景：进程已隶属于其他 Job（ERROR_ACCESS_DENIED）→ JobProcessAlreadyInJob
    virtual Result<void> AssignProcess(void* process_handle) = 0;

    // 级联终止 Job 内所有进程
    virtual Result<void> TerminateAll(uint32_t exit_code) = 0;

    // 终止单个进程
    virtual Result<void> TerminateProcess(void* process_handle, uint32_t exit_code) = 0;

    // 查询会计信息（CPU/IO/进程数/页错误）
    virtual Result<JobAccountingInfo> QueryAccounting() const = 0;

    // 查询峰值内存（单进程峰值，字节）
    virtual Result<uint64_t> QueryPeakMemory() const = 0;

    // 获取 Job 内所有进程的 PID 列表
    // 使用 QueryInformationJobObject(JobObjectBasicProcessIdList)
    // 返回: 成功返回 PID 列表（空 Job 返回空列表），失败返回错误码
    virtual Result<std::vector<uint32_t>> QueryProcessList() const = 0;

    // 查询单个进程的退出码
    // 参数: pid - 进程 PID
    // 返回: 成功返回退出码，失败返回错误码
    // 注意: 进程仍在运行时返回 STILL_ACTIVE (259)；进程已退出且句柄关闭时
    //       OpenProcess 可能失败（权限/已不存在）→ JobQueryFailed
    virtual Result<uint32_t> QueryProcessExitCode(uint32_t pid) const = 0;

    // 查询进程完整路径
    // 参数: pid - 进程 PID
    // 返回: 成功返回 UTF-8 完整路径，失败（进程已退出/权限不足）返回 JobQueryFailed
    // 实现: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + QueryFullProcessImageNameW
    virtual Result<std::string> QueryProcessPath(uint32_t pid) const = 0;

    // 设置崩溃静默标志（JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION）
    // 参数: silent - true 崩溃时不弹 Windows 错误对话框（进程直接终止）
    // 返回: 成功返回 Ok，失败返回错误码
    virtual Result<void> SetCrashSilent(bool silent) = 0;

    // 注册 IOCP 通知回调（仅支持单个 sink，重复注册覆盖）
    virtual Result<void> RegisterNotificationSink(IJobNotificationSink& sink) = 0;

    // 停止通知线程并注销 sink（Shutdown 清理前调用）
    //
    // IOCP 通知线程持有 sink_ 指针（非拥有），
    // 若 usecase 先于 Job 析构，IOCP 线程仍可能调用已析构 usecase 的
    // OnNotification → use-after-free → 0xC0000005（间歇性 Shutdown 崩溃）。
    // 本方法在 usecase 析构前停止 IOCP 线程并清空 sink_，消除该竞态。
    // 注意：只停通知线程，不关闭 Job 句柄（usercase 析构仍需 TerminateAll）。
    virtual Result<void> Shutdown() = 0;

    // 获取 Job 句柄（void* 形式，供 ProcessLauncher 在 CreateProcess 后
    // 直接 AssignProcessToJobObject 用的快捷路径；常规路径走 AssignProcess）
    virtual void* GetHandle() const = 0;
};

} // namespace winsandbox
