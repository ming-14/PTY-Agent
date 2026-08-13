// =============================================================================
// IWfpEngine - 网络白名单引擎端口接口（core 层）
//
// 实现（infra/wfp/WfpEngineImpl）：本地 SOCKS5 代理方案。
// WFP 用户态 ALE callout 回调需要内核驱动（用户态 API 仅提供管理功能），
// 因此白名单用"本地 SOCKS5 代理 + 子进程代理环境变量"实现：
//
// 生命周期：
//   1. Open() — 启动本地 SOCKS5 代理监听（127.0.0.1:随机端口，bind 失败即报错）
//   2. RegisterConnectFilter() — 绑定白名单规则并启动代理线程
//   3. 运行期：代理按白名单匹配，命中→转发，未命中→拒绝 + NetworkBlocked 事件
//   4. UnregisterAll() — 停止代理
//   5. Close() — 关闭引擎
//
// 限制：
//   - 仅代理走环境变量（HTTP_PROXY/HTTPS_PROXY/ALL_PROXY）的流量
//   - 非 HTTP 流量（原始 TCP/UDP）不受代理控制
//   - 无需管理员权限
//
// 供 NativeSandboxedProcess 在 net_policy=Allowlist 时调用并注入代理环境变量
// =============================================================================

#pragma once

#include "core/entities/NetworkRule.hpp"
#include "core/entities/Result.hpp"

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace winsandbox {

// 网络拦截事件回调（代理工作线程中调用，通知上层有连接被拦截）
// 参数：ip, port, protocol, reason
using NetworkBlockedCallback = std::function<void(
    const std::string& ip, uint16_t port, uint8_t protocol, const std::string& reason)>;

class IWfpEngine {
public:
    virtual ~IWfpEngine() = default;

    // 启动 SOCKS5 代理监听（127.0.0.1:随机端口）
    // 绑定/监听失败立即返回 Err（不静默失效）
    virtual Result<void> Open() = 0;

    // 注册白名单并启动代理线程
    // allowlist: 白名单规则（空=全部拒绝；ip 空=任意 IP，port 0=任意端口，
    //            protocol 0 或 kTcp=TCP）
    // on_blocked: 拦截回调（在代理工作线程中调用，必须快速返回）
    // instance_id: 实例标识（仅日志用途）
    virtual Result<void> RegisterConnectFilter(
        const std::vector<NetworkRule>& allowlist,
        NetworkBlockedCallback on_blocked,
        uint64_t instance_id) = 0;

    // 停止代理并 join 所有工作线程（重复调用安全）
    // 必须在 Close 前调用，否则代理端口残留
    virtual Result<void> UnregisterAll() = 0;

    // 关闭引擎（与 Open 的 WSAStartup 配对）
    virtual Result<void> Close() = 0;

    // 引擎是否已打开
    virtual bool IsOpen() const = 0;

    // SOCKS5 代理监听端口（0 = 无代理）
    // allowlist 模式由 NativeSandboxedProcess 注入子进程代理环境变量
    virtual uint16_t ProxyPort() const = 0;
};

} // namespace winsandbox
