// =============================================================================
// SiloImpl - Server Silo 隔离实现（infra 层）
//
// 通过 ntdll 动态加载方式访问未文档化的 Server Silo API：
//   - NtSetInformationJobObject(JobObjectCreateSilo=35)  把 Job 就地转换为 Silo
//   - NtQueryInformationJobObject(JobObjectSiloBasicInformation=36) 探测确认
//
// 平台支持（docs/design/Phase2-Candidates-Evaluation-20260806.md）：
//   - 仅 Win Server / Win11 预览支持用户态创建 Server Silo
//   - Win10 客户端（含 22H2）实测 JobObjectCreateSilo 返回 STATUS_INVALID_PARAMETER
//   - 非管理员无权限（需 SeTcbPrivilege / SeAssignPrimaryTokenPrivilege）
//
// 设计（条件启用，失败优雅降级）：
//   - IsAvailable()：探测管理员 + 平台支持，结果缓存
//   - ElevateJob()：把调用方传入的 Job 句柄就地升级为 Server Silo。
//     进程仍留在原 Job（资源限制照常生效），同时获得 Silo 视图级隔离。
// =============================================================================
#include "infra/silo/SiloImpl.hpp"

#include <windows.h>
#include <winternl.h>

#include <format>

namespace winsandbox {

namespace {

// 未文档化信息类（winnt.h 已有枚举定义）
constexpr int kJobObjectCreateSilo = 35;
constexpr int kJobObjectSiloBasicInformation = 36;

// ntdll 函数指针类型
typedef NTSTATUS (NTAPI* NtSetInformationJobObject_t)(HANDLE, INT, PVOID, ULONG);
typedef NTSTATUS (NTAPI* NtQueryInformationJobObject_t)(HANDLE, INT, PVOID, ULONG, PULONG);
typedef NTSTATUS (NTAPI* NtCreateJobObject_t)(PHANDLE, ACCESS_MASK, POBJECT_ATTRIBUTES);

// 从 ntdll 动态加载函数指针
template <typename T>
bool LoadNtExport(const char* name, T& out) {
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (!ntdll) {
        return false;
    }
    FARPROC p = GetProcAddress(ntdll, name);
    if (!p) {
        return false;
    }
    out = reinterpret_cast<T>(p);
    return true;
}

// 是否管理员（Silo 创建要求 SeTcbPrivilege / SeAssignPrimaryTokenPrivilege）
bool IsElevated() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        return false;
    }
    BYTE buf[sizeof(TOKEN_ELEVATION)] = {};
    DWORD size = 0;
    BOOL ok = GetTokenInformation(token, TokenElevation, buf, sizeof(buf), &size);
    CloseHandle(token);
    if (!ok) {
        return false;
    }
    return reinterpret_cast<TOKEN_ELEVATION*>(buf)->TokenIsElevated != 0;
}

} // namespace

SiloImpl::SiloImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger))
{
}

SiloImpl::~SiloImpl()
{
}

bool SiloImpl::IsAvailable() const
{
    // 非管理员直接不可用（创建 Silo 需 SeTcbPrivilege）
    if (!IsElevated()) {
        return false;
    }

    // 需要 ntdll 导出函数
    static NtSetInformationJobObject_t fn_set = nullptr;
    static NtQueryInformationJobObject_t fn_query = nullptr;
    static NtCreateJobObject_t fn_create = nullptr;
    static bool fn_loaded = false;
    if (!fn_loaded) {
        fn_loaded = LoadNtExport("NtSetInformationJobObject", fn_set) &&
                    LoadNtExport("NtQueryInformationJobObject", fn_query) &&
                    LoadNtExport("NtCreateJobObject", fn_create);
    }
    if (!fn_loaded) {
        return false;
    }

    // 缓存探测结果
    if (probe_done_.load(std::memory_order_acquire)) {
        return probe_available_.load(std::memory_order_acquire);
    }
    std::lock_guard<std::mutex> lk(probe_mutex_);
    if (probe_done_.load(std::memory_order_acquire)) {
        return probe_available_.load(std::memory_order_acquire);
    }

    // 创建临时 Job 并尝试转换为 Silo 探测平台支持
    HANDLE job = nullptr;
    NTSTATUS st = fn_create(&job, JOB_OBJECT_ALL_ACCESS, nullptr);
    if (st != 0 || !job) {
        logger_->Log(LogLevel::Warn,
            "Silo: probe NtCreateJobObject failed: 0x" +
            std::format("{:08X}", static_cast<unsigned>(st)));
        probe_done_.store(true, std::memory_order_release);
        probe_available_.store(false, std::memory_order_release);
        return false;
    }

    st = fn_set(job, kJobObjectCreateSilo, nullptr, 0);
    bool supported = (st == 0);

    // 查询 Silo 基本信息二次确认
    if (supported) {
        BYTE info[32] = {};
        ULONG len = 0;
        NTSTATUS qs = fn_query(job, kJobObjectSiloBasicInformation, info, sizeof(info), &len);
        // STATUS_JOB_NO_CONTAINER = job 不是 container；转换成功后应返回 0
        supported = (qs == 0);
    }

    CloseHandle(job);

    if (supported) {
        logger_->Log(LogLevel::Info, "Silo: platform supports Server Silo");
    } else {
        logger_->Log(LogLevel::Warn,
            "Silo: platform does not support Server Silo (Win10 client likely), "
            "using Job+Low IL isolation only");
    }
    probe_done_.store(true, std::memory_order_release);
    probe_available_.store(supported, std::memory_order_release);
    return supported;
}

Result<void> SiloImpl::ElevateJob(void* job_handle)
{
    if (job_handle == nullptr) {
        return Result<void>::Err(ErrorCode::InvalidArgument,
            "ElevateJob: null job handle");
    }
    if (!IsAvailable()) {
        return Result<void>::Err(ErrorCode::SiloUnavailable,
            "Server Silo not available on this platform");
    }

    static NtSetInformationJobObject_t fn_set = nullptr;
    static bool fn_loaded = false;
    if (!fn_loaded) {
        fn_loaded = LoadNtExport("NtSetInformationJobObject", fn_set);
    }
    if (!fn_loaded || !fn_set) {
        return Result<void>::Err(ErrorCode::SiloUnavailable,
            "ntdll NtSetInformationJobObject not available");
    }

    NTSTATUS st = fn_set(static_cast<HANDLE>(job_handle), kJobObjectCreateSilo, nullptr, 0);
    if (st != 0) {
        logger_->Log(LogLevel::Error,
            "Silo: JobObjectCreateSilo failed: 0x" +
            std::format("{:08X}", static_cast<unsigned>(st)));
        return Result<void>::Err(ErrorCode::SiloCreateFailed,
            "JobObjectCreateSilo failed, silo elevation aborted");
    }

    logger_->Log(LogLevel::Info, "Silo: job elevated to Server Silo");
    return Result<void>::Ok();
}

} // namespace winsandbox
