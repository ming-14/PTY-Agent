// =============================================================================
// IsolationPolicy - 隔离策略实体（core 层）
//
// Low IL Token + Job 纯用户态隔离模型。
//
// 文件系统语义固定（无配置项）：
//   - 全盘只读：进程 token IL=Low(4096)，NO_WRITE_UP 强制写任何 Medium 对象被拒
//   - 自动可写区：每进程创建 %LOCALAPPDATA%\win-sandbox\sessions\...\writable，
//     打 Low 标签并重定向 %TEMP%
//
// 网络策略收敛为 unrestricted | allowlist：
//   none/loopback_only/outbound 无法在纯用户态下系统级执行（WFP 系统过滤需
//   管理员），解析层对非法值显式拒绝，杜绝"声明了限制却不生效"的静默失败。
// =============================================================================

#pragma once

#include "core/entities/NetworkRule.hpp"  // NetworkRule（allowlist 规则）

#include <cstdint>
#include <vector>

namespace winsandbox {

// ----- 网络策略 -----
enum class NetworkPolicy {
    Unrestricted,  // 不限制（用户 token 天然全通）
    Allowlist,     // SOCKS5 代理白名单（HTTP/HTTPS 走代理，非 HTTP 流量不受控）
};

// ----- 隔离策略 -----
// 由 ConfigLoader 从 JSON 解析，或由 start_process 参数携带（bindings 解析）
// NativeSandboxedProcess 消费此对象执行隔离准备
struct IsolationPolicy {
    // 网络策略（默认不限制：web 终端主用途；限制语义见枚举注释）
    NetworkPolicy net_policy = NetworkPolicy::Unrestricted;

    // SOCKS5 白名单规则（仅 net_policy=Allowlist 时生效）
    std::vector<NetworkRule> net_allowlist;

    // Job UI 限制开关：剪贴板读写/全局原子表/系统参数设置
    // true  → 调用 SetUiLimits(true)（JOB_OBJECT_UILIMIT_* 组合）
    // false（默认）→ 不限制（web 终端 Ctrl+C/V 体验不受影响）
    bool clipboard_isolate = false;
};

} // namespace winsandbox
