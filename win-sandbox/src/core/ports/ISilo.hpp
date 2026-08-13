// =============================================================================
// ISilo - Server Silo 隔离端口（core 层）
//
// 抽象 Windows Server Silo（进程隔离容器）的可选更强隔离能力。
// 由 infra/silo/SiloImpl 提供具体实现。
//
// 背景：
//   - Server Silo = 带 SILO 标志的 Job Object，提供视图级隔离：
//     独立对象命名空间 / 注册表 hivestack / 文件系统挂载重定向 / 网络 compartment
//   - 与现有 Job（资源限制）+ Low IL token（完整性强制）正交，可叠加
//   - 用户态 API 未文档化；Win10 客户端实测不可用（JobObjectCreateSilo 返回
//     STATUS_INVALID_PARAMETER），仅 Win Server / Win11 预览支持
//
// 设计（条件启用，失败优雅降级）：
//   - IsAvailable() 动态探测：非管理员直接不可用；尝试创建失败 → 不可用
//   - ElevateJob() 把现有 Job 句柄就地升级为 Server Silo（不新建 Job）。
//     这样进程仍留在原 Job（资源限制照常生效），同时获得 Silo 的视图级隔离，
//     无需改变 Job 分配逻辑。
//   - 探测失败 / 非管理员时 IsAvailable() 返回 false，调用方跳过 Silo，
//     继续用现有 Job + Low IL 隔离，不影响任何功能。
//
// 句柄约定（与 IJobObject 一致）：
//   - 接口使用 void* 代替 Win32 HANDLE，避免 core 层包含 windows.h
//   - 实现层负责 void* ↔ HANDLE 的 reinterpret_cast
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"

#include <cstdint>

namespace winsandbox {

class ISilo {
public:
    virtual ~ISilo() = default;

    // 探测 Server Silo 是否可用（管理员 + 平台支持）
    // 线程安全，可随时调用；探测结果缓存
    virtual bool IsAvailable() const = 0;

    // 把已有 Job 句柄就地升级为 Server Silo Job
    // job_handle: 来自 IJobObject::GetHandle() 的 void* Job 句柄
    // 契约：Job 必须为空（尚未 AssignProcess 任何进程）——Silo 转换只允许
    //   空 Job；调用方须在 AssignProcess 之前调用（StartProcess 流程中
    //   Silo Elevate 先于 Launch）
    // 成功：Job 升级为 Silo，进程分配逻辑不变，资源限制仍生效
    // 失败：SiloUnavailable（探测失败）/ SiloCreateFailed（升级失败）
    virtual Result<void> ElevateJob(void* job_handle) = 0;
};

} // namespace winsandbox
