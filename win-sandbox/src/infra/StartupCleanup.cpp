// =============================================================================
// StartupCleanup - 启动时残留资源清理实现
//
// 会话目录清理：
//   枚举 %LOCALAPPDATA%\win-sandbox\sessions\ 下 <os-pid>-<process_id> 目录，
//   删除宿主进程已不存在的残留（WriteArea Teardown 失败时启动期兜底）。
//   删除前检查宿主进程是否存活（OpenProcess + GetExitCodeProcess）：
//   多进程并发运行时，进程 A 的启动清理不得删除进程 B 仍在使用的会话目录
//   （目录命名含 os-pid 前缀，但仅凭名称不能判定进程死活）。
//
// ETW Session 清理：
//   使用 QueryAllTracesW 枚举所有 ETW session，
//   对名字以 win-sandbox-etw- 开头的调 ControlTraceW(STOP) 停止。
//   不处理 NT Kernel Logger（系统级 session，不属本 sandbox）。
// =============================================================================

#include "infra/StartupCleanup.hpp"

#include <windows.h>
#include <knownfolders.h>  // FOLDERID_LocalAppData
#include <shlobj.h>        // SHGetKnownFolderPath
#include <evntrace.h>      // QueryAllTracesW / ControlTraceW / EVENT_TRACE_PROPERTIES

#include <algorithm>  // std::all_of
#include <filesystem>
#include <format>
#include <string>
#include <vector>

#pragma comment(lib, "advapi32.lib")

namespace winsandbox {

namespace {

// 获取 %LOCALAPPDATA% 路径（SHGetKnownFolderPath，替代已废弃的 CSIDL API）
std::wstring GetLocalAppData() {
    PWSTR path = nullptr;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &path))) {
        std::wstring result(path ? path : L"");
        ::CoTaskMemFree(path);
        return result;
    }
    // 回退：用环境变量
    wchar_t buf[MAX_PATH + 1] = {};
    DWORD len = GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH);
    if (len > 0 && len <= MAX_PATH) {
        return std::wstring(buf);
    }
    return L"";
}

// WideString → UTF-8（日志用；非 ASCII 目录名不乱码）
std::string WideToUtf8(const std::wstring& ws) {
    if (ws.empty()) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, ws.c_str(),
                                  static_cast<int>(ws.size()),
                                  nullptr, 0, nullptr, nullptr);
    if (len <= 0) return {};
    std::string s(static_cast<size_t>(len), '\0');
    WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), static_cast<int>(ws.size()),
                        &s[0], len, nullptr, nullptr);
    return s;
}

} // namespace

// =============================================================================
// 公共接口
// =============================================================================

std::string StartupCleanup::RunAll(std::shared_ptr<ILogger> logger) {
    int cleaned_dirs = CleanupSessionDirs(logger);
    int cleaned_sessions = CleanupEtwSessions(logger);

    std::string summary = std::format(
        "StartupCleanup: {} session dir(s) cleaned, {} ETW session(s) stopped",
        cleaned_dirs, cleaned_sessions);
    logger->Log(LogLevel::Info, summary);
    return summary;
}

// =============================================================================
// 会话目录清理
// =============================================================================

int StartupCleanup::CleanupSessionDirs(std::shared_ptr<ILogger> logger) {
    std::wstring local_appdata = GetLocalAppData();
    if (local_appdata.empty()) {
        logger->Log(LogLevel::Warn,
            "StartupCleanup: Cannot determine LOCALAPPDATA, skipping session dir cleanup");
        return 0;
    }

    std::wstring sessions_root = local_appdata + L"\\win-sandbox\\sessions";
    logger->Log(LogLevel::Debug,
        std::format("StartupCleanup: Scanning for orphaned session dirs: {}",
                    WideToUtf8(sessions_root)));

    WIN32_FIND_DATAW ffd = {};
    HANDLE hFind = FindFirstFileW((sessions_root + L"\\*").c_str(), &ffd);
    if (hFind == INVALID_HANDLE_VALUE) {
        // 没有 sessions 根目录 = 无残留，不是错误
        logger->Log(LogLevel::Debug, "StartupCleanup: No orphaned session dirs found");
        return 0;
    }

    const unsigned long current_pid = GetCurrentProcessId();
    int cleaned = 0;
    do {
        if (!(ffd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
            continue;  // 跳过非目录项
        }
        if (wcscmp(ffd.cFileName, L".") == 0 || wcscmp(ffd.cFileName, L"..") == 0) {
            continue;
        }

        std::wstring dir_name_w(ffd.cFileName);
        unsigned long os_pid = 0;
        if (!IsSessionDirName(dir_name_w, os_pid)) {
            continue;  // 非本沙箱命名格式（如用户自建目录），不触碰
        }
        if (os_pid == current_pid) {
            // 当前进程的会话：可能有活跃子进程，交由 WriteArea Teardown 正常管理
            continue;
        }

        // 宿主进程存活检查：其他进程可能仍在运行（多进程并发使用本库），
        // 删除其活跃会话目录 = 数据丢失。仅删除进程已不存在/已退出的残留。
        bool owner_alive = false;
        HANDLE h_proc = ::OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, os_pid);
        if (h_proc) {
            DWORD exit_code = 0;
            owner_alive = ::GetExitCodeProcess(h_proc, &exit_code) &&
                          exit_code == STILL_ACTIVE;
            ::CloseHandle(h_proc);
        }
        if (owner_alive) {
            logger->Log(LogLevel::Debug,
                std::format("StartupCleanup: skip session dir (owner pid={} still alive): {}",
                            os_pid, WideToUtf8(dir_name_w)));
            continue;
        }
        // OpenProcess 失败（进程不存在/已退出/权限不足）→ 视为残留删除

        std::wstring full = sessions_root + L"\\" + dir_name_w;
        logger->Log(LogLevel::Info,
            std::format("StartupCleanup: Deleting orphaned session dir: {}",
                        WideToUtf8(dir_name_w)));
        // 递归删除（含 writable 及其内容）
        std::error_code ec;
        std::filesystem::remove_all(full, ec);
        if (ec) {
            logger->Log(LogLevel::Warn,
                std::format("StartupCleanup: remove_all failed for session dir {}: {}",
                            WideToUtf8(dir_name_w), ec.message()));
        } else {
            logger->Log(LogLevel::Info,
                std::format("StartupCleanup: Deleted session dir: {}",
                            WideToUtf8(dir_name_w)));
            ++cleaned;
        }
    } while (FindNextFileW(hFind, &ffd) != 0);

    FindClose(hFind);

    if (cleaned == 0) {
        logger->Log(LogLevel::Debug, "StartupCleanup: No orphaned session dirs to clean");
    }

    return cleaned;
}

// =============================================================================
// ETW Session 清理
// =============================================================================

int StartupCleanup::CleanupEtwSessions(std::shared_ptr<ILogger> logger) {
    // QueryAllTracesW 使用 PEVENT_TRACE_PROPERTIES 指针数组
    // 第一步：用 1 个 element 查询总大小
    ULONG session_count = 0;
    ULONG buf_size = sizeof(EVENT_TRACE_PROPERTIES) + 1024 * sizeof(wchar_t);

    std::vector<BYTE> buf(buf_size);
    auto* props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(buf.data());
    props->Wnode.BufferSize = static_cast<ULONG>(buf.size());
    props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
    props->LogFileNameOffset = 0;

    PEVENT_TRACE_PROPERTIES props_array[1] = { props };
    ULONG status = QueryAllTracesW(props_array, 1, &session_count);
    if (status == ERROR_INSUFFICIENT_BUFFER || status == ERROR_MORE_DATA) {
        // buffer 不够（MORE_DATA 也可能返回部分计数），逐条扩容重试
        ULONG needed_buf_size = buf_size;
        int grow_attempts = 0;
        while ((status == ERROR_INSUFFICIENT_BUFFER || status == ERROR_MORE_DATA) &&
               grow_attempts < 64) {
            needed_buf_size += sizeof(EVENT_TRACE_PROPERTIES) + 1024 * sizeof(wchar_t);
            buf.resize(needed_buf_size);
            props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(buf.data());
            props->Wnode.BufferSize = static_cast<ULONG>(buf.size());
            props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
            props->LogFileNameOffset = 0;
            props_array[0] = props;
            ULONG temp_count = 0;
            status = QueryAllTracesW(props_array, 1, &temp_count);
            ++grow_attempts;
            if (temp_count > 0) {
                session_count = temp_count;
                break;
            }
        }
    }

    if (status != ERROR_SUCCESS && status != ERROR_MORE_DATA) {
        logger->Log(LogLevel::Warn,
            std::format("StartupCleanup: QueryAllTracesW failed: {}", status));
        return 0;
    }

    // 如果返回了大于 0 的 session_count，但实际只有 1 个 buffer，
    // 需要用 session_count 个 buffer 重新查询才能获取所有 session 信息
    if (session_count > 1) {
        buf_size = (sizeof(EVENT_TRACE_PROPERTIES) + 1024 * sizeof(wchar_t)) * session_count;
        buf.resize(buf_size);
        props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(buf.data());
        props->Wnode.BufferSize = static_cast<ULONG>(buf.size());
        props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
        props->LogFileNameOffset = 0;

        // 构建指针数组：每个 session 一个 EVENT_TRACE_PROPERTIES
        std::vector<PEVENT_TRACE_PROPERTIES> prop_ptrs(session_count);
        for (ULONG i = 0; i < session_count; ++i) {
            auto* entry = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(
                buf.data() + i * (sizeof(EVENT_TRACE_PROPERTIES) + 1024 * sizeof(wchar_t)));
            entry->Wnode.BufferSize = sizeof(EVENT_TRACE_PROPERTIES) + 1024 * sizeof(wchar_t);
            entry->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
            entry->LogFileNameOffset = 0;
            prop_ptrs[i] = entry;
        }

        status = QueryAllTracesW(prop_ptrs.data(), session_count, &session_count);
        if (status != ERROR_SUCCESS && status != ERROR_MORE_DATA) {
            logger->Log(LogLevel::Warn,
                std::format("StartupCleanup: QueryAllTracesW (multi) failed: {}", status));
            return 0;
        }

        // 用第一个 buffer 的 props 来遍历
        props = prop_ptrs[0];
    }

    logger->Log(LogLevel::Debug,
        std::format("StartupCleanup: Found {} total ETW sessions", session_count));

    int cleaned = 0;
    for (ULONG i = 0; i < session_count; ++i) {
        // 计算当前 session 的 props 指针
        auto* cur_props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(
            buf.data() + i * (sizeof(EVENT_TRACE_PROPERTIES) + 1024 * sizeof(wchar_t)));

        wchar_t* name_ptr = reinterpret_cast<wchar_t*>(
            reinterpret_cast<BYTE*>(cur_props) + cur_props->LoggerNameOffset);
        std::wstring session_name_w(name_ptr);
        std::string session_name = WideToUtf8(session_name_w);

        // 只清理以 win-sandbox-etw- 开头的 session
        if (session_name.find("win-sandbox-etw-") != 0) {
            continue;
        }

        logger->Log(LogLevel::Info,
            std::format("StartupCleanup: Stopping orphaned ETW session: {}", session_name));

        // 准备停止参数
        ULONG stop_size = sizeof(EVENT_TRACE_PROPERTIES) +
                          (session_name_w.size() + 1) * sizeof(wchar_t);
        std::vector<BYTE> stop_buf(stop_size);
        auto* stop_props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(stop_buf.data());
        stop_props->Wnode.BufferSize = stop_size;
        stop_props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
        memcpy(reinterpret_cast<BYTE*>(stop_props) + stop_props->LoggerNameOffset,
               session_name_w.c_str(), (session_name_w.size() + 1) * sizeof(wchar_t));

        ULONG stop_status = ControlTraceW(0, session_name_w.c_str(), stop_props,
                                           EVENT_TRACE_CONTROL_STOP);
        if (stop_status == ERROR_SUCCESS) {
            logger->Log(LogLevel::Info,
                std::format("StartupCleanup: Stopped ETW session: {}", session_name));
            ++cleaned;
        } else {
            logger->Log(LogLevel::Warn,
                std::format("StartupCleanup: ControlTraceW(STOP) failed for {}: {}",
                            session_name, stop_status));
        }
    }

    if (cleaned == 0) {
        logger->Log(LogLevel::Debug, "StartupCleanup: No orphaned ETW sessions to clean");
    }

    return cleaned;
}

// =============================================================================
// 辅助方法
// =============================================================================

bool StartupCleanup::IsSessionDirName(const std::wstring& name, unsigned long& os_pid) {
    // 格式：<os-pid>-<process_id>（如 3088-4242）
    const size_t dash = name.find(L'-');
    if (dash == std::wstring::npos || dash == 0 || dash + 1 >= name.size()) {
        return false;
    }
    std::wstring pid_part = name.substr(0, dash);
    std::wstring rest = name.substr(dash + 1);
    // 两侧都必须纯数字
    auto is_digits = [](const std::wstring& s) {
        return !s.empty() && std::all_of(s.begin(), s.end(),
                                         [](wchar_t c) { return c >= L'0' && c <= L'9'; });
    };
    if (!is_digits(pid_part) || !is_digits(rest)) {
        return false;
    }
    try {
        os_pid = std::stoul(pid_part);
        return true;
    } catch (...) {
        return false;
    }
}

} // namespace winsandbox