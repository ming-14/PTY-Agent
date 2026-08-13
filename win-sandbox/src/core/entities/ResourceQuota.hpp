// =============================================================================
// ResourceQuota - 资源配额值对象（core 层）
//
// 描述沙箱对被隔离进程施加的资源限制。所有字段可选：未设置 = 不限制该项。
// 实现层（JobObjectImpl）负责把这些字段翻译为 Win32 Job Object 限制标志。
//
// 设计要点：
//   - 不依赖 windows.h（core 层保持框架独立）
//   - 所有时间字段单位：毫秒（ms）
//   - 所有内存字段单位：兆字节（MB）
//   - IO 速率字段单位：字节/秒 或 IOPS
//   - breakaway_ok=false（默认）：禁止子进程逃逸 Job（沙箱默认行为）
// =============================================================================
#pragma once

#include <cstdint>
#include <optional>

namespace winsandbox {

struct ResourceQuota {
    // ----- CPU 限制 -----
    std::optional<uint64_t> cpu_ms;              // Job 总 CPU 时间上限（ms）
    std::optional<uint32_t> cpu_rate_percent;    // CPU 占比硬上限（1-100）

    // ----- 内存限制 -----
    std::optional<uint64_t> memory_mb;           // 单进程提交内存上限
    std::optional<uint64_t> job_memory_mb;       // 整个 Job 内存上限

    // ----- IO 限制（Win10+，需管理员）-----
    std::optional<uint64_t> io_rate_bytes_per_sec;  // 字节/秒
    std::optional<uint64_t> io_rate_iops;           // IOPS

    // ----- 进程数限制 -----
    std::optional<uint32_t> max_processes;       // Job 内最大进程数（含子进程）

    // ----- 超时 -----
    std::optional<uint64_t> wall_clock_timeout_ms;  // 沙箱主循环 wall clock 超时
    std::optional<uint64_t> cpu_timeout_ms;          // Job CPU 时间超时（同 cpu_ms，语义别名）

    // ----- UI 限制 -----
    // true（默认）：禁止访问其他进程窗口、剪贴板、系统参数、显示设置、全局原子表
    bool no_ui = true;

    // ----- 子进程逃逸 -----
    // false（默认）：子进程强制留在 Job 内（沙箱推荐）
    // true：允许子进程通过 CREATE_BREAKAWAY_FROM_JOB 逃逸（仅在受控场景开启）
    bool breakaway_ok = false;

    // ----- 崩溃静默 -----
    // false（默认）：进程未处理异常时可能弹 Windows 错误对话框（WER）
    // true：设置 JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION，崩溃直接终止、
    //       无对话框（自动化/无头场景）
    bool crash_silent = false;
};

} // namespace winsandbox
