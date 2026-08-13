// =============================================================================
// WfpEngineImpl 实现 — SOCKS5 代理方案
//
// 启动本地 SOCKS5 代理，按白名单规则转发/拒绝连接。
// SOCKS5 协议仅实现 CONNECT 命令（不支持 BIND/UDP ASSOCIATE）。
// =============================================================================

#include "infra/wfp/WfpEngineImpl.hpp"
#include "core/entities/Result.hpp"

#include <spdlog/spdlog.h>

#include <winsock2.h>
#include <ws2tcpip.h>

#pragma comment(lib, "ws2_32.lib")

#include <format>
#include <mutex>
#include <vector>

namespace winsandbox {

// =============================================================================
// SocksProxyServer - 简易 SOCKS5 代理服务器
//
// 监听 127.0.0.1:port，接受 SOCKS5 CONNECT 请求，按白名单转发/拒绝。
// 仅支持 CONNECT 命令，不支持 BIND/UDP ASSOCIATE。
// 无认证（METHOD 0x00）。
// =============================================================================
class SocksProxyServer {
public:
    SocksProxyServer(std::shared_ptr<ILogger> logger,
                     const std::vector<NetworkRule>& allowlist,
                     NetworkBlockedCallback on_blocked,
                     uint16_t listen_port)
        : logger_(std::move(logger))
        , allowlist_(allowlist)
        , on_blocked_(std::move(on_blocked))
        , listen_port_(listen_port) {
    }

    // 运行代理服务器（阻塞，在独立线程中调用）
    void Run() {
        WSADATA wsa{};
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
            logger_->Log(LogLevel::Error, "SocksProxy: WSAStartup failed");
            return;
        }

        SOCKET listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listen_sock == INVALID_SOCKET) {
            logger_->Log(LogLevel::Error, "SocksProxy: socket() failed");
            WSACleanup();
            return;
        }

        // 允许地址复用
        int opt = 1;
        setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR,
                   reinterpret_cast<const char*>(&opt), sizeof(opt));

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(listen_port_);

        if (bind(listen_sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
            logger_->Log(LogLevel::Error,
                         std::format("SocksProxy: bind() failed: {}", WSAGetLastError()));
            closesocket(listen_sock);
            WSACleanup();
            return;
        }

        if (listen(listen_sock, 5) == SOCKET_ERROR) {
            logger_->Log(LogLevel::Error, "SocksProxy: listen() failed");
            closesocket(listen_sock);
            WSACleanup();
            return;
        }

        logger_->Log(LogLevel::Info,
                     std::format("SocksProxy: listening on 127.0.0.1:{}", listen_port_));

        while (running_.load()) {
            // 非阻塞 accept：设置超时
            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(listen_sock, &readfds);
            timeval tv{1, 0};  // 1s 超时
            int sel = select(0, &readfds, nullptr, nullptr, &tv);
            if (sel == SOCKET_ERROR || sel == 0) continue;

            sockaddr_in client_addr{};
            int client_len = sizeof(client_addr);
            SOCKET client = accept(listen_sock,
                                   reinterpret_cast<sockaddr*>(&client_addr), &client_len);
            if (client == INVALID_SOCKET) continue;

            // 处理客户端连接（简单同步处理，生产环境应用线程池）
            HandleClient(client);
        }

        closesocket(listen_sock);
        WSACleanup();
        logger_->Log(LogLevel::Info, "SocksProxy: stopped");
    }

    void Stop() { running_.store(false); }

private:
    void HandleClient(SOCKET client) {
        // SOCKS5 握手：客户端发送 [VER, NMETHODS, METHODS...]
        // 我们只支持 METHOD 0x00（无认证）
        uint8_t buf[256]{};

        // 读取版本和方法选择
        int n = RecvExact(client, buf, 2);
        if (n <= 0 || buf[0] != 0x05) {
            closesocket(client);
            return;
        }
        uint8_t nmethods = buf[1];
        if (nmethods > 0) {
            RecvExact(client, buf, nmethods);
        }

        // 回复：选择 METHOD 0x00
        uint8_t reply[] = {0x05, 0x00};
        send(client, reinterpret_cast<const char*>(reply), 2, 0);

        // 读取 CONNECT 请求
        n = RecvExact(client, buf, 4);
        if (n <= 0 || buf[0] != 0x05 || buf[1] != 0x01) {
            // 仅支持 CONNECT (0x01)
            uint8_t err_reply[] = {0x05, 0x07, 0x00, 0x01, 0,0,0,0, 0,0};
            send(client, reinterpret_cast<const char*>(err_reply), 10, 0);
            closesocket(client);
            return;
        }
        // buf[2] = RSV, buf[3] = ATYP

        std::string target_ip;
        uint16_t target_port = 0;

        // 解析目标地址
        switch (buf[3]) {
            case 0x01: {
                // IPv4: 4 bytes
                uint8_t ip4[4];
                if (RecvExact(client, ip4, 4) <= 0) { closesocket(client); return; }
                target_ip = std::format("{}.{}.{}.{}", ip4[0], ip4[1], ip4[2], ip4[3]);
                break;
            }
            case 0x03: {
                // 域名: 1 byte length + domain
                uint8_t domain_len;
                if (RecvExact(client, &domain_len, 1) <= 0) { closesocket(client); return; }
                char domain[256]{};
                if (RecvExact(client, reinterpret_cast<uint8_t*>(domain), domain_len) <= 0) {
                    closesocket(client); return;
                }
                // 解析域名到 IP
                addrinfo hints{}, *result = nullptr;
                hints.ai_family = AF_INET;
                hints.ai_socktype = SOCK_STREAM;
                if (getaddrinfo(domain, nullptr, &hints, &result) != 0 || result == nullptr) {
                    SendConnectReply(client, 0x04);  // host unreachable
                    closesocket(client);
                    return;
                }
                char ip_str[INET_ADDRSTRLEN]{};
                sockaddr_in* sa = reinterpret_cast<sockaddr_in*>(result->ai_addr);
                inet_ntop(AF_INET, &sa->sin_addr, ip_str, sizeof(ip_str));
                target_ip = ip_str;
                freeaddrinfo(result);
                break;
            }
            case 0x04: {
                // IPv6: 16 bytes
                uint8_t ip6[16];
                if (RecvExact(client, ip6, 16) <= 0) { closesocket(client); return; }
                char ip_str[INET6_ADDRSTRLEN]{};
                inet_ntop(AF_INET6, ip6, ip_str, sizeof(ip_str));
                target_ip = ip_str;
                break;
            }
            default: {
                SendConnectReply(client, 0x08);  // address type not supported
                closesocket(client);
                return;
            }
        }

        // 读取目标端口（2 bytes, network byte order）
        uint8_t port_buf[2];
        if (RecvExact(client, port_buf, 2) <= 0) { closesocket(client); return; }
        target_port = (static_cast<uint16_t>(port_buf[0]) << 8) | port_buf[1];

        // 查白名单
        bool allowed = false;
        for (const auto& rule : allowlist_) {
            if (!rule.ip.empty() && rule.ip != target_ip) continue;
            if (rule.port != 0 && rule.port != target_port) continue;
            if (rule.protocol != 0 && rule.protocol != NetworkRule::kTcp) continue;
            allowed = true;
            break;
        }

        if (!allowed) {
            // 拒绝连接
            SendConnectReply(client, 0x02);  // connection not allowed
            logger_->Log(LogLevel::Info,
                         std::format("SocksProxy: BLOCKED {}:{} (not in allowlist)",
                                     target_ip, target_port));
            if (on_blocked_) {
                on_blocked_(target_ip, target_port, NetworkRule::kTcp, "not_in_allowlist");
            }
            closesocket(client);
            return;
        }

        // 连接目标
        SOCKET remote = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (remote == INVALID_SOCKET) {
            SendConnectReply(client, 0x05);  // connection refused
            closesocket(client);
            return;
        }

        sockaddr_in remote_addr{};
        remote_addr.sin_family = AF_INET;
        remote_addr.sin_port = htons(target_port);
        inet_pton(AF_INET, target_ip.c_str(), &remote_addr.sin_addr);

        if (connect(remote, reinterpret_cast<sockaddr*>(&remote_addr), sizeof(remote_addr)) == SOCKET_ERROR) {
            SendConnectReply(client, 0x05);  // connection refused
            closesocket(remote);
            closesocket(client);
            return;
        }

        // 成功：回复 SOCKS5 连接成功
        uint8_t success_reply[] = {0x05, 0x00, 0x00, 0x01, 0,0,0,0, 0,0};
        send(client, reinterpret_cast<const char*>(success_reply), 10, 0);

        logger_->Log(LogLevel::Debug,
                     std::format("SocksProxy: CONNECTED {}:{} (in allowlist)",
                                 target_ip, target_port));

        // 双向转发（简单 select 模型）
        Relay(client, remote);

        closesocket(remote);
        closesocket(client);
    }

    void SendConnectReply(SOCKET s, uint8_t status) {
        uint8_t reply[] = {0x05, status, 0x00, 0x01, 0,0,0,0, 0,0};
        send(s, reinterpret_cast<const char*>(reply), 10, 0);
    }

    void Relay(SOCKET client, SOCKET remote) {
        const int BUF_SIZE = 8192;
        char buf[BUF_SIZE];

        while (running_.load()) {
            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(client, &readfds);
            FD_SET(remote, &readfds);
            timeval tv{1, 0};

            int sel = select(0, &readfds, nullptr, nullptr, &tv);
            if (sel == SOCKET_ERROR) break;
            if (sel == 0) continue;

            if (FD_ISSET(client, &readfds)) {
                int n = recv(client, buf, BUF_SIZE, 0);
                if (n <= 0) break;
                if (send(remote, buf, n, 0) <= 0) break;
            }

            if (FD_ISSET(remote, &readfds)) {
                int n = recv(remote, buf, BUF_SIZE, 0);
                if (n <= 0) break;
                if (send(client, buf, n, 0) <= 0) break;
            }
        }
    }

    static int RecvExact(SOCKET s, uint8_t* buf, int len) {
        int total = 0;
        while (total < len) {
            int n = recv(s, reinterpret_cast<char*>(buf + total), len - total, 0);
            if (n <= 0) return n;
            total += n;
        }
        return total;
    }

    std::shared_ptr<ILogger> logger_;
    const std::vector<NetworkRule>& allowlist_;
    NetworkBlockedCallback on_blocked_;
    uint16_t listen_port_;
    std::atomic<bool> running_{true};
};

// =============================================================================
// WfpEngineImpl 实现
// =============================================================================

WfpEngineImpl::WfpEngineImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger)) {
}

WfpEngineImpl::~WfpEngineImpl() {
    Close();
}

Result<void> WfpEngineImpl::Open() {
    std::lock_guard lock(mutex_);
    if (running_.load()) {
        return Result<void>::Err(ErrorCode::InternalError, "WFP engine already open");
    }

    // 选择随机可用端口
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        return Result<void>::Err(ErrorCode::InternalError, "WSAStartup failed");
    }

    SOCKET test_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (test_sock == INVALID_SOCKET) {
        WSACleanup();
        return Result<void>::Err(ErrorCode::InternalError, "socket() failed");
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;  // 让系统分配端口

    if (bind(test_sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
        closesocket(test_sock);
        WSACleanup();
        return Result<void>::Err(ErrorCode::InternalError, "bind() failed for proxy port");
    }

    int addr_len = sizeof(addr);
    getsockname(test_sock, reinterpret_cast<sockaddr*>(&addr), &addr_len);
    uint16_t port = ntohs(addr.sin_port);
    closesocket(test_sock);
    WSACleanup();

    proxy_port_.store(port);
    running_.store(true);

    logger_->Log(LogLevel::Info, std::format("WFP engine opened (SOCKS5 proxy port={})", port));
    return Result<void>::Ok();
}

Result<void> WfpEngineImpl::RegisterConnectFilter(
    const std::vector<NetworkRule>& allowlist,
    NetworkBlockedCallback on_blocked,
    uint64_t instance_id) {
    std::lock_guard lock(mutex_);
    if (!running_.load()) {
        return Result<void>::Err(ErrorCode::InternalError, "WFP engine not open");
    }

    allowlist_ = allowlist;
    on_blocked_ = std::move(on_blocked);

    // 启动 SOCKS5 代理服务器（独立线程）
    auto port = proxy_port_.load();
    proxy_ = std::make_unique<SocksProxyServer>(logger_, allowlist_, on_blocked_, port);
    proxy_thread_ = std::thread([this] { proxy_->Run(); });

    logger_->Log(LogLevel::Info,
                 std::format("SOCKS5 proxy started: allowlist_size={} instance_id={} port={}",
                             allowlist_.size(), instance_id, port));
    return Result<void>::Ok();
}

Result<void> WfpEngineImpl::UnregisterAll() {
    std::lock_guard lock(mutex_);
    if (proxy_) {
        proxy_->Stop();
    }
    if (proxy_thread_.joinable()) {
        proxy_thread_.join();
    }
    proxy_.reset();
    logger_->Log(LogLevel::Info, "SOCKS5 proxy stopped");
    return Result<void>::Ok();
}

Result<void> WfpEngineImpl::Close() {
    UnregisterAll();
    running_.store(false);
    proxy_port_.store(0);
    logger_->Log(LogLevel::Info, "WFP engine closed");
    return Result<void>::Ok();
}

bool WfpEngineImpl::IsOpen() const {
    return running_.load();
}

} // namespace winsandbox
