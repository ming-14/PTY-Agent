// =============================================================================
// WfpEngineImpl 实现 — SOCKS5 代理方案
//
// 启动本地 SOCKS5 代理，按白名单规则转发/拒绝连接。
// SOCKS5 协议仅实现 CONNECT 命令（不支持 BIND/UDP ASSOCIATE）。
//
// 生命周期设计（消除 TOCTOU 与静默失败）：
//   - Open()：WSAStartup + 创建/绑定/监听 127.0.0.1:0（系统分配端口），
//     失败立即返回 Err（bind 不再等到代理线程才暴露失败）
//   - RegisterConnectFilter()：把已绑定 socket 移交 SocksProxyServer 并启动
//     线程；重复注册显式拒绝（旧线程未 join 即替换会 UAF）
//   - UnregisterAll()/Close()：Stop → join 代理线程（内部再 join 各连接工作
//     线程）→ 关闭 socket
//
// 连接处理（线程池）：每个客户端连接独立线程处理，黑洞目标 connect 用
// 非阻塞 + select(10s) 超时，防单个坏连接阻塞整个代理。
// =============================================================================

#include "infra/wfp/WfpEngineImpl.hpp"
#include "core/entities/Result.hpp"

#include <spdlog/spdlog.h>

#include <winsock2.h>
#include <ws2tcpip.h>

#pragma comment(lib, "ws2_32.lib")

#include <format>
#include <mutex>
#include <thread>
#include <vector>

namespace winsandbox {

// =============================================================================
// SocksProxyServer - 简易 SOCKS5 代理服务器
//
// 持有 Open() 已绑定的监听 socket，接受 SOCKS5 CONNECT 请求，按白名单转发/拒绝。
// 仅支持 CONNECT 命令，不支持 BIND/UDP ASSOCIATE。无认证（METHOD 0x00）。
//
// 线程模型：
//   - Run()：accept 循环（代理线程）
//   - 每个客户端连接一个工作线程（并发上限 kMaxWorkers，超出拒绝新连接）
//   - Stop()：置 running_=false → Run 退出 → join 所有工作线程
// =============================================================================
class SocksProxyServer {
public:
    SocksProxyServer(std::shared_ptr<ILogger> logger,
                     std::vector<NetworkRule> allowlist,
                     NetworkBlockedCallback on_blocked,
                     uintptr_t listen_sock,
                     uint16_t listen_port)
        : logger_(std::move(logger))
        , allowlist_(std::move(allowlist))  // 值拷贝：工作线程只读自己的副本，无竞态
        , on_blocked_(std::move(on_blocked))
        , listen_sock_(static_cast<SOCKET>(listen_sock))
        , listen_port_(listen_port) {
    }

    ~SocksProxyServer() {
        // 析构兜底（正常路径 Run 已 join 全部工作线程）
        Stop();
        JoinWorkers();
        if (listen_sock_ != INVALID_SOCKET) {
            closesocket(listen_sock_);
            listen_sock_ = INVALID_SOCKET;
        }
    }

    // 运行代理服务器（阻塞，在独立线程中调用）
    void Run() {
        logger_->Log(LogLevel::Info,
                     std::format("SocksProxy: listening on 127.0.0.1:{}", listen_port_));

        while (running_.load()) {
            // 非阻塞 accept：select 1s 超时轮询停止信号
            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(listen_sock_, &readfds);
            timeval tv{1, 0};
            int sel = select(0, &readfds, nullptr, nullptr, &tv);
            if (sel == SOCKET_ERROR || sel == 0) continue;

            sockaddr_in client_addr{};
            int client_len = sizeof(client_addr);
            SOCKET client = accept(listen_sock_,
                                   reinterpret_cast<sockaddr*>(&client_addr), &client_len);
            if (client == INVALID_SOCKET) continue;

            // 并发上限：超出时拒绝新连接（防工作线程无限膨胀）
            {
                std::lock_guard<std::mutex> lk(workers_mutex_);
                if (workers_.size() >= kMaxWorkers) {
                    uint8_t busy_reply[] = {0x05, 0x05, 0x00, 0x01, 0, 0, 0, 0, 0, 0};
                    send(client, reinterpret_cast<const char*>(busy_reply),
                         static_cast<int>(sizeof(busy_reply)), 0);
                    closesocket(client);
                    continue;
                }
                workers_.emplace_back([this, client] { HandleClient(client); });
            }
        }

        closesocket(listen_sock_);
        listen_sock_ = INVALID_SOCKET;
        JoinWorkers();
        logger_->Log(LogLevel::Info, "SocksProxy: stopped");
    }

    void Stop() { running_.store(false); }

private:
    static constexpr size_t kMaxWorkers = 16;

    // 工作线程集合（仅 Run 线程写入；JoinWorkers 在 Stop 后调用）
    std::mutex workers_mutex_;
    std::vector<std::thread> workers_;

    void JoinWorkers() {
        std::vector<std::thread> to_join;
        {
            std::lock_guard<std::mutex> lk(workers_mutex_);
            to_join.swap(workers_);
        }
        for (auto& t : to_join) {
            if (t.joinable()) {
                t.join();
            }
        }
    }

    // 带超时的精确读取（2s）：对端不发数据时线程也能退出（Stop 后 join 不卡死）
    static int RecvExact(SOCKET s, uint8_t* buf, int len) {
        int total = 0;
        while (total < len) {
            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(s, &readfds);
            timeval tv{2, 0};
            int sel = select(0, &readfds, nullptr, nullptr, &tv);
            if (sel <= 0) return (sel == 0) ? 0 : -1;  // 超时/错误视为断开
            int n = recv(s, reinterpret_cast<char*>(buf + total), len - total, 0);
            if (n <= 0) return n;
            total += n;
        }
        return total;
    }

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
            if (RecvExact(client, buf, nmethods) <= 0) {
                closesocket(client);
                return;
            }
        }

        // 回复：选择 METHOD 0x00
        uint8_t reply[] = {0x05, 0x00};
        send(client, reinterpret_cast<const char*>(reply), 2, 0);

        // 读取 CONNECT 请求
        n = RecvExact(client, buf, 4);
        if (n <= 0 || buf[0] != 0x05 || buf[1] != 0x01) {
            // 仅支持 CONNECT (0x01)
            SendConnectReply(client, 0x07);
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
            case 0x04:
                // IPv6：远端 socket 为 AF_INET（仅 IPv4 转发），明确拒绝
                SendConnectReply(client, 0x08);  // address type not supported
                closesocket(client);
                return;
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

        // 查白名单（只读本线程持有的拷贝，无竞态）
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

        // 连接目标（非阻塞 + select 超时，防黑洞 IP 阻塞工作线程）
        SOCKET remote = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (remote == INVALID_SOCKET) {
            SendConnectReply(client, 0x05);  // connection refused
            closesocket(client);
            return;
        }

        sockaddr_in remote_addr{};
        remote_addr.sin_family = AF_INET;
        remote_addr.sin_port = htons(target_port);
        if (inet_pton(AF_INET, target_ip.c_str(), &remote_addr.sin_addr) != 1) {
            SendConnectReply(client, 0x05);
            closesocket(remote);
            closesocket(client);
            return;
        }

        // 非阻塞 connect + select 等待（10s 超时）
        u_long nonblocking = 1;
        ioctlsocket(remote, FIONBIO, &nonblocking);
        int rc = connect(remote, reinterpret_cast<sockaddr*>(&remote_addr),
                         sizeof(remote_addr));
        if (rc == SOCKET_ERROR && WSAGetLastError() != WSAEWOULDBLOCK) {
            SendConnectReply(client, 0x05);
            closesocket(remote);
            closesocket(client);
            return;
        }
        if (rc == SOCKET_ERROR) {
            fd_set wfds;
            FD_ZERO(&wfds);
            FD_SET(remote, &wfds);
            timeval tv{10, 0};
            int sel = select(0, nullptr, &wfds, nullptr, &tv);
            if (sel <= 0) {
                // 超时/错误：连接失败
                SendConnectReply(client, 0x05);
                closesocket(remote);
                closesocket(client);
                return;
            }
            int sock_err = 0;
            int sock_err_len = sizeof(sock_err);
            if (getsockopt(remote, SOL_SOCKET, SO_ERROR,
                           reinterpret_cast<char*>(&sock_err), &sock_err_len) == SOCKET_ERROR ||
                sock_err != 0) {
                SendConnectReply(client, 0x05);
                closesocket(remote);
                closesocket(client);
                return;
            }
        }
        // 恢复阻塞模式
        u_long blocking = 0;
        ioctlsocket(remote, FIONBIO, &blocking);

        // 成功：回复 SOCKS5 连接成功
        SendConnectReply(client, 0x00);

        logger_->Log(LogLevel::Debug,
                     std::format("SocksProxy: CONNECTED {}:{} (in allowlist)",
                                 target_ip, target_port));

        // 双向转发（select 模型，1s 超时轮询停止信号）
        Relay(client, remote);

        closesocket(remote);
        closesocket(client);
    }

    void SendConnectReply(SOCKET s, uint8_t status) {
        uint8_t reply[] = {0x05, status, 0x00, 0x01, 0, 0, 0, 0, 0, 0};
        send(s, reinterpret_cast<const char*>(reply),
             static_cast<int>(sizeof(reply)), 0);
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

    std::shared_ptr<ILogger> logger_;
    const std::vector<NetworkRule> allowlist_;   // 值拷贝（工作线程独占读取）
    NetworkBlockedCallback on_blocked_;          // 值拷贝
    SOCKET listen_sock_;
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

    // WSAStartup（Open/Close 配对；代理线程不再重复调用）
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        return Result<void>::Err(ErrorCode::InternalError, "WSAStartup failed");
    }

    SOCKET listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listen_sock == INVALID_SOCKET) {
        WSACleanup();
        return Result<void>::Err(ErrorCode::InternalError, "socket() failed");
    }

    // 不设 SO_REUSEADDR：Windows 上该选项允许两个 socket 绑定同一地址端口，
    // 已绑定的端口不应被其他进程抢占
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;  // 系统分配端口（listen 前就绑定，消除 TOCTOU）

    if (bind(listen_sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
        DWORD err = WSAGetLastError();
        closesocket(listen_sock);
        WSACleanup();
        return Result<void>::Err(
            ErrorCode::InternalError,
            std::format("bind() failed for proxy port: err={}", err));
    }

    if (listen(listen_sock, 8) == SOCKET_ERROR) {
        DWORD err = WSAGetLastError();
        closesocket(listen_sock);
        WSACleanup();
        return Result<void>::Err(
            ErrorCode::InternalError,
            std::format("listen() failed: err={}", err));
    }

    int addr_len = sizeof(addr);
    if (getsockname(listen_sock, reinterpret_cast<sockaddr*>(&addr), &addr_len)
        == SOCKET_ERROR) {
        DWORD err = WSAGetLastError();
        closesocket(listen_sock);
        WSACleanup();
        return Result<void>::Err(
            ErrorCode::InternalError,
            std::format("getsockname() failed: err={}", err));
    }

    listen_sock_ = static_cast<uintptr_t>(listen_sock);
    proxy_port_.store(ntohs(addr.sin_port));
    running_.store(true);

    logger_->Log(LogLevel::Info,
                 std::format("WFP engine opened (SOCKS5 proxy port={})",
                             proxy_port_.load()));
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
    // 重复注册防护：旧代理线程从未 join，直接替换会 UAF
    if (proxy_ || proxy_thread_.joinable()) {
        return Result<void>::Err(
            ErrorCode::InternalError, "SOCKS5 proxy already registered");
    }
    if (listen_sock_ == 0) {
        return Result<void>::Err(ErrorCode::InternalError,
                                 "WFP engine listen socket not ready");
    }

    // 启动 SOCKS5 代理服务器（独立线程）；allowlist/on_blocked 值拷贝进代理，
    // 后续写引擎成员不影响运行中的工作线程
    auto port = proxy_port_.load();
    proxy_ = std::make_unique<SocksProxyServer>(
        logger_, allowlist, std::move(on_blocked), listen_sock_, port);
    listen_sock_ = 0;  // 移交代理线程持有
    proxy_thread_ = std::thread([this] { proxy_->Run(); });

    logger_->Log(LogLevel::Info,
                 std::format("SOCKS5 proxy started: allowlist_size={} instance_id={} port={}",
                             allowlist.size(), instance_id, port));
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
    // 未注册到代理的残留监听 socket（Open 后直接 Close 的路径）
    if (listen_sock_ != 0) {
        closesocket(static_cast<SOCKET>(listen_sock_));
        listen_sock_ = 0;
    }
    logger_->Log(LogLevel::Info, "SOCKS5 proxy stopped");
    return Result<void>::Ok();
}

Result<void> WfpEngineImpl::Close() {
    UnregisterAll();
    running_.store(false);
    proxy_port_.store(0);
    WSACleanup();  // 与 Open 的 WSAStartup 配对
    logger_->Log(LogLevel::Info, "WFP engine closed");
    return Result<void>::Ok();
}

bool WfpEngineImpl::IsOpen() const {
    return running_.load();
}

} // namespace winsandbox