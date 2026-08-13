// =============================================================================
// BehaviorEvent - 行为事件实体（core 层）
//
// ETW 采集的进程/文件/注册表/网络四类行为事件统一表示。
// 每个事件携带序号（seq），用于丢包检测。
// =============================================================================
#pragma once

#include <cstdint>
#include <string>
#include <nlohmann/json.hpp>

namespace winsandbox {

// 行为事件类型
enum class BehaviorEventType : int {
    ProcessStart    = 0,   // 进程创建
    ProcessStop     = 1,   // 进程退出
    ThreadStart     = 2,   // 线程创建
    ThreadStop      = 3,   // 线程退出
    ImageLoad       = 4,   // 模块加载
    FileCreate      = 5,   // 文件创建/打开
    FileWrite       = 6,   // 文件写入
    FileDelete      = 7,   // 文件删除
    RegistrySetKey  = 8,   // 注册表写值
    RegistryCreateKey = 9, // 注册表建键
    RegistryDeleteKey = 10,// 注册表删键
    TcpConnect      = 11,  // TCP 连接
    UdpSend         = 12,  // UDP 发送
    AccessDenied    = 13,  // 访问拒绝
    GapDetected     = 14,  // 丢包检测（序号不连续）
    Unknown         = 99,  // 未知事件类型
};

NLOHMANN_JSON_SERIALIZE_ENUM(BehaviorEventType, {
    {BehaviorEventType::ProcessStart,    "process_start"},
    {BehaviorEventType::ProcessStop,     "process_stop"},
    {BehaviorEventType::ThreadStart,     "thread_start"},
    {BehaviorEventType::ThreadStop,      "thread_stop"},
    {BehaviorEventType::ImageLoad,       "image_load"},
    {BehaviorEventType::FileCreate,      "file_create"},
    {BehaviorEventType::FileWrite,       "file_write"},
    {BehaviorEventType::FileDelete,      "file_delete"},
    {BehaviorEventType::RegistrySetKey,  "registry_set_key"},
    {BehaviorEventType::RegistryCreateKey, "registry_create_key"},
    {BehaviorEventType::RegistryDeleteKey, "registry_delete_key"},
    {BehaviorEventType::TcpConnect,      "tcp_connect"},
    {BehaviorEventType::UdpSend,         "udp_send"},
    {BehaviorEventType::AccessDenied,    "access_denied"},
    {BehaviorEventType::GapDetected,     "gap_detected"},
    {BehaviorEventType::Unknown,         "unknown"},
})

// 行为事件结构
struct BehaviorEvent {
    BehaviorEventType type = BehaviorEventType::Unknown;
    uint32_t pid = 0;             // 进程 ID
    uint32_t tid = 0;             // 线程 ID
    uint64_t timestamp_ms = 0;    // 事件时间戳（Unix ms）
    uint64_t seq = 0;             // 全局序号（递增，用于丢包检测）

    // 可变字段（按事件类型填充，其余为空）
    std::string image_path;       // ProcessStart/ImageLoad: 可执行文件/模块路径
    std::string command_line;     // ProcessStart: 命令行
    uint32_t parent_pid = 0;      // ProcessStart: 父进程 ID
    std::string file_path;        // File*: 文件路径
    std::string key_path;         // Registry*: 注册表路径
    std::string value_name;       // RegistrySetKey: 值名
    std::string local_addr;       // TcpConnect: 本地地址
    std::string remote_addr;      // TcpConnect/UdpSend: 远程地址
    uint16_t local_port = 0;      // TcpConnect: 本地端口
    uint16_t remote_port = 0;     // TcpConnect/UdpSend: 远程端口
    std::string operation;        // AccessDenied: 操作描述
    uint32_t gap_count = 0;       // GapDetected: 丢失事件数
};

// JSON 序列化
inline void to_json(nlohmann::json& j, const BehaviorEvent& e) {
    j = nlohmann::json{
        {"type", e.type},
        {"pid", e.pid},
        {"tid", e.tid},
        {"timestamp_ms", e.timestamp_ms},
        {"seq", e.seq},
    };
    if (!e.image_path.empty())     j["image_path"]     = e.image_path;
    if (!e.command_line.empty())   j["command_line"]   = e.command_line;
    if (e.parent_pid != 0)         j["parent_pid"]     = e.parent_pid;
    if (!e.file_path.empty())      j["file_path"]      = e.file_path;
    if (!e.key_path.empty())       j["key_path"]       = e.key_path;
    if (!e.value_name.empty())     j["value_name"]     = e.value_name;
    if (!e.local_addr.empty())     j["local_addr"]     = e.local_addr;
    if (!e.remote_addr.empty())    j["remote_addr"]    = e.remote_addr;
    if (e.local_port != 0)         j["local_port"]     = e.local_port;
    if (e.remote_port != 0)        j["remote_port"]    = e.remote_port;
    if (!e.operation.empty())      j["operation"]      = e.operation;
    if (e.gap_count != 0)          j["gap_count"]      = e.gap_count;
}

} // namespace winsandbox
