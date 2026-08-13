// =============================================================================
// ErrorCode - 全局错误码枚举（core 层）
//
// 跨模块共享的错误码定义。各模块通过 Result<T> 返回错误时使用。
// =============================================================================
#pragma once

namespace winsandbox {

enum class ErrorCode {
    Ok = 0,

    // ----- IPC schema 校验 -----
    IpcSchemaValidationFailed,  // 字段缺失或类型错误（start_process 参数解析复用）

    // ----- Job Object 相关 -----
    JobCreateFailed,
    JobSetLimitFailed,           // SetInformationJobObject 失败
    JobAssignFailed,
    JobTerminateFailed,
    JobQueryFailed,
    JobProcessAlreadyInJob,      // 进程已隶属于其他 Job
    JobIocpCreateFailed,         // CreateIoCompletionPort 失败

    // ----- Process 相关 -----
    ProcessLaunchFailed,         // CreateProcessW 失败
    ProcessPipeCreateFailed,     // CreatePipe 失败
    ProcessWaitFailed,           // WaitForSingleObject 失败
    ProcessAlreadyExited,        // 进程已退出（Terminate 时句柄失效也复用）
    ProcessStillRunning,         // WaitForExit 超时：进程仍在运行
    ProcessStdinWriteFailed,     // WriteFile(stdin) 失败
    ProcessSignalFailed,         // GenerateConsoleCtrlEvent 失败
    ProcessNotFound,             // process_id 在沙箱实例中找不到

    // ----- Config 相关 -----
    ConfigFileNotFound,          // 配置文件不存在
    ConfigParseFailed,           // JSON 解析失败（语法错误）
    ConfigSchemaValidationFailed,// schema 校验失败（字段缺失/类型错/范围越界）

    // ----- TokenIsolator 相关 -----
    TokenIsolatorFailed,            // 隔离 token 派生失败（Duplicate/CreateRestricted/SetTokenInformation）

    // ----- WriteArea 相关 -----
    WriteAreaCreateFailed,          // 可写区创建/打 Low 标签失败
    WriteAreaTeardownFailed,        // 可写区清理失败

    // ----- ETW 相关 -----
    EtwSessionFailed,           // StartTraceW / EnableTraceEx2 失败
    EtwNotRunning,              // 未 Start 就调用 Stop

    // ----- Server Silo 相关（候选）-----
    SiloUnavailable,            // 平台不支持（Win10 客户端）/ 非管理员
    SiloCreateFailed,           // 创建 Silo Job 失败

    // ----- 多沙箱全局资源配额（候选）-----
    GlobalQuotaNotEnabled,      // 全局配额未启用就调用
    GlobalQuotaExceeded,        // 全局配额超限（CPU/内存/进程数）
    GlobalQuotaIpcFailed,       // 共享内存 / Mutex 创建失败

    // ----- 通用 -----
    InvalidArgument,
    InternalError,
};

} // namespace winsandbox
