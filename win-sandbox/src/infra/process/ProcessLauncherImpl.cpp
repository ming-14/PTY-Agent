// =============================================================================
// ProcessLauncherImpl 实现
//
// 封装 Win32 CreateProcessW + stdio 匿名管道创建。
//
// 关键 API 映射：
//   Launch()       → CreatePipe × 3 + CreateProcessW + CloseHandle 子进程端 stdin_read/stdout_write/stderr_write
//   Terminate()    → ::TerminateProcess
//   WaitForExit()  → WaitForSingleObject + GetExitCodeProcess
//
// 单位转换：
//   timeout_ms → DWORD：UINT64_MAX → INFINITE；其他 → 截断为 DWORD
//   Unix ms    → FILETIME 不需要（沙箱用 std::chrono 系统时钟记录）
// =============================================================================

#include "infra/process/ProcessLauncherImpl.hpp"

#include <spdlog/spdlog.h>

#include <windows.h>
#include <processthreadsapi.h>  // InitializeProcThreadAttributeList / UpdateProcThreadAttribute

#include <chrono>
#include <format>
#include <map>
#include <string_view>

namespace winsandbox {

namespace {

// 当前 Unix 毫秒时间戳
inline uint64_t NowUnixMs() {
    using namespace std::chrono;
    return static_cast<uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
}

// D4 修复（黑盒报告 r4）：命令行脱敏，仅保留可执行路径、去掉参数。
// 完整命令行可能含口令/令牌，不应以 info 级落盘。
std::string RedactCommandLine(std::string_view cmdline) {
    if (cmdline.empty()) {
        return "(empty)";
    }
    size_t start = 0;
    while (start < cmdline.size() &&
           (cmdline[start] == ' ' || cmdline[start] == '\t')) {
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

// ms → DWORD（WaitForSingleObject 超时）
// UINT64_MAX → INFINITE；其他 → 截断为 DWORD
inline DWORD MsToWaitTimeout(uint64_t ms) {
    if (ms == UINT64_MAX) return INFINITE;
    return static_cast<DWORD>(ms);
}

} // namespace

ProcessLauncherImpl::ProcessLauncherImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger)) {}

// =============================================================================
// ToWide - UTF-8 → UTF-16
// =============================================================================
std::wstring ProcessLauncherImpl::ToWide(const std::string& s) {
    if (s.empty()) return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    if (len <= 0) return {};
    std::wstring w(static_cast<size_t>(len), 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), len);
    w.resize(static_cast<size_t>(len - 1));  // 去掉末尾 null
    return w;
}

// =============================================================================
// CreateInheritablePipe - 创建匿名继承管道
//
// 语义对齐 CreatePipe：返回 {read_handle, write_handle}。
//   read_inherit:  控制读端是否可继承（子进程读 stdin / 沙箱读 stdout、stderr）
//   write_inherit: 控制写端是否可继承（沙箱写 stdin / 子进程写 stdout、stderr）
//
// 调用方根据数据流向选择继承标志：
//   stdin  沙箱→子进程：read_end（子进程读）继承，write_end（沙箱写）不继承
//   stdout 子进程→沙箱：read_end（沙箱读）不继承，write_end（子进程写）继承
//   stderr 同 stdout
// =============================================================================
Result<std::pair<wil::unique_handle, wil::unique_handle>>
ProcessLauncherImpl::CreateInheritablePipe(bool read_inherit, bool write_inherit) {
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;  // 默认允许继承，再通过 SetHandleInformation 精确控制

    HANDLE read_raw = nullptr;
    HANDLE write_raw = nullptr;
    // 64KB 缓冲区（匿名管道最大可设为 64KB），避免 WriteStdin 写入大块数据（如 20MB
    // stdin_data）时因缓冲区过小导致频繁阻塞。默认值 0 为 4KB，对 20MB 数据需 ~5000 次
    // 上下文切换，显著拖慢吞吐量。
    if (!CreatePipe(&read_raw, &write_raw, &sa, 64 * 1024)) {
        DWORD err = GetLastError();
        return Result<std::pair<wil::unique_handle, wil::unique_handle>>::Err(
            ErrorCode::ProcessPipeCreateFailed,
            std::format("CreatePipe failed: err={}", err));
    }

    wil::unique_handle read_h(read_raw);
    wil::unique_handle write_h(write_raw);

    // 精确设置继承标志：消除"默认全继承"带来的意外句柄泄漏到子进程
    if (!SetHandleInformation(read_h.get(),
                              HANDLE_FLAG_INHERIT,
                              read_inherit ? HANDLE_FLAG_INHERIT : 0)) {
        DWORD err = GetLastError();
        return Result<std::pair<wil::unique_handle, wil::unique_handle>>::Err(
            ErrorCode::ProcessPipeCreateFailed,
            std::format("SetHandleInformation(read) failed: err={}", err));
    }
    if (!SetHandleInformation(write_h.get(),
                              HANDLE_FLAG_INHERIT,
                              write_inherit ? HANDLE_FLAG_INHERIT : 0)) {
        DWORD err = GetLastError();
        return Result<std::pair<wil::unique_handle, wil::unique_handle>>::Err(
            ErrorCode::ProcessPipeCreateFailed,
            std::format("SetHandleInformation(write) failed: err={}", err));
    }

    return Result<std::pair<wil::unique_handle, wil::unique_handle>>::Ok(
        std::make_pair(std::move(read_h), std::move(write_h)));
}

// =============================================================================
// BuildEnvironmentBlock - 构建子进程环境块（UTF-16，双 null 结尾）
// =============================================================================
Result<std::vector<wchar_t>> ProcessLauncherImpl::BuildEnvironmentBlock(
    bool inherit_env,
    const std::vector<std::pair<std::string, std::string>>& env_vars) {

    // 用 map 合并环境变量（同名覆盖）：NAME=value 形式（UTF-16）
    std::map<std::wstring, std::wstring, std::less<>> merged;

    // 1. 复制父进程环境变量（如要求继承）
    if (inherit_env) {
        wil::unique_environstrings_ptr parent_env(GetEnvironmentStringsW());
        if (!parent_env) {
            DWORD err = GetLastError();
            return Result<std::vector<wchar_t>>::Err(
                ErrorCode::InternalError,
                std::format("GetEnvironmentStringsW failed: err={}", err));
        }

        const wchar_t* cursor = parent_env.get();
        while (*cursor) {
            std::wstring_view entry(cursor);
            // 跳过 Windows 内置的特殊变量（以 '=' 开头，如 =C:、=ExitCode）
            if (!entry.empty() && entry[0] == L'=') {
                // 仍保留：用于驱动器当前目录解析（必须保留，否则子进程相对路径异常）
                size_t eq_pos = entry.find(L'=');
                if (eq_pos != std::wstring_view::npos) {
                    std::wstring name(entry.substr(0, eq_pos));
                    std::wstring value(entry.substr(eq_pos + 1));
                    merged[name] = std::move(value);
                }
            } else {
                size_t eq_pos = entry.find(L'=');
                if (eq_pos != std::wstring_view::npos) {
                    std::wstring name(entry.substr(0, eq_pos));
                    std::wstring value(entry.substr(eq_pos + 1));
                    merged[name] = std::move(value);
                }
            }
            cursor += entry.size() + 1;  // 跳到下一个变量
        }
    }

    // 2. 合并用户传入的环境变量（覆盖同名）
    for (const auto& [k, v] : env_vars) {
        std::wstring wkey = ToWide(k);
        std::wstring wval = ToWide(v);
        if (wkey.empty()) {
            // 跳过空键
            continue;
        }
        merged[wkey] = std::move(wval);
    }

    // 3. 序列化为双 null 结尾的 UTF-16 块：NAME=value\0NAME=value\0...\0
    std::vector<wchar_t> block;
    // 估算大小：所有 key=value\0 + 末尾 \0
    size_t total = 1;  // 末尾 \0
    for (const auto& [k, v] : merged) {
        total += k.size() + 1 /*=*/ + v.size() + 1 /*\0*/;
    }
    block.reserve(total);

    for (const auto& [k, v] : merged) {
        block.insert(block.end(), k.begin(), k.end());
        block.push_back(L'=');
        block.insert(block.end(), v.begin(), v.end());
        block.push_back(L'\0');
    }
    block.push_back(L'\0');  // 结束符
    // 空环境块（inherit_env=false 且无 env_vars）必须双 null 结尾。
    // 仅单 null 时 CreateProcessW 会继续读取 vector 容量区后的堆内存，
    // 偶发将垃圾当环境条目解析 → ERROR_INVALID_PARAMETER(87)（~80% 概率）。
    if (block.size() == 1) {
        block.push_back(L'\0');
    }

    return Result<std::vector<wchar_t>>::Ok(std::move(block));
}

// =============================================================================
// Launch - 启动进程
// =============================================================================
Result<LaunchResult> ProcessLauncherImpl::Launch(const LaunchRequest& req) {
    LaunchResult result;
    result.process.command_line = req.command_line;
    result.process.working_dir = req.working_dir;
    result.process.state = ProcessState::Pending;

    // ===== ConPTY 模式（hpcon 非空）=====
    // 子进程 stdio 由 ConPTY 内核驱动自动分配，不创建匿名管道。
    // bInheritHandles=FALSE，hStdInput/Output/Error=NULL（STARTF_USESTDHANDLES 置位但句柄留空）。
    // PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE (0x00020016) 携带 HPCON。
    // Phase 16：隔离 token 走 CreateProcessAsUserW（非 SECURITY_CAPABILITIES），
    // 属性列表仅 PSEUDOCONSOLE 一项。
    // conhost.exe 由外部（PTY-Agent）启动，不在沙箱内；子进程在沙箱内，隔离语义完整。
    if (req.hpcon != nullptr) {
        auto env_result = BuildEnvironmentBlock(req.inherit_env, req.env_vars);
        if (!env_result) {
            return Result<LaunchResult>::Err(env_result.Code(), env_result.Message());
        }
        auto& env_block = env_result.Value();

        std::wstring cmd_w = ToWide(req.command_line);
        std::vector<wchar_t> cmd_buf(cmd_w.begin(), cmd_w.end());
        cmd_buf.push_back(L'\0');
        std::wstring workdir_w = ToWide(req.working_dir);
        LPCWSTR workdir_ptr = req.working_dir.empty() ? nullptr : workdir_w.c_str();

        const bool use_iso = (req.isolated_token != nullptr);
        const DWORD attr_count = 1;  // 仅 PSEUDOCONSOLE 属性（Phase 16：AC 属性已移除）

        // 属性列表初始化（attr_count 个属性）
        SIZE_T attr_size = 0;
        if (!::InitializeProcThreadAttributeList(nullptr, attr_count, 0, &attr_size) &&
            ::GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
            DWORD err = ::GetLastError();
            return Result<LaunchResult>::Err(
                ErrorCode::ProcessLaunchFailed,
                std::format("InitializeProcThreadAttributeList(size probe) failed: err={}", err));
        }
        std::vector<BYTE> attr_buf(attr_size);
        LPPROC_THREAD_ATTRIBUTE_LIST attr_list =
            reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attr_buf.data());
        if (!::InitializeProcThreadAttributeList(attr_list, attr_count, 0, &attr_size)) {
            DWORD err = ::GetLastError();
            return Result<LaunchResult>::Err(
                ErrorCode::ProcessLaunchFailed,
                std::format("InitializeProcThreadAttributeList failed: err={}", err));
        }
        auto attr_guard = wil::scope_exit([&] { ::DeleteProcThreadAttributeList(attr_list); });

        // 属性 1: PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
        // lpValue = HPCON 值本身（非 &HPCON），cbSize = sizeof(HPCON) = sizeof(void*)
        if (!::UpdateProcThreadAttribute(
                attr_list, 0,
                0x00020016,  // PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
                req.hpcon, sizeof(void*),
                nullptr, nullptr)) {
            DWORD err = ::GetLastError();
            return Result<LaunchResult>::Err(
                ErrorCode::ProcessLaunchFailed,
                std::format("UpdateProcThreadAttribute(PSEUDOCONSOLE) failed: err={}", err));
        }

        // STARTUPINFOEXW：hStdInput/Output/Error = NULL（ConPTY 驱动自动分配 console 句柄）
        STARTUPINFOEXW siex{};
        siex.StartupInfo.cb = sizeof(siex);
        siex.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        siex.lpAttributeList = attr_list;

        // 不设 CREATE_NO_WINDOW：ConPTY 内部以 --headless 启动 conhost，不弹窗口。
        // 不使用 CREATE_NEW_PROCESS_GROUP：实测该标志会使 ConPTY 的 Ctrl+C（\x03 →
        // CTRL_C_EVENT）失效——子进程成为新进程组后 conhost 无法向其转发中断信号
        // （对照实验：无 CNPG 时 ping 被 \x03 中断并输出 Control-C，带 CNPG 时无响应）。
        DWORD creation_flags = CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT;

        PROCESS_INFORMATION pi{};
        BOOL ok = FALSE;
        if (use_iso) {
            // 隔离 token 模式：CreateProcessAsUserW(隔离 token)
            //（token 由 ITokenIsolator 派生：Low IL + 无特权）
            ok = ::CreateProcessAsUserW(
                static_cast<HANDLE>(req.isolated_token),
                nullptr, cmd_buf.data(),
                nullptr, nullptr,
                FALSE,  // bInheritHandles：ConPTY 要求 FALSE
                creation_flags,
                env_block.data(), workdir_ptr,
                &siex.StartupInfo, &pi);
        } else {
            ok = ::CreateProcessW(
                nullptr, cmd_buf.data(),
                nullptr, nullptr,
                FALSE,
                creation_flags,
                env_block.data(), workdir_ptr,
                &siex.StartupInfo, &pi);
        }
        if (!ok) {
            DWORD err = ::GetLastError();
            return Result<LaunchResult>::Err(
                ErrorCode::ProcessLaunchFailed,
                std::format("CreateProcess(ConPTY) failed: err={} cmd={}",
                            err, RedactCommandLine(req.command_line)));
        }

        result.process_handle = static_cast<void*>(pi.hProcess);
        result.thread_handle  = static_cast<void*>(pi.hThread);
        result.stdin_write    = nullptr;  // ConPTY 模式：stdio 由外部管理
        result.stdout_read    = nullptr;
        result.stderr_read    = nullptr;
        result.process.pid = pi.dwProcessId;
        result.process.state = ProcessState::Running;
        result.process.start_time_ms = NowUnixMs();

        logger_->Log(LogLevel::Info,
                     std::format("conpty process launched: pid={} isolated={}",
                                 pi.dwProcessId, use_iso));
        logger_->Log(LogLevel::Debug,
                     std::format("conpty process launched (full): pid={} cmd={}",
                                 pi.dwProcessId, req.command_line));
        return Result<LaunchResult>::Ok(std::move(result));
    }

    // 1. 创建 3 条 stdio 管道
    //
    // 数据流向与继承标志（read_end/write_end 语义对齐 CreatePipe）：
    //   stdin  沙箱写 → 子进程读：read_end（子进程）继承，write_end（沙箱）不继承
    //   stdout 子进程写 → 沙箱读：read_end（沙箱）不继承，write_end（子进程）继承
    //   stderr 同 stdout
    //
    // 关键修复（Phase 3 T3.3）：旧代码 stdin 误用 (parent_inherit=false, child_inherit=true)
    //   导致 read_end 不继承、write_end 继承，但子进程需要的是 read_end → 子进程拿到的
    //   hStdInput 实际是写端，ReadFile 立即失败，REPL 进程视为 EOF 立即退出。
    auto stdin_pipe = CreateInheritablePipe(/*read_inherit=*/true,  /*write_inherit=*/false);
    if (!stdin_pipe) {
        return Result<LaunchResult>::Err(stdin_pipe.Code(), stdin_pipe.Message());
    }
    auto stdout_pipe = CreateInheritablePipe(/*read_inherit=*/false, /*write_inherit=*/true);
    if (!stdout_pipe) {
        return Result<LaunchResult>::Err(stdout_pipe.Code(), stdout_pipe.Message());
    }
    auto stderr_pipe = CreateInheritablePipe(/*read_inherit=*/false, /*write_inherit=*/true);
    if (!stderr_pipe) {
        return Result<LaunchResult>::Err(stderr_pipe.Code(), stderr_pipe.Message());
    }

    // 拆出读端/写端：pair.first = read_end，pair.second = write_end
    // stdin:  read_end → 子进程 hStdInput；write_end → 沙箱持有（WriteStdin 用）
    // stdout: read_end → 沙箱持有（StreamReader 用）；write_end → 子进程 hStdOutput
    // stderr: 同 stdout
    wil::unique_handle stdin_read   = std::move(stdin_pipe.Value().first);
    wil::unique_handle stdin_write  = std::move(stdin_pipe.Value().second);
    wil::unique_handle stdout_read  = std::move(stdout_pipe.Value().first);
    wil::unique_handle stdout_write = std::move(stdout_pipe.Value().second);
    wil::unique_handle stderr_read  = std::move(stderr_pipe.Value().first);
    wil::unique_handle stderr_write = std::move(stderr_pipe.Value().second);

    // 2. 构建 STARTUPINFO（设置 stdio 句柄）
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput  = stdin_read.get();
    si.hStdOutput = stdout_write.get();
    si.hStdError  = stderr_write.get();

    // interactive 模式（create_no_window=false）下子进程可能继承不到任何 console
    // （沙箱由后台进程启动时无 console），CreateProcessW 会为 console 程序新建
    // 可见 console 窗口弹到桌面。用 STARTF_USESHOWWINDOW + SW_HIDE 隐藏新建窗口：
    // - 继承已有 console 时：窗口已存在，本标志不影响（原终端使用场景行为不变）
    // - 新建 console 时：窗口隐藏，CtrlBreak 定向依赖进程组机制，不受影响
    if (!req.create_no_window) {
        si.dwFlags |= STARTF_USESHOWWINDOW;
        si.wShowWindow = SW_HIDE;
    }

    // 3. 构建环境块
    auto env_result = BuildEnvironmentBlock(req.inherit_env, req.env_vars);
    if (!env_result) {
        return Result<LaunchResult>::Err(env_result.Code(), env_result.Message());
    }
    auto& env_block = env_result.Value();

    // 4. 准备命令行与工作目录（UTF-16）
    // CreateProcessW 可修改 lpCommandLine 参数，所以不能传 const 字符串字面量
    std::wstring cmd_w = ToWide(req.command_line);
    std::vector<wchar_t> cmd_buf(cmd_w.begin(), cmd_w.end());
    cmd_buf.push_back(L'\0');

    std::wstring workdir_w = ToWide(req.working_dir);
    LPCWSTR workdir_ptr = req.working_dir.empty() ? nullptr : workdir_w.c_str();

    // 5. dwCreationFlags
    // CREATE_UNICODE_ENVIRONMENT:  lpEnvironment 是 UTF-16
    // CREATE_NEW_PROCESS_GROUP:    Phase 3 T3.4：新进程组，使 CtrlBreak 可按 pid 定向投递
    //                               （进程组 ID = 新进程 PID；不隔离 console，仅隔离信号广播域）
    // CREATE_NO_WINDOW:            不弹控制台（非 interactive 模式；interactive 模式由上层清除此标志以共享 console）
    //
    // 设计说明（T3.4 最终方案）：
    //   Windows 上 CTRL_C_EVENT 无法定向投递到非调用进程所在组（API 返回 TRUE 但不投递），
    //   CtrlC 只能广播（会命中调用方自身，不适合沙箱定向控制场景）。
    //   故沙箱只支持 CtrlBreak（定向）+ Kill（TerminateProcess），不提供 CtrlC。
    //   CtrlBreak 用 GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) 定向到本进程组，
    //   不影响调用方进程。
    //
    // 不设置 CREATE_BREAKAWAY_FROM_JOB: 默认禁止子进程逃逸（沙箱安全语义）
    // 不设置 CREATE_SUSPENDED:         Launch 后立即返回，AssignProcess 由上层调用
    DWORD creation_flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_PROCESS_GROUP;
    if (req.create_no_window) {
        creation_flags |= CREATE_NO_WINDOW;
    }

    // 6. 创建进程（双模式分支）
    //   - req.isolated_token 非空 → 隔离 token 模式
    //     （CreateProcessAsUserW(token) + 普通 STARTUPINFOW；token 由 TokenIsolator 派生）
    //   - 否则 → 普通模式（CreateProcessW + STARTUPINFOW）
    PROCESS_INFORMATION pi{};
    if (req.isolated_token != nullptr) {
        // ----- 隔离 token 模式（Phase 16 Low IL）-----
        BOOL ok_iso = ::CreateProcessAsUserW(
            static_cast<HANDLE>(req.isolated_token),
            nullptr,                       // lpApplicationName（nullptr 表示从命令行解析）
            cmd_buf.data(),                // lpCommandLine（可修改 buffer）
            nullptr,                       // lpProcessAttributes（默认安全描述符）
            nullptr,                       // lpThreadAttributes
            TRUE,                          // bInheritHandles：stdio 管道子进程端要继承
            creation_flags,
            env_block.data(),              // lpEnvironment
            workdir_ptr,                   // lpCurrentDirectory
            &si,
            &pi);
        if (!ok_iso) {
            DWORD err = ::GetLastError();
            return Result<LaunchResult>::Err(
                ErrorCode::ProcessLaunchFailed,
                std::format("CreateProcessAsUserW(isolated token) failed: err={} cmd={}",
                            err, RedactCommandLine(req.command_line)));
        }
        logger_->Log(LogLevel::Info,
                     std::format("isolated process launched: pid={}", pi.dwProcessId));
    } else {
        // ----- Phase 1 普通模式（CreateProcessW + STARTUPINFOW）-----
        BOOL ok = CreateProcessW(
            nullptr,                // lpApplicationName（nullptr 表示从命令行解析）
            cmd_buf.data(),         // lpCommandLine（可修改 buffer）
            nullptr,                // lpProcessAttributes（默认安全描述符）
            nullptr,                // lpThreadAttributes
            TRUE,                   // bInheritHandles：必须 TRUE，stdio 管道子进程端要继承
            creation_flags,
            env_block.data(),       // lpEnvironment（nullptr 表示继承父进程环境）
            workdir_ptr,            // lpCurrentDirectory
            &si,
            &pi);
        if (!ok) {
            DWORD err = GetLastError();
            return Result<LaunchResult>::Err(
                ErrorCode::ProcessLaunchFailed,
                std::format("CreateProcessW failed: err={} cmd={}", err,
                            RedactCommandLine(req.command_line)));
        }
    }

    // 7. 填充 LaunchResult
    // 沙箱端句柄移交给调用方：
    //   stdin_write  → StartProcessUseCase 持有，用于 WriteStdin / CloseStdin
    //   stdout_read  → StreamReader 持有，读取子进程 stdout
    //   stderr_read  → StreamReader 持有，读取子进程 stderr
    // 子进程端句柄（stdin_read / stdout_write / stderr_write）在这里出作用域自动 CloseHandle：
    //   - 父进程侧关闭后，子进程的 stdout/stderr 写端引用计数归零时沙箱读端才会收到 EOF
    //   - 父进程侧关闭 stdin_read 不影响子进程（子进程继承的是独立句柄表项）
    result.process_handle = static_cast<void*>(pi.hProcess);
    result.thread_handle  = static_cast<void*>(pi.hThread);
    result.stdin_write    = static_cast<void*>(stdin_write.release());
    result.stdout_read    = static_cast<void*>(stdout_read.release());
    result.stderr_read    = static_cast<void*>(stderr_read.release());

    // wil::unique_handle 离开作用域：stdin_read / stdout_write / stderr_write 自动 CloseHandle

    result.process.pid = pi.dwProcessId;
    result.process.state = ProcessState::Running;
    result.process.start_time_ms = NowUnixMs();

    // D4 修复（黑盒报告 r4）：info 级不记录完整命令行（参数可能含口令/令牌），
    // 仅记录可执行路径摘要；完整命令行保留 debug 级。
    logger_->Log(LogLevel::Info,
                 std::format("process launched: pid={} cmd={}",
                             result.process.pid, RedactCommandLine(req.command_line)));
    logger_->Log(LogLevel::Debug,
                 std::format("process launched (full): pid={} cmd={}",
                             result.process.pid, req.command_line));
    return Result<LaunchResult>::Ok(std::move(result));
}

// =============================================================================
// WriteStdin - Phase 3：写入子进程 stdin
//
// 同步 WriteFile 到 stdin 管道写端。匿名管道缓冲区满会阻塞（实际场景 stdin
// 写入量小，可接受；REPL 输入命令通常 < 1KB）。
//
// 失败场景：
//   - stdin_write 为空 → InvalidArgument
//   - WriteFile 失败（管道已关闭/对端断开）→ ProcessStdinWriteFailed
//   - 写入字节数不全（极少见，管道异常）→ ProcessStdinWriteFailed
//
// 注意：调用方需保证 stdin_write 生命周期有效（StartProcessUseCase 持有）
// =============================================================================
Result<void> ProcessLauncherImpl::WriteStdin(void* stdin_write, const void* data, size_t size) {
    if (!stdin_write) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "stdin_write is null");
    }
    if (size == 0) {
        return Result<void>::Ok();  // 空写入直接成功
    }
    if (!data) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "data is null but size > 0");
    }

    HANDLE h = static_cast<HANDLE>(stdin_write);
    // 匿名管道写端不需要 overlapped，同步 WriteFile 即可
    // 匿名管道默认缓冲区 64KB+，写小数据不会阻塞

    // 大块写入计时日志（用于诊断 20MB stdin_data 超时问题）
    auto t0 = std::chrono::steady_clock::now();
    const uint64_t total_size = static_cast<uint64_t>(size);
    logger_->Log(LogLevel::Debug,
                 std::format("WriteStdin: starting write of {} bytes", total_size));

    DWORD written = 0;
    if (!::WriteFile(h, data, static_cast<DWORD>(size), &written, nullptr)) {
        DWORD err = ::GetLastError();
        auto t1 = std::chrono::steady_clock::now();
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
        logger_->Log(LogLevel::Error,
                     std::format("WriteStdin: WriteFile failed after {}ms: err={} (pipe may be closed)",
                                 elapsed, err));
        // ERROR_BROKEN_PIPE (109) / ERROR_NO_DATA (232)：管道已关闭（子进程退出）
        return Result<void>::Err(
            ErrorCode::ProcessStdinWriteFailed,
            std::format("WriteFile(stdin) failed: err={} (pipe may be closed)", err));
    }
    if (written != static_cast<DWORD>(size)) {
        auto t1 = std::chrono::steady_clock::now();
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
        logger_->Log(LogLevel::Error,
                     std::format("WriteStdin: short write after {}ms: {}/{}", elapsed, written, size));
        return Result<void>::Err(
            ErrorCode::ProcessStdinWriteFailed,
            std::format("WriteFile(stdin) short write: {}/{}", written, size));
    }

    auto t1 = std::chrono::steady_clock::now();
    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
    logger_->Log(LogLevel::Info,
                 std::format("WriteStdin: wrote {} bytes in {}ms ({} MB/s)",
                             total_size, elapsed,
                             elapsed > 0 ? (total_size / 1024 / 1024 * 1000 / elapsed) : 0));
    return Result<void>::Ok();
}

// =============================================================================
// CloseStdin - Phase 3：关闭 stdin 写端
//
// 让子进程 ReadFile(stdin) 返回 EOF，REPL 进程感知到输入结束。
// 幂等：nullptr 直接返回。
// =============================================================================
void ProcessLauncherImpl::CloseStdin(void* stdin_write) {
    if (!stdin_write) return;
    ::CloseHandle(static_cast<HANDLE>(stdin_write));
}

// =============================================================================
// Terminate - 终止进程
//
// 实现说明：
//   调用 TerminateProcess 前先用 WaitForSingleObject(h, 0) 显式检测进程状态。
//   原因：直接调 TerminateProcess 对已退出进程会返回 ERROR_ACCESS_DENIED，
//   这个错误码与"权限不足"无法区分，会导致上层误判。
//   通过预检测可明确返回 ProcessAlreadyExited，语义清晰。
// =============================================================================
Result<void> ProcessLauncherImpl::Terminate(void* process_handle, uint32_t exit_code) {
    if (!process_handle) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "process_handle is null");
    }
    HANDLE h = static_cast<HANDLE>(process_handle);

    // 1. 预检测：进程是否已退出
    DWORD pre_check = WaitForSingleObject(h, 0);
    if (pre_check == WAIT_FAILED) {
        DWORD err = GetLastError();
        return Result<void>::Err(
            ErrorCode::ProcessWaitFailed,
            std::format("WaitForSingleObject(pre-terminate check) failed: err={}", err));
    }
    if (pre_check == WAIT_OBJECT_0) {
        // 进程已退出，无需再终止
        return Result<void>::Err(
            ErrorCode::ProcessAlreadyExited,
            "process already exited; Terminate skipped");
    }

    // 2. WAIT_TIMEOUT：进程仍在运行，调用 TerminateProcess
    if (!::TerminateProcess(h, exit_code)) {
        DWORD err = GetLastError();
        if (err == ERROR_ACCESS_DENIED) {
            // 此时已通过预检测确认进程在运行，ACCESS_DENIED 表示权限不足
            return Result<void>::Err(
                ErrorCode::JobTerminateFailed,
                std::format("TerminateProcess failed (access denied): err={}", err));
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
// Signal - Phase 3 T3.4：发送信号到子进程
//
// 两种信号语义（T3.4 最终方案，移除 CtrlC）：
//   CtrlBreak → GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)
//               定向投递到子进程所在进程组（CREATE_NEW_PROCESS_GROUP 创建，组 ID = pid）
//               软中断，子进程可捕获；Python 默认收到后以 SIGBREAK 退出
//   Kill      → TerminateProcess(handle, 1)
//               强制终止，不可捕获；等同 Terminate(handle, 1)
//
// 为何不支持 CtrlC（设计决策）：
//   Windows 上 CTRL_C_EVENT 无法定向投递到非调用进程所在进程组（API 返回 TRUE 但
//   不投递），只能用 GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) 广播到调用进程组所有
//   进程。广播会命中调用方（Python 客户端）自身，要求调用方注册 SetConsoleCtrlHandler
//   屏蔽，是坏设计。故沙箱不提供 CtrlC，用 CtrlBreak 替代（CtrlBreak 可定向）。
//
// console 共享要求（CtrlBreak）：
//   GenerateConsoleCtrlEvent 只能通知"与调用方共享同一 console 的进程"。
//   因此 StartProcessUseCase 在 interactive=true 时不设 CREATE_NO_WINDOW，
//   让子进程继承沙箱的 console。若沙箱本身无 console（如被 pythonw 启动），
//   GenerateConsoleCtrlEvent 会失败（ERROR_INVALID_PARAMETER 或 ERROR_INVALID_HANDLE），
//   调用方应回退到 Kill。
//
// Kill 路径与 Terminate 共用预检测逻辑（避免对已退出进程调 TerminateProcess
// 返回 ACCESS_DENIED 与"权限不足"混淆）。
// =============================================================================
Result<void> ProcessLauncherImpl::Signal(void* process_handle, uint32_t pid, ProcessSignal sig) {
    if (!process_handle) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "process_handle is null");
    }
    HANDLE h = static_cast<HANDLE>(process_handle);

    if (sig == ProcessSignal::Kill) {
        // Kill 路径：预检测 + TerminateProcess（复用 Terminate 的逻辑，exit_code 固定 1）
        DWORD pre_check = WaitForSingleObject(h, 0);
        if (pre_check == WAIT_FAILED) {
            DWORD err = GetLastError();
            return Result<void>::Err(
                ErrorCode::ProcessWaitFailed,
                std::format("WaitForSingleObject(pre-signal kill check) failed: err={}", err));
        }
        if (pre_check == WAIT_OBJECT_0) {
            return Result<void>::Err(
                ErrorCode::ProcessAlreadyExited,
                "process already exited; Signal(Kill) skipped");
        }
        if (!::TerminateProcess(h, 1)) {
            DWORD err = GetLastError();
            if (err == ERROR_ACCESS_DENIED) {
                return Result<void>::Err(
                    ErrorCode::JobTerminateFailed,
                    std::format("TerminateProcess failed (access denied): err={}", err));
            }
            return Result<void>::Err(
                ErrorCode::JobTerminateFailed,
                std::format("TerminateProcess failed: err={}", err));
        }
        logger_->Log(LogLevel::Info,
                     std::format("process killed via Signal: pid={}", pid));
        return Result<void>::Ok();
    }

    // CtrlBreak 路径：GenerateConsoleCtrlEvent 定向投递到子进程组
    // pid 即进程组 ID（CREATE_NEW_PROCESS_GROUP 创建的进程组，根进程 PID = group ID）
    // CtrlBreak 定向投递不影响调用方进程（与 CtrlC 广播不同）
    if (!::GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)) {
        DWORD err = GetLastError();
        // ERROR_INVALID_PARAMETER (87)：通常因为目标进程组不共享调用方的 console
        //   （CREATE_NO_WINDOW 阻止了 console 共享，或沙箱本身无 console）
        // ERROR_NOT_FOUND (1168)：进程组不存在（进程已退出）
        return Result<void>::Err(
            ErrorCode::ProcessSignalFailed,
            std::format("GenerateConsoleCtrlEvent(CtrlBreak) failed: err={} pid={} "
                        "(ensure interactive=true and sandbox has a console)",
                        err, pid));
    }
    logger_->Log(LogLevel::Info,
                 std::format("signal sent: CtrlBreak pid={}", pid));
    return Result<void>::Ok();
}

// =============================================================================
// WaitForExit - 等待进程退出
// =============================================================================
Result<int32_t> ProcessLauncherImpl::WaitForExit(void* process_handle, uint64_t timeout_ms) {
    if (!process_handle) {
        return Result<int32_t>::Err(ErrorCode::InvalidArgument, "process_handle is null");
    }
    HANDLE h = static_cast<HANDLE>(process_handle);

    DWORD wait_result = WaitForSingleObject(h, MsToWaitTimeout(timeout_ms));
    if (wait_result == WAIT_FAILED) {
        DWORD err = GetLastError();
        return Result<int32_t>::Err(
            ErrorCode::ProcessWaitFailed,
            std::format("WaitForSingleObject failed: err={}", err));
    }
    if (wait_result == WAIT_TIMEOUT) {
        // 进程仍在运行
        return Result<int32_t>::Err(
            ErrorCode::ProcessStillRunning,
            std::format("WaitForSingleObject timed out: timeout_ms={}", timeout_ms));
    }

    // WAIT_OBJECT_0：进程已退出，取退出码
    DWORD exit_code = 0;
    if (!GetExitCodeProcess(h, &exit_code)) {
        DWORD err = GetLastError();
        return Result<int32_t>::Err(
            ErrorCode::ProcessWaitFailed,
            std::format("GetExitCodeProcess failed: err={}", err));
    }

    return Result<int32_t>::Ok(static_cast<int32_t>(exit_code));
}

} // namespace winsandbox
