// =============================================================================
// SandboxConfig - 沙箱配置领域对象（core 层）
//
// 由 ConfigLoader 从 JSON 文件解析而来，作为沙箱启动期的全局配置。
// 顶层段：logging / default_quota / isolation / monitoring / silo / global_quota
//
// 设计要点：
//   - 不依赖 windows.h 与第三方库（core 层保持框架独立）
//   - 所有路径字段在加载时已展开环境变量（%LOCALAPPDATA% → 实际路径）
//   - 数值字段用固定宽度整型，避免跨平台 int 大小差异
//   - 默认值在 BuildDefault() 中集中维护，便于审计
//
// 字段单位约定（与 ResourceQuota 一致）：
//   - 时间：毫秒（ms）
//   - 内存：兆字节（MB）
//   - 大小：兆字节（MB）
// =============================================================================
#pragma once

#include "core/entities/GlobalQuota.hpp"
#include "core/entities/IsolationPolicy.hpp"
#include "core/entities/ResourceQuota.hpp"
#include "core/entities/EtwConfig.hpp"

#include <cstdint>
#include <string>

namespace winsandbox {

// ----- 日志配置 -----
struct LoggingConfig {
    std::string level = "info";         // trace|debug|info|warn|error
    std::string dir;                    // 日志目录（已展开环境变量），空表示用 spdlog 默认
    uint32_t retention_days = 7;        // 日志保留天数（0 = 永久保留）
};

// ----- 监控配置 -----
struct MonitoringConfig {
    bool etw_enabled = false;           // 是否启用 ETW 行为监控
    EtwConfig etw;                     // ETW 配置
};

// ----- Server Silo 配置（候选）-----
struct SiloConfig {
    bool enabled = false;              // 是否尝试启用 Server Silo（需平台支持）
};

// ----- 沙箱全局配置 -----
struct SandboxConfig {
    LoggingConfig logging;
    MonitoringConfig monitoring;       // 行为监控
    ResourceQuota default_quota;        // 默认资源配额（start_process 未指定 quota 时 fallback）

    SiloConfig silo;                    // 候选：Server Silo 更强隔离
    GlobalQuotaConfig global_quota;     // 候选：多沙箱全局资源配额

    // 默认隔离策略（start_process 未指定 isolation_policy 时 fallback）
    // 由 ConfigLoader 从 JSON 的 "isolation" 段解析
    IsolationPolicy default_isolation_policy;

    // 构造内置默认配置（无配置文件时使用）
    // 集中维护默认值，便于审计与测试
    static SandboxConfig BuildDefault();
};

} // namespace winsandbox
