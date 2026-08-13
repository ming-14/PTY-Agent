// =============================================================================
// NetworkRule - 网络白名单规则实体（core 层）
//
// 定义 IP/port 级网络访问控制规则，供 WFP callout 回调匹配。
// WFP allowlist 模式消费此实体。
//
// 设计要点：
//   - 不依赖 windows.h（core 层保持框架独立）
//   - ip 字段支持 IPv4（"1.2.3.4"）和 IPv6（"::1"）字符串
//   - port=0 表示匹配任意端口
//   - protocol=0 表示匹配任意协议
// =============================================================================

#pragma once

#include <cstdint>
#include <string>

namespace winsandbox {

struct NetworkRule {
    std::string ip;          // 目标 IP（IPv4 或 IPv6 字符串，空=匹配任意）
    uint16_t port = 0;       // 目标端口（0=匹配任意端口）
    uint8_t  protocol = 0;   // IP 协议号（6=TCP, 17=UDP, 0=任意）

    static constexpr uint8_t kTcp = 6;
    static constexpr uint8_t kUdp = 17;
};

} // namespace winsandbox
