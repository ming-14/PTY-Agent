// =============================================================================
// StartupCleanup - 启动时残留资源清理
//
// 职责：
//   1. 扫描并清理残留的沙箱会话目录（%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>）
//   2. 扫描并停止残留的 ETW session（win-sandbox-etw-*）
//
// 设计要点：
//   - 所有方法为静态，无状态，幂等
//   - 清理失败不阻塞启动，仅记录日志
//   - 只清理 win-sandbox 自有命名空间的资源，避免误删其他应用
// =============================================================================
#pragma once

#include "core/ports/ILogger.hpp"

#include <memory>
#include <string>

namespace winsandbox {

class StartupCleanup {
public:
    // 执行全部清理操作（会话目录 + ETW session）
    // 返回清理摘要（供日志记录）
    static std::string RunAll(std::shared_ptr<ILogger> logger);

    // 清理残留的沙箱会话目录
    // 枚举 %LOCALAPPDATA%\win-sandbox\sessions\ 下的 <os-pid>-<process_id> 子目录，
    // 删除不属于当前进程（os-pid != GetCurrentProcessId()）的残留（Teardown 失败兜底）
    static int CleanupSessionDirs(std::shared_ptr<ILogger> logger);

    // 清理残留的 ETW session
    // 使用 QueryAllTracesW 查找 win-sandbox-etw-* session 并停止
    static int CleanupEtwSessions(std::shared_ptr<ILogger> logger);

private:
    // 校验目录名格式：<os-pid>-<process_id>（均为正整数）
    static bool IsSessionDirName(const std::wstring& name, unsigned long& os_pid);
};

} // namespace winsandbox