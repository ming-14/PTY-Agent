// =============================================================================
// EtwConfig - ETW 监控配置实体（core 层）
//
// 定义 ETW session 的配置参数，包括 session 名称、provider 列表、
// ring buffer 大小、事件过滤等。
// =============================================================================
#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <optional>

namespace winsandbox {

// ETW Provider 配置
struct EtwProviderConfig {
    std::string provider_guid;    // Provider GUID 字符串
    uint8_t  level = 0;           // EnableTraceEx2 level (0-255)
    uint64_t keyword_mask = 0;    // EnableTraceEx2 keyword mask
    uint32_t enable_flags = 0;    // NT Kernel Logger 专用 flags
};

// ETW Session 配置
struct EtwSessionConfig {
    std::string session_name;              // Session 名称（唯一）
    std::vector<EtwProviderConfig> providers;  // 该 session 启用的 provider
    bool is_kernel_session = false;         // 是否为内核 session（需管理员）
};

// ETW 监控总配置
struct EtwConfig {
    bool enabled = false;                          // 是否启用 ETW 监控
    std::vector<EtwSessionConfig> sessions;        // session 列表
    uint32_t ring_buffer_size = 10000;             // RingBuffer 容量（事件数）
    uint32_t dispatch_batch_size = 100;            // 批量发送大小
    uint32_t dispatch_timeout_ms = 10;             // 批量发送超时
    uint32_t stats_interval_ms = 5000;             // StatsReport 间隔

    // 事件类型过滤（空 = 全部订阅）
    std::vector<int> filter_types;

    // 进程 PID 白名单（空 = 不过滤；非空时只处理这些进程的事件，源头减噪）
    std::vector<uint32_t> filter_pids;

    // 降级模式文件监控目录（ReadDirectoryChangesW 监控，非管理员可用）
    // 空 = 不启用降级模式文件事件；非空 = 递归监控每个目录
    // 注意：仅对指定目录树生效，无法做到全盘监控（管理员 ETW 无此限制）
    std::vector<std::string> degraded_monitor_dirs;

    // 强制走降级模式（即使以管理员运行）
    // 用途：在管理员环境中也能验证/测试降级路径（文件/网络轮询事件）
    bool force_degraded = false;

    // 降级模式网络轮询开关（GetExtendedTcpTable/GetUdpTable，非管理员可用）
    bool degraded_net_polling = true;

    // 生成默认配置（管理员模式：1 个 NT Kernel Logger + 2 个用户态 session）
    static EtwConfig Default() {
        EtwConfig cfg;
        cfg.enabled = true;

        // Session 1: NT Kernel Logger（进程/线程/镜像/文件/注册表/网络）
        // NT Kernel Logger 是系统单例，session 名必须为 "NT Kernel Logger"
        // EnableFlags 控制采集哪些事件类别
        EtwSessionConfig s1;
        s1.session_name = "NT Kernel Logger";
        s1.is_kernel_session = true;
        EtwProviderConfig p1;
        // provider_guid 仅用于 EnableTraceEx2；内核分支（NT Kernel Logger）用
        // StartTraceW + EnableFlags，不走 EnableTraceEx2，此处不生效
        p1.provider_guid = "";
        // EVENT_TRACE_FLAG_PROCESS | THREAD | IMAGE_LOAD | DISK_IO | DISK_FILE_IO | NETWORK | REGISTRY
        // 注意：DISK_IO_INIT (0x00000800) 产生大量噪音，不启用；
        //       REGISTRY (0x00010000) 噪音大且解析为粗分类，如无需求可关闭
        p1.enable_flags = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000100 | 0x00000200 | 0x00010000 | 0x00000400;
        p1.level = 0;
        p1.keyword_mask = 0;
        s1.providers.push_back(p1);
        cfg.sessions.push_back(s1);

        // Session 2: 文件/注册表（manifest-based providers，非内核 session）
        EtwSessionConfig s2;
        s2.session_name = "win-sandbox-etw-file";
        s2.is_kernel_session = false;
        EtwProviderConfig p2a;
        p2a.provider_guid = "{edd08927-9cc4-4e65-b970-cb605d8e3e27}";  // Kernel-File
        p2a.level = 4;   // INFORMATIONAL
        p2a.keyword_mask = 0x10;  // WINEVENT_KEYWORD_FILE
        s2.providers.push_back(p2a);
        EtwProviderConfig p2b;
        p2b.provider_guid = "{ae53722e-c863-47d4-a83a-a5d2c7c6e5a0}";  // Kernel-Registry
        p2b.level = 4;   // INFORMATIONAL
        p2b.keyword_mask = 0x10;  // WINEVENT_KEYWORD_REGISTRY
        s2.providers.push_back(p2b);
        cfg.sessions.push_back(s2);

        // Session 3: 网络（manifest-based provider，非内核 session）
        EtwSessionConfig s3;
        s3.session_name = "win-sandbox-etw-net";
        s3.is_kernel_session = false;
        EtwProviderConfig p3;
        p3.provider_guid = "{7dd42a49-5329-4931-9a5e-4c3d8a5b2e1a}";  // Kernel-Network
        p3.level = 4;   // INFORMATIONAL
        p3.keyword_mask = 0x10;  // WINEVENT_KEYWORD_NETWORK
        s3.providers.push_back(p3);
        cfg.sessions.push_back(s3);

        return cfg;
    }

    // 生成降级配置（非管理员，进程轮询 + 可选的目录文件监控 + 网络轮询）
    static EtwConfig Degraded() {
        EtwConfig cfg;
        cfg.enabled = true;

        // 降级模式不依赖 ETW session：
        //   - 进程事件：Toolhelp32Snapshot 轮询进程列表
        //   - 文件事件：ReadDirectoryChangesW 监控 degraded_monitor_dirs
        //   - 网络事件：GetExtendedTcpTable/GetUdpTable 轮询连接表
        // degraded_monitor_dirs 默认空（不监控文件），由配置显式指定监控目录。

        cfg.ring_buffer_size = 5000;  // 降级时减小 buffer
        return cfg;
    }
};

} // namespace winsandbox
