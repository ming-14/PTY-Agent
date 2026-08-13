// =============================================================================
// ProcessLauncherImpl - Win32 进程启动器实现（infra 层）
//
// 实现 IProcessLauncher 端口，封装 CreateProcessW + stdio 匿名管道。
//
// 设计要点：
//   1. 不分配到 Job：Launch 仅创建进程并返回句柄，由上层 StartProcessUseCase
//      调用 IJobObject::AssignProcess。这样 ProcessLauncher 与 Job 解耦，
//      隔离 token（Low IL，TokenIsolator 派生）由上层经 isolated_token 传入。
//   2. stdio 管道：3 个匿名继承管道（stdin/stdout/stderr）
//      - 沙箱端 HANDLE 不继承（HANDLE_FLAG_INHERIT 清除）
//      - 子进程端 HANDLE 继承（HANDLE_FLAG_INHERIT 设置）
//      - 子进程端通过 STARTUPINFO.hStdInput/Output/Error 传入
//   3. 环境块：UTF-16 双 null 结尾块
//      - inherit_env=true：复制父进程环境，再合并 env_vars
//      - inherit_env=false：仅用 env_vars
//   4. CREATE_NO_WINDOW：避免弹出控制台（沙箱场景通常不需要 UI）
//   5. 不设置 CREATE_BREAKAWAY_FROM_JOB：默认禁止子进程逃逸（沙箱安全语义）
//   6. 不设置 CREATE_SUSPENDED：Launch 后立即返回，AssignProcess 由上层调用
//      风险：极短窗口内子进程可能已运行；但对 OJ 场景可接受
//      （且 Job 会在 AssignProcess 后立即生效，限制已设置完成）
//
// 句柄所有权：
//   - LaunchResult 中所有 void* 句柄归调用方所有
//   - 调用方需 CloseHandle（或交给 wil::unique_handle RAII）
//   - 进程句柄需保留至 WaitForExit 取得退出码后才能关闭
//   - stdio 管道句柄在读取线程退出后再关闭
// =============================================================================

#pragma once

#include "core/entities/Result.hpp"
#include "core/entities/SandboxedProcess.hpp"
#include "core/ports/ILogger.hpp"
#include "core/ports/IProcessLauncher.hpp"

#include <wil/resource.h>

#include <atomic>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace winsandbox {

class ProcessLauncherImpl : public IProcessLauncher {
public:
    explicit ProcessLauncherImpl(std::shared_ptr<ILogger> logger);
    ~ProcessLauncherImpl() override = default;

    ProcessLauncherImpl(const ProcessLauncherImpl&) = delete;
    ProcessLauncherImpl& operator=(const ProcessLauncherImpl&) = delete;
    ProcessLauncherImpl(ProcessLauncherImpl&&) = delete;
    ProcessLauncherImpl& operator=(ProcessLauncherImpl&&) = delete;

    // ----- IProcessLauncher 实现 -----
    Result<LaunchResult> Launch(const LaunchRequest& req) override;
    Result<void> WriteStdin(void* stdin_write, const void* data, size_t size) override;
    void CloseStdin(void* stdin_write) override;
    Result<void> Terminate(void* process_handle, uint32_t exit_code) override;
    Result<void> Signal(void* process_handle, uint32_t pid, ProcessSignal sig) override;
    Result<int32_t> WaitForExit(void* process_handle, uint64_t timeout_ms) override;
    uint64_t CurrentProcessId() override;
    void CloseHandle(void* handle) override;

private:
    // 创建匿名继承管道
    // read_inherit:  读端句柄是否可被子进程继承
    // write_inherit: 写端句柄是否可被子进程继承
    // 返回 {read_handle, write_handle}
    //
    // 设计说明：
    //   read_inherit/write_inherit 语义直接对应 CreatePipe 返回的
    //   (read_end, write_end) 句柄顺序，避免调用方传错句柄：若把写端传给
    //   子进程 hStdInput，子进程 ReadFile(写端) 立即失败 → REPL 进程视为 EOF 退出。
    Result<std::pair<wil::unique_handle, wil::unique_handle>>
        CreateInheritablePipe(bool read_inherit, bool write_inherit);

    // 创建 stdin 专用管道：写端（沙箱）以 FILE_FLAG_OVERLAPPED 打开，
    // 供 WriteStdin 支持超时/取消（防大块写入 + 子进程不读时无限期阻塞）；
    // 读端（子进程）可继承。返回 {read_handle, write_handle}。
    Result<std::pair<wil::unique_handle, wil::unique_handle>> CreateStdinPipe();

    // 构建子进程环境块（UTF-16，双 null 结尾）
    // inherit_env=true：复制父进程环境，再合并 env_vars（同名覆盖）
    // inherit_env=false：仅用 env_vars
    // 返回的 vector 末尾保证是双 \0\0，可直接传给 CreateProcessW.lpEnvironment
    // 失败场景：GetEnvironmentStringsW 失败 / UTF-8→UTF-16 转换失败
    Result<std::vector<wchar_t>> BuildEnvironmentBlock(
        bool inherit_env,
        const std::vector<std::pair<std::string, std::string>>& env_vars);

    // UTF-8 → UTF-16
    static std::wstring ToWide(const std::string& s);

    std::shared_ptr<ILogger> logger_;
};

} // namespace winsandbox
