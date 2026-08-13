// =============================================================================
// IWfpEngine - WFP 引擎端口接口（core 层）
//
// WFP（Windows Filtering Platform）用户态 ALE callout 接口。
// 注册 ALE_AUTH_CONNECT_V4/V6 filter，按白名单规则放行/拒绝网络连接。
//
// 生命周期：
//   1. Open() — 打开 WFP 引擎会话
//   2. RegisterConnectFilter() — 注册 callout + filter（绑定白名单规则）
//   3. 运行期：callout 回调按白名单匹配，命中→PERMIT，未命中→BLOCK + NetworkBlocked 事件
//   4. UnregisterAll() — 注销 callout + 删除 filter
//   5. Close() — 关闭引擎会话
//
// 前提：管理员权限（WFP 注册需要）
// 降级：非管理员时 Open() 返回 Err，调用方记 Warn 降级（allowlist 不生效，
// 进程网络不受限，语义等同 net_policy=unrestricted）
//
// Phase 5 WFP allowlist：net_policy=Allowlist 时由 StartProcessUseCase 调用
// =============================================================================

#pragma once

#include "core/entities/NetworkRule.hpp"
#include "core/entities/Result.hpp"

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace winsandbox {

// 网络拦截事件回调（WFP callout 回调中调用，通知上层有连接被拦截）
// 参数：ip, port, protocol, reason
using NetworkBlockedCallback = std::function<void(
    const std::string& ip, uint16_t port, uint8_t protocol, const std::string& reason)>;

class IWfpEngine {
public:
    virtual ~IWfpEngine() = default;

    // 打开 WFP 引擎会话
    // 需管理员权限，失败返回 Err（调用方降级处理）
    virtual Result<void> Open() = 0;

    // 注册 ALE_AUTH_CONNECT callout + filter
    // allowlist: 白名单规则（空=全部拒绝）
    // on_blocked: 拦截回调（在 callout 回调线程中调用，必须快速返回）
    // instance_id: 沙箱实例 ID（用于 WFP 会话名/filter 名，避免多实例冲突）
    virtual Result<void> RegisterConnectFilter(
        const std::vector<NetworkRule>& allowlist,
        NetworkBlockedCallback on_blocked,
        uint64_t instance_id) = 0;

    // 注销所有 callout + 删除所有 filter
    // 必须在 Close 前调用，否则 filter 残留导致网络持续被拦截
    virtual Result<void> UnregisterAll() = 0;

    // 关闭 WFP 引擎会话
    virtual Result<void> Close() = 0;

    // 引擎是否已打开
    virtual bool IsOpen() const = 0;

    // HIGH-3 修复（r5）：返回 SOCKS5 代理监听端口（0 = 无代理，如 WFP callout
    //   实现）。allowlist 模式由 StartProcessUseCase 注入子进程代理环境变量。
    virtual uint16_t ProxyPort() const = 0;
};

} // namespace winsandbox
