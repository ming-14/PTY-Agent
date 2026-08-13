// =============================================================================
// WfpEngineImpl - 网络白名单引擎实现（infra 层）
//
// WFP 用户态 ALE callout 回调需要内核驱动（用户态 API 仅提供管理功能），
// 网络白名单采用 SOCKS5 代理方案：
//
//   1. Open() — 启动本地 SOCKS5 代理服务器（监听 127.0.0.1:随机端口）
//   2. 代理按白名单规则转发/拒绝连接
//   3. 被隔离进程通过环境变量 HTTP_PROXY/HTTPS_PROXY 使用代理
//   4. 拦截时发送 NetworkBlocked 事件
//
// 限制：
//   - 仅代理 HTTP/HTTPS 流量（通过环境变量）
//   - 非 HTTP 流量（原始 TCP/UDP）不受代理控制
//   - 无 AppContainer：代理仅拦截走环境变量的 HTTP/HTTPS，其余流量 = 用户 token 天然语义
// =============================================================================

#pragma once

#include "core/ports/IWfpEngine.hpp"
#include "core/ports/ILogger.hpp"

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace winsandbox {

class SocksProxyServer;

class WfpEngineImpl : public IWfpEngine {
public:
    explicit WfpEngineImpl(std::shared_ptr<ILogger> logger);
    ~WfpEngineImpl() override;

    WfpEngineImpl(const WfpEngineImpl&) = delete;
    WfpEngineImpl& operator=(const WfpEngineImpl&) = delete;

    Result<void> Open() override;
    Result<void> RegisterConnectFilter(
        const std::vector<NetworkRule>& allowlist,
        NetworkBlockedCallback on_blocked,
        uint64_t instance_id) override;
    Result<void> UnregisterAll() override;
    Result<void> Close() override;
    bool IsOpen() const override;

    // 代理监听端口（Open 后可用，用于设置 HTTP_PROXY 环境变量）
    uint16_t ProxyPort() const override { return proxy_port_.load(); }

private:
    std::shared_ptr<ILogger> logger_;
    mutable std::mutex mutex_;

    std::unique_ptr<SocksProxyServer> proxy_;
    std::thread proxy_thread_;
    std::atomic<uint16_t> proxy_port_{0};
    std::atomic<bool> running_{false};

    // Open() 中创建并绑定的监听 socket（SOCKET 值；注册到代理后移交，
    // 置 0 避免重复关闭）。以 uintptr_t 存储避免头文件依赖 winsock2。
    uintptr_t listen_sock_ = 0;
};

} // namespace winsandbox
