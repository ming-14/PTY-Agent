// =============================================================================
// PermissionDetector 实现
// =============================================================================
#include "adapters/PermissionDetector.hpp"

#include <windows.h>
#include <psapi.h>

#pragma comment(lib, "psapi.lib")

namespace winsandbox {

// 静态成员初始化
PermissionMode PermissionDetector::cached_mode_ = PermissionMode::StandardUser;
std::once_flag PermissionDetector::cached_once_;

// ----------------------------------------------------------------------------- 
// CapabilityReport 方法
// ----------------------------------------------------------------------------- 

bool CapabilityReport::Has(const std::string& module) const {
    for (const auto& c : capabilities) {
        if (c.module == module) return true;
    }
    return false;
}

bool CapabilityReport::IsAvailable(const std::string& module) const {
    for (const auto& c : capabilities) {
        if (c.module == module) return c.available;
    }
    return false;
}

std::string CapabilityReport::ToJson() const {
    std::string mode_str = (mode == PermissionMode::Admin) ? "admin" : "standard_user";

    std::string caps = "[";
    for (size_t i = 0; i < capabilities.size(); ++i) {
        if (i > 0) caps += ",";
        caps += "{\"module\":\"" + capabilities[i].module + "\"";
        caps += ",\"available\":" + std::string(capabilities[i].available ? "true" : "false");
        if (!capabilities[i].degraded_reason.empty()) {
            caps += ",\"degraded_reason\":\"" + capabilities[i].degraded_reason + "\"";
        }
        caps += "}";
    }
    caps += "]";

    return "{\"mode\":\"" + mode_str + "\",\"capabilities\":" + caps + "}";
}

// ----------------------------------------------------------------------------- 
// PermissionDetector 方法
// ----------------------------------------------------------------------------- 

PermissionMode PermissionDetector::Detect() {
    // std::call_once 线程安全初始化（并发首次调用无数据竞争）
    std::call_once(cached_once_, [] {
        HANDLE token = nullptr;
        if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
            cached_mode_ = PermissionMode::StandardUser;
            return;
        }
        TOKEN_ELEVATION elev = {};
        DWORD ret_len = 0;
        BOOL ok = GetTokenInformation(token, TokenElevation, &elev, sizeof(elev), &ret_len);
        CloseHandle(token);
        cached_mode_ = (ok && elev.TokenIsElevated != 0)
                           ? PermissionMode::Admin
                           : PermissionMode::StandardUser;
    });
    return cached_mode_;
}

bool PermissionDetector::IsAdmin() {
    return Detect() == PermissionMode::Admin;
}

CapabilityReport PermissionDetector::BuildReport() {
    PermissionMode mode = Detect();
    CapabilityReport report;
    report.mode = mode;

    bool is_admin = (mode == PermissionMode::Admin);

    // Job Object：始终可用（Job Object 不需要管理员权限）
    // 但 CPU rate control 在非管理员下可能受限
    report.capabilities.push_back({
        "job_object", true, ""
    });

    // Low IL 隔离 token：纯用户态（DuplicateTokenEx + SetTokenInformation
    // IL=Low + SetNamedSecurityInfo 打标），非管理员可用，始终可用
    report.capabilities.push_back({
        "low_il_token", true, ""
    });

    // ETW 行为监控：管理员模式可用真 ETW，非管理员降级为轮询+目录监控+网络轮询
    if (is_admin) {
        report.capabilities.push_back({
            "etw", true, ""
        });
    } else {
        report.capabilities.push_back({
            "etw", false,
            "non-admin: degraded to process polling + dir file watch + network polling "
            "(no registry events)"
        });
    }

    // 网络限制（allowlist/SOCKS5 走 WFP callout）：需要管理员；非管理员仅能 unrestricted
    if (is_admin) {
        report.capabilities.push_back({
            "network", true, ""
        });
    } else {
        report.capabilities.push_back({
            "network", false,
            "non-admin: WFP connect filter unavailable, only net_policy=unrestricted"
        });
    }

    // 管道 DACL 保护：始终可用（CreateNamedPipe + SetSecurityDescriptor）
    report.capabilities.push_back({
        "pipe_security", true, ""
    });

    return report;
}

std::string PermissionDetector::FormatReport(const CapabilityReport& report) {
    std::string mode_str = (report.mode == PermissionMode::Admin)
        ? "Admin" : "StandardUser";

    std::string text = "PermissionMode: " + mode_str + "\n";
    text += "Capabilities:\n";
    for (const auto& c : report.capabilities) {
        text += "  " + c.module + ": ";
        if (c.available) {
            text += "available";
        } else {
            text += "degraded (" + c.degraded_reason + ")";
        }
        text += "\n";
    }
    return text;
}

} // namespace winsandbox
