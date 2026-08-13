// =============================================================================
// GlobalQuotaManagerImpl - 全局资源配额实现（infra 层）
//
// 跨进程共享内存配额池：
//   - 命名 CreateFileMapping + MapViewOfFile：多个 sandbox.exe 进程共享同一块状态
//   - 命名 CreateMutex：串行化 acquire/release/query（写操作）
//   - 首次创建者（GetLastError == ERROR_ALREADY_EXISTS 判断）写入配置上限
//   - 末实例注销时释放共享内存句柄
//
// 内存布局（SharedState，进程共享）：
//   struct { magic, max_*, used_*, active_instances, owner_count }
//
// 权限说明：
//   - 命名对象名前缀 "Local\" 使共享内存仅在当前登录会话可见（跨会话隔离），
//     避免其他会话的同名池冲突；同会话内所有 sandbox.exe 可见。
//   - Mutex 同理使用 "Local\" 前缀。
//
// 线程安全：内部 mutex_ + 命名 Mutex 双重保护，线程安全。
// =============================================================================
#include "infra/globalquota/GlobalQuotaManagerImpl.hpp"

#include <windows.h>

namespace winsandbox {

namespace {

// 共享内存状态 magic 值（检测内存未初始化/版本不匹配）
constexpr uint32_t kSharedMagic = 0x574E5351;  // "WNSQ"

// 共享内存大小（4096 足够，含状态结构）
constexpr size_t kSharedSize = 4096;

// 命名对象前缀（当前登录会话内可见）
std::wstring ToWide(const std::string& s) {
    std::wstring w(s.begin(), s.end());
    return w;
}

} // namespace

GlobalQuotaManagerImpl::GlobalQuotaManagerImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger))
{
}

GlobalQuotaManagerImpl::~GlobalQuotaManagerImpl()
{
    Unregister();
}

Result<void> GlobalQuotaManagerImpl::Register(const GlobalQuotaConfig& config)
{
    if (!config.enabled) {
        return Result<void>::Err(ErrorCode::GlobalQuotaNotEnabled,
            "global quota not enabled");
    }

    std::lock_guard<std::mutex> lk(mutex_);
    if (registered_) {
        return Result<void>::Ok();  // 幂等
    }

    pool_name_ = config.pool_name;
    std::wstring map_name = L"Local\\" + ToWide(pool_name_);
    std::wstring mutex_name = L"Local\\" + ToWide(pool_name_) + L"-mutex";

    // 1. 创建/打开共享内存（首实例创建并写入上限）
    HANDLE mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        static_cast<DWORD>(kSharedSize), map_name.c_str());
    if (!mapping) {
        logger_->Log(LogLevel::Error,
            "GlobalQuota: CreateFileMappingW failed: " + std::to_string(GetLastError()));
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "CreateFileMappingW failed");
    }

    // 2. 打开命名 Mutex（串行化访问）
    HANDLE mutex = CreateMutexW(nullptr, FALSE, mutex_name.c_str());
    if (!mutex) {
        logger_->Log(LogLevel::Error,
            "GlobalQuota: CreateMutexW failed: " + std::to_string(GetLastError()));
        CloseHandle(mapping);
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "CreateMutexW failed");
    }

    // 3. 映射视图
    BYTE* base = static_cast<BYTE*>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, 0));
    if (!base) {
        logger_->Log(LogLevel::Error,
            "GlobalQuota: MapViewOfFile failed: " + std::to_string(GetLastError()));
        CloseHandle(mapping);
        CloseHandle(mutex);
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "MapViewOfFile failed");
    }

    mapping_handle_ = mapping;
    mutex_handle_ = mutex;
    shared_base_ = base;

    // 4. 锁内初始化
    DWORD wait = WaitForSingleObject(mutex_handle_, 5000);
    if (wait != WAIT_OBJECT_0) {
        logger_->Log(LogLevel::Error,
            "GlobalQuota: mutex wait timeout: " + std::to_string(wait));
        Unregister();
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "mutex wait timeout");
    }

    auto* state = reinterpret_cast<SharedState*>(base);
    DWORD err = GetLastError();
    if (err != ERROR_ALREADY_EXISTS) {
        // 首次创建：初始化状态 + 写入上限
        memset(state, 0, sizeof(SharedState));
        state->magic = kSharedMagic;
        state->max_cpu_rate_percent = config.max_cpu_rate_percent.value_or(0);
        state->max_memory_mb = config.max_memory_mb.value_or(0);
        state->max_processes = config.max_processes.value_or(0);
        logger_->Log(LogLevel::Info,
            "GlobalQuota: created pool '" + pool_name_ + "' "
            "(cpu=" + std::to_string(state->max_cpu_rate_percent) +
            " mem=" + std::to_string(state->max_memory_mb) +
            " proc=" + std::to_string(state->max_processes) + ")");
    } else {
        if (state->magic != kSharedMagic) {
            logger_->Log(LogLevel::Error,
                "GlobalQuota: existing pool has bad magic, ignoring");
            ReleaseMutex(mutex_handle_);
            Unregister();
            return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
                "existing pool magic mismatch");
        }
        logger_->Log(LogLevel::Info,
            "GlobalQuota: joined existing pool '" + pool_name_ + "' "
            "(cpu=" + std::to_string(state->max_cpu_rate_percent) +
            " mem=" + std::to_string(state->max_memory_mb) +
            " proc=" + std::to_string(state->max_processes) + ")");
    }

    // 登记本实例
    state->active_instances++;
    ReleaseMutex(mutex_handle_);

    registered_ = true;
    config_ = config;
    return Result<void>::Ok();
}

Result<void> GlobalQuotaManagerImpl::Unregister()
{
    std::lock_guard<std::mutex> lk(mutex_);
    if (!registered_) {
        return Result<void>::Ok();
    }

    // 锁内递减实例计数
    if (shared_base_ && mutex_handle_) {
        DWORD wait = WaitForSingleObject(mutex_handle_, 5000);
        if (wait == WAIT_OBJECT_0) {
            auto* state = reinterpret_cast<SharedState*>(shared_base_);
            if (state->active_instances > 0) {
                state->active_instances--;
            }
            ReleaseMutex(mutex_handle_);
        }
    }

    if (shared_base_) {
        UnmapViewOfFile(shared_base_);
        shared_base_ = nullptr;
    }
    if (mapping_handle_) {
        CloseHandle(mapping_handle_);
        mapping_handle_ = nullptr;
    }
    if (mutex_handle_) {
        CloseHandle(mutex_handle_);
        mutex_handle_ = nullptr;
    }

    registered_ = false;
    logger_->Log(LogLevel::Info, "GlobalQuota: unregistered from pool");
    return Result<void>::Ok();
}

Result<void> GlobalQuotaManagerImpl::Acquire(uint64_t memory_mb, uint32_t cpu_rate_percent,
                                             uint32_t process_count)
{
    std::lock_guard<std::mutex> lk(mutex_);
    if (!registered_ || !shared_base_ || !mutex_handle_) {
        return Result<void>::Err(ErrorCode::GlobalQuotaNotEnabled,
            "global quota not registered");
    }

    DWORD wait = WaitForSingleObject(mutex_handle_, 5000);
    if (wait != WAIT_OBJECT_0) {
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "mutex wait timeout");
    }

    auto* state = reinterpret_cast<SharedState*>(shared_base_);
    // 校验上限与本次申请
    if (state->max_memory_mb > 0 &&
        state->used_memory_mb + memory_mb > state->max_memory_mb) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global memory quota exceeded: " +
            std::to_string(state->used_memory_mb + memory_mb) + "/" +
            std::to_string(state->max_memory_mb) + " MB");
    }
    if (state->max_cpu_rate_percent > 0 &&
        state->used_cpu_rate + cpu_rate_percent > state->max_cpu_rate_percent) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global cpu quota exceeded: " +
            std::to_string(state->used_cpu_rate + cpu_rate_percent) + "/" +
            std::to_string(state->max_cpu_rate_percent) + " %");
    }
    if (state->max_processes > 0 &&
        state->active_processes + process_count > state->max_processes) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global process quota exceeded: " +
            std::to_string(state->active_processes + process_count) + "/" +
            std::to_string(state->max_processes));
    }

    state->used_memory_mb += memory_mb;
    state->used_cpu_rate += cpu_rate_percent;
    state->active_processes += process_count;
    ReleaseMutex(mutex_handle_);
    return Result<void>::Ok();
}

Result<void> GlobalQuotaManagerImpl::Release(uint64_t memory_mb, uint32_t cpu_rate_percent,
                                             uint32_t process_count)
{
    std::lock_guard<std::mutex> lk(mutex_);
    if (!registered_ || !shared_base_ || !mutex_handle_) {
        return Result<void>::Err(ErrorCode::GlobalQuotaNotEnabled,
            "global quota not registered");
    }

    DWORD wait = WaitForSingleObject(mutex_handle_, 5000);
    if (wait != WAIT_OBJECT_0) {
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "mutex wait timeout");
    }

    auto* state = reinterpret_cast<SharedState*>(shared_base_);
    // 防下溢
    state->used_memory_mb = (state->used_memory_mb >= memory_mb)
        ? state->used_memory_mb - memory_mb : 0;
    state->used_cpu_rate = (state->used_cpu_rate >= cpu_rate_percent)
        ? state->used_cpu_rate - cpu_rate_percent : 0;
    state->active_processes = (state->active_processes >= process_count)
        ? state->active_processes - process_count : 0;
    ReleaseMutex(mutex_handle_);
    return Result<void>::Ok();
}

Result<GlobalQuotaUsage> GlobalQuotaManagerImpl::Query() const
{
    GlobalQuotaUsage usage;

    std::lock_guard<std::mutex> lk(mutex_);
    if (!registered_ || !shared_base_ || !mutex_handle_) {
        return Result<GlobalQuotaUsage>::Err(ErrorCode::GlobalQuotaNotEnabled,
            "global quota not registered");
    }

    DWORD wait = WaitForSingleObject(mutex_handle_, 5000);
    if (wait != WAIT_OBJECT_0) {
        return Result<GlobalQuotaUsage>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "mutex wait timeout");
    }

    auto* state = reinterpret_cast<SharedState*>(shared_base_);
    usage.active_instances = state->active_instances;
    usage.used_memory_mb = state->used_memory_mb;
    usage.active_processes = state->active_processes;
    usage.used_cpu_rate = state->used_cpu_rate;
    ReleaseMutex(mutex_handle_);
    return Result<GlobalQuotaUsage>::Ok(std::move(usage));
}

} // namespace winsandbox
