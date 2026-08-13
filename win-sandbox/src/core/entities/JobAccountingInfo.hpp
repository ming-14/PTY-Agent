// =============================================================================
// JobAccountingInfo - Job 会计信息（core 层）
//
// 由 JobObjectImpl::QueryAccounting() 从 Win32
// JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION 转换而来。
// 全部使用可移植类型（uint64_t/uint32_t），不依赖 windows.h。
//
// 单位约定：
//   - CPU 时间：100ns 单位（与 Win32 FILETIME 一致），调用方可按需转换为 ms/s
//   - IO 计数：operation_count = 操作次数；transfer_count = 字节数
//   - 内存：字节
// =============================================================================
#pragma once

#include <cstdint>

namespace winsandbox {

struct JobAccountingInfo {
    // ----- 采样时间戳（Unix 毫秒）-----
    // 采样时刻，用于与 BehaviorLog 事件时间对齐
    uint64_t sample_time_ms = 0;

    // ----- CPU 时间（100ns 单位）-----
    uint64_t total_user_time_100ns = 0;                // Job 启动至今用户态总时间
    uint64_t total_kernel_time_100ns = 0;              // Job 启动至今内核态总时间
    uint64_t this_period_user_time_100ns = 0;          // 本周期用户态时间（重置后）
    uint64_t this_period_kernel_time_100ns = 0;        // 本周期内核态时间（重置后）

    // ----- IO 操作计数 -----
    uint64_t read_operation_count = 0;
    uint64_t write_operation_count = 0;
    uint64_t other_operation_count = 0;

    // ----- IO 字节计数 -----
    uint64_t read_transfer_count = 0;                  // 字节
    uint64_t write_transfer_count = 0;
    uint64_t other_transfer_count = 0;

    // ----- 进程计数 -----
    uint32_t total_processes = 0;                      // 历史累计进程数
    uint32_t active_processes = 0;                     // 当前活跃进程数
    uint32_t terminated_processes = 0;                 // 因限制被终止的进程数

    // ----- 内存峰值（字节）-----
    uint64_t peak_process_memory = 0;                  // 单进程峰值
    uint64_t peak_job_memory = 0;                      // Job 整体峰值

    // ----- 页错误 -----
    uint64_t total_page_faults = 0;
};

} // namespace winsandbox
