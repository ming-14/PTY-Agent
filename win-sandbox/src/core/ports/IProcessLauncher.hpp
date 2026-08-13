// =============================================================================
// IProcessLauncher - 进程启动器端口（core 层）
//
// 抽象 CreateProcess + stdio 管道创建。
// Phase 1：普通 Token（CreateProcessW）；
// Phase 16：隔离 token 模式 - 当 LaunchRequest.isolated_token 非空时走
//           CreateProcessAsUserW（token 由 ITokenIsolator 派生：Low IL + 无特权）。
//
// 句柄所有权约定：
//   - LaunchResult 中的所有 void* 句柄归调用方所有
//   - 调用方负责 CloseHandle（或交给 wil::unique_handle RAII）
//   - 进程句柄需保留至 WaitForExit 取得退出码后才能关闭
//   - stdio 管道句柄在读取线程退出后再关闭
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"
#include "core/entities/SandboxedProcess.hpp"

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace winsandbox {

// Phase 3 T3.4：进程信号类型
// CtrlBreak 依赖 GenerateConsoleCtrlEvent，要求子进程与沙箱共享 console
//   （StartProcessUseCase 在 interactive=true 时不设 CREATE_NO_WINDOW）
// Kill 直接 TerminateProcess，不依赖 console
//
// 为何不含 CtrlC（T3.4 设计决策）：
//   Windows 上 CTRL_C_EVENT 无法定向投递到非调用进程所在进程组（API 返回 TRUE 但
//   不投递），只能广播（GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)），广播会命中
//   调用方自身，要求调用方注册 SetConsoleCtrlHandler 屏蔽，是坏设计。
//   故沙箱不提供 CtrlC，用 CtrlBreak 替代（CtrlBreak 可定向投递到 CREATE_NEW_PROCESS_GROUP
//   创建的进程组，不影响调用方）。
enum class ProcessSignal {
    CtrlBreak,  // CTRL_BREAK_EVENT：定向投递到子进程组，子进程可捕获；Python 默认 SIGBREAK 退出
    Kill,       // TerminateProcess：强制终止，不可捕获；句柄级精确投递
};

// 启动进程请求
struct LaunchRequest {
    std::string command_line;        // 完整命令行（含可执行路径），UTF-8
    std::string working_dir;         // 工作目录，空表示继承父进程
    std::vector<std::pair<std::string, std::string>> env_vars;  // 额外环境变量
    bool inherit_env = true;         // 是否继承父进程环境变量
    bool create_no_window = true;    // CREATE_NO_WINDOW 标志（避免弹出控制台）

    // ===== 隔离 token 模式字段（Phase 16 Low IL）=====
    // 当 isolated_token 非空时启用隔离：
    //   - 实现层走 CreateProcessAsUserW(isolated_token) + 普通 STARTUPINFOW
    //   - token 由 ITokenIsolator 派生（DuplicateTokenEx + CreateRestrictedToken
    //     清特权 + SetTokenInformation IL=Low），实现层拥有，调用方不可 CloseHandle
    // 当 isolated_token 为空时走普通 CreateProcessW 路径
    void* isolated_token = nullptr;

    // ===== ConPTY 模式字段（ConPTY 深度切合）=====
    // 当 hpcon 非空时启用 ConPTY 伪终端模式：
    //   - 不创建匿名管道（子进程 stdio 由 ConPTY 内核驱动自动分配）
    //   - bInheritHandles = FALSE（伪控制台句柄通过属性传递，不需要管道继承）
    //   - hStdInput/Output/Error = NULL（STARTF_USESTDHANDLES 置位但句柄留空）
    //   - PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 携带 HPCON
    //   - 与隔离 token 可组合（PSEUDOCONSOLE 属性 + CreateProcessAsUserW）
    // 当 hpcon 为空时走匿名管道路径
    //
    // hpcon 由外部创建（PTY-Agent ConDrv 直连），通过 pybind11 传入
    // 生命周期：调用方拥有，Launch 不 ClosePseudoConsole（由外部 close 时清理）
    void* hpcon = nullptr;
};

// 启动进程结果
// 所有句柄为 void* 形式，调用方 reinterpret_cast<HANDLE> 使用
struct LaunchResult {
    SandboxedProcess process;        // 进程领域信息（pid 等）
    void* process_handle = nullptr;  // 主线程进程句柄（PROCESS_ALL_ACCESS）
    void* thread_handle = nullptr;   // 主线程句柄（可立即 CloseHandle 减少引用）
    void* stdin_write = nullptr;     // stdin 管道写端（沙箱→子进程）
    void* stdout_read = nullptr;     // stdout 管道读端（子进程→沙箱）
    void* stderr_read = nullptr;     // stderr 管道读端（子进程→沙箱）
};

class IProcessLauncher {
public:
    virtual ~IProcessLauncher() = default;

    // 启动进程（不分配到 Job，由调用方负责 AssignProcess）
    // 失败场景：CreatePipe 失败 / CreateProcessW 失败
    virtual Result<LaunchResult> Launch(const LaunchRequest& req) = 0;

    // Phase 3：写入子进程 stdin
    // stdin_write: LaunchResult.stdin_write 句柄（interactive=true 时由 StartProcessUseCase 保留）
    // data/size:   要写入的字节流（UTF-8 文本或二进制）
    // 同步 WriteFile，匿名管道缓冲区满会阻塞（实际场景 stdin 写入量小，可接受）
    // 失败场景：WriteFile 失败 / 管道已关闭（子进程退出）
    virtual Result<void> WriteStdin(void* stdin_write, const void* data, size_t size) = 0;

    // Phase 3：关闭 stdin 写端（让子进程 ReadFile(stdin) 返回 EOF）
    // 由 StartProcessUseCase 析构或主动断流时调用
    virtual void CloseStdin(void* stdin_write) = 0;

    // 终止进程
    virtual Result<void> Terminate(void* process_handle, uint32_t exit_code) = 0;

    // Phase 3 T3.4：发送信号到子进程
    // CtrlBreak → GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) 定向投递到子进程组
    //             （要求子进程与沙箱共享 console；不影响调用方进程）
    // Kill      → TerminateProcess（强制终止，exit_code 固定 1）
    // pid: 子进程 PID，同时作为 process_group_id（CREATE_NEW_PROCESS_GROUP 创建的进程组 ID = PID）
    // 失败场景：
    //   - 进程已退出（Kill 时预检测）→ ProcessAlreadyExited
    //   - GenerateConsoleCtrlEvent 失败（无 console / 权限不足）→ ProcessSignalFailed
    //   - TerminateProcess 失败 → JobTerminateFailed
    virtual Result<void> Signal(void* process_handle, uint32_t pid, ProcessSignal sig) = 0;

    // 等待进程退出
    // timeout_ms: 0 = 不阻塞立即返回；UINT64_MAX = INFINITE
    // 返回：
    //   成功 → 退出码
    //   超时 → ErrorCode::ProcessStillRunning（进程仍在运行，未退出）
    //   失败 → ErrorCode::ProcessWaitFailed（WaitForSingleObject/GetExitCodeProcess 失败）
    virtual Result<int32_t> WaitForExit(void* process_handle, uint64_t timeout_ms) = 0;
};

} // namespace winsandbox
