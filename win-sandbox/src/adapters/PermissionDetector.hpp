// =============================================================================
// PermissionDetector - 权限检测适配器（adapters 层）
//
// 检测当前进程的权限级别，输出 PermissionMode。
// 生成 CapabilityReport（实际生效能力集）。
//
// 设计要点：
//   - 纯检测，不修改任何系统状态
//   - 结果缓存（同一进程内权限不会变化）
//   - core 层定义 PermissionMode/CapabilityReport，本文件实现检测逻辑
// =============================================================================
#pragma once

#include "core/entities/ErrorCode.hpp"
#include "core/ports/ILogger.hpp"

#include <mutex>
#include <string>
#include <vector>

namespace winsandbox {

// ----- 权限级别（core 层概念，此处前置声明以便同文件使用）-----

enum class PermissionMode {
    Admin,          // 管理员（TokenElevation == TRUE）
    StandardUser,   // 普通用户（TokenElevation == FALSE）
};

// 能力项：模块名 + 是否可用 + 降级原因
struct CapabilityItem {
    std::string module;         // "job_object" / "low_il_token" / "etw" / "network" / "pipe_security"
    bool available;             // 该能力是否完整可用
    std::string degraded_reason; // 不可用时原因（空 = 完整可用）
};

// CapabilityReport：启动时输出实际生效能力集
struct CapabilityReport {
    PermissionMode mode;
    std::vector<CapabilityItem> capabilities;

    // 便捷查询
    bool Has(const std::string& module) const;
    bool IsAvailable(const std::string& module) const;
    std::string ToJson() const;  // 序列化为 JSON 字符串
};

// ----- 权限检测器 -----

class PermissionDetector {
public:
    // 检测当前进程权限级别（结果缓存，线程安全：std::call_once 初始化）
    static PermissionMode Detect();

    // 是否管理员
    static bool IsAdmin();

    // 生成完整能力报告
    // 根据 PermissionMode + 系统环境检测各模块可用性
    //（"available" 表示预期可用；实际启动失败（ETW/代理）以运行时日志为准）
    static CapabilityReport BuildReport();

    // 能力报告序列化为日志友好格式
    static std::string FormatReport(const CapabilityReport& report);

private:
    // std::call_once 保护的缓存（并发首次调用无数据竞争）
    static PermissionMode cached_mode_;
    static std::once_flag cached_once_;
};

} // namespace winsandbox
