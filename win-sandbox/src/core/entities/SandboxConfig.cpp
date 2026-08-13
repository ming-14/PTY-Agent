// =============================================================================
// SandboxConfig 实现
//
// 仅放 BuildDefault() 等需要 .cpp 的实现；inline 字段默认值已在头文件中给出。
// =============================================================================
#include "core/entities/SandboxConfig.hpp"

namespace winsandbox {

SandboxConfig SandboxConfig::BuildDefault() {
    SandboxConfig cfg;

    // 日志：info 级别，目录留空（让 Logger 用 spdlog 默认）
    cfg.logging.level = "info";
    cfg.logging.dir.clear();
    cfg.logging.retention_days = 7;

    // 默认资源配额：保守值
    //   - 不限 CPU 时间（cpu_ms 留空）
    //   - 不限 CPU 占比（cpu_rate_percent 留空，沙箱不主动限制）
    //   - 内存 256MB（OJ 场景典型值）
    //   - 进程数 64（防 fork 炸弹）
    //   - 默认无 wall_clock 超时（由调用方在 StartProcess 时指定）
    //   - UI 限制开启（沙箱默认无头）
    //   - 禁止子进程逃逸 Job
    cfg.default_quota.memory_mb = 256;
    cfg.default_quota.max_processes = 64;
    cfg.default_quota.no_ui = true;
    cfg.default_quota.breakaway_ok = false;

    return cfg;
}

} // namespace winsandbox
