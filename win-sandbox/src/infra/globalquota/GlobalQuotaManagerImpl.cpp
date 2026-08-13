// =============================================================================
// GlobalQuotaManagerImpl - 全局资源配额实现（infra 层）
//
// 跨进程共享内存配额池：
//   - 命名 CreateFileMapping + MapViewOfFile：多个沙箱实例进程共享同一块状态
//   - 命名 CreateMutex：串行化 acquire/release/query（写操作）
//   - 首次创建者（CreateFileMappingW 返回值 + 紧随其后的 GetLastError 判断）
//     写入配置上限
//   - 实例台账（InstanceSlot 数组）+ 心跳：宿主崩溃/kill 后，其他实例在
//     Acquire/Query 时回收超时槽位占用，防止配额永久泄漏
//   - 注销时释放共享内存句柄
//
// 内存布局（SharedState，进程共享）：
//   struct { magic, max_*, used_*, InstanceSlot slots[] }
//
// 权限说明：
//   - 命名对象名前缀 "Local\" 使共享内存仅在当前登录会话可见（跨会话隔离），
//     避免其他会话的同名池冲突；同会话内所有沙箱实例可见。
//   - Mutex 同理使用 "Local\" 前缀。
//
// 线程安全：内部 mutex_ + 命名 Mutex 双重保护。Register 错误路径不借用
// Unregister（防持锁自死锁），走 CleanupHandlesLocked 直接释放。
// =============================================================================
#include "infra/globalquota/GlobalQuotaManagerImpl.hpp"

#include <windows.h>

#include <ctime>
#include <string>

namespace winsandbox {

namespace {

// 共享内存状态 magic 值（检测内存未初始化/版本不匹配）
constexpr uint32_t kSharedMagic = 0x574E5351;  // "WNSQ"

// 心跳超时：超过该时长未活动的实例视为已崩溃，回收其占用（秒）
constexpr uint32_t kHeartbeatTimeoutSec = 60;

// 共享内存大小（4096 足够，含状态结构）
constexpr size_t kSharedSize = 4096;

// 命名对象前缀（当前登录会话内可见）
std::wstring ToWide(const std::string& s) {
    if (s.empty()) {
        return L"";
    }
    int len = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()),
                                  nullptr, 0);
    if (len <= 0) {
        return L"";
    }
    std::wstring w(static_cast<size_t>(len), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()),
                        w.data(), len);
    return w;
}

// 当前 epoch 秒（跨进程统一时间基，用于心跳）
uint32_t NowEpochSec() {
    return static_cast<uint32_t>(std::time(nullptr));
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
    //    注意：GetLastError 必须紧随 CreateFileMappingW 之后捕获，
    //    中间不可插入其他 API 调用（否则 last-error 被污染，误判首创建者
    //    会把现存池 memset 清零）
    HANDLE mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        static_cast<DWORD>(kSharedSize), map_name.c_str());
    DWORD create_err = GetLastError();
    if (!mapping) {
        logger_->Log(LogLevel::Error,
            "GlobalQuota: CreateFileMappingW failed: " + std::to_string(create_err));
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
        CleanupHandlesLocked();
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "mutex wait timeout");
    }

    auto* state = reinterpret_cast<SharedState*>(base);
    bool is_first_creator = (create_err != ERROR_ALREADY_EXISTS);
    if (is_first_creator) {
        // 首次创建：初始化状态 + 写入上限 + 全槽清零
        memset(state, 0, kSharedSize);
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
            CleanupHandlesLocked();
            return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
                "existing pool magic mismatch");
        }
        logger_->Log(LogLevel::Info,
            "GlobalQuota: joined existing pool '" + pool_name_ + "' "
            "(cpu=" + std::to_string(state->max_cpu_rate_percent) +
            " mem=" + std::to_string(state->max_memory_mb) +
            " proc=" + std::to_string(state->max_processes) + ")");
    }

    // 5. 登记本实例：分配唯一令牌 + 空槽
    uint64_t token = (static_cast<uint64_t>(::GetCurrentProcessId()) << 32) |
                     static_cast<uint32_t>(reinterpret_cast<uintptr_t>(this) ^
                                           static_cast<uintptr_t>(::GetTickCount64()));
    uint32_t slot_idx = 0;
    for (; slot_idx < kMaxInstanceSlots; ++slot_idx) {
        if (state->slots[slot_idx].token == 0) {
            break;
        }
    }
    if (slot_idx >= kMaxInstanceSlots) {
        logger_->Log(LogLevel::Error,
            "GlobalQuota: instance slot table full (max " +
            std::to_string(kMaxInstanceSlots) + ")");
        ReleaseMutex(mutex_handle_);
        CleanupHandlesLocked();
        return Result<void>::Err(ErrorCode::GlobalQuotaIpcFailed,
            "instance slot table full");
    }
    auto& slot = state->slots[slot_idx];
    slot.token = token;
    slot.pid = ::GetCurrentProcessId();
    slot.last_heartbeat_s = NowEpochSec();
    slot.mem_mb = 0;
    slot.cpu_rate = 0;
    slot.process_count = 0;

    own_token_ = token;
    own_slot_ = slot_idx;
    ReleaseMutex(mutex_handle_);

    registered_ = true;
    config_ = config;
    logger_->Log(LogLevel::Info,
        "GlobalQuota: registered to pool '" + pool_name_ + "' slot=" +
        std::to_string(slot_idx));
    return Result<void>::Ok();
}

Result<void> GlobalQuotaManagerImpl::Unregister()
{
    std::lock_guard<std::mutex> lk(mutex_);
    if (!registered_) {
        return Result<void>::Ok();
    }

    // 锁内清除本实例槽位（占用从池中扣除）
    if (shared_base_ && mutex_handle_) {
        DWORD wait = WaitForSingleObject(mutex_handle_, 5000);
        if (wait == WAIT_OBJECT_0) {
            auto* state = reinterpret_cast<SharedState*>(shared_base_);
            auto& slot = state->slots[own_slot_];
            if (slot.token == own_token_) {
                state->used_memory_mb -= slot.mem_mb;
                state->used_cpu_rate -= slot.cpu_rate;
                state->active_processes -= slot.process_count;
                slot = {};
            }
            ReleaseMutex(mutex_handle_);
        }
    }

    CleanupHandlesLocked();

    registered_ = false;
    own_token_ = 0;
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
    // 回收心跳超时的陈旧实例（崩溃/kill 泄漏的占用）
    ReclaimStaleSlotsLocked(state);

    // 校验本次申请的总量（加法溢出保护）
    if (memory_mb > UINT64_MAX - state->used_memory_mb) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global memory quota overflow");
    }
    if (state->max_memory_mb > 0 &&
        state->used_memory_mb + memory_mb > state->max_memory_mb) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global memory quota exceeded: " +
            std::to_string(state->used_memory_mb + memory_mb) + "/" +
            std::to_string(state->max_memory_mb) + " MB");
    }
    if (state->max_cpu_rate_percent > 0 &&
        static_cast<uint64_t>(state->used_cpu_rate) + cpu_rate_percent >
            state->max_cpu_rate_percent) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global cpu quota exceeded: " +
            std::to_string(state->used_cpu_rate + cpu_rate_percent) + "/" +
            std::to_string(state->max_cpu_rate_percent) + " %");
    }
    if (state->max_processes > 0 &&
        static_cast<uint64_t>(state->active_processes) + process_count >
            state->max_processes) {
        ReleaseMutex(mutex_handle_);
        return Result<void>::Err(ErrorCode::GlobalQuotaExceeded,
            "global process quota exceeded: " +
            std::to_string(state->active_processes + process_count) + "/" +
            std::to_string(state->max_processes));
    }

    state->used_memory_mb += memory_mb;
    state->used_cpu_rate += cpu_rate_percent;
    state->active_processes += process_count;

    // 本实例槽位登记占用 + 刷新心跳
    auto& slot = state->slots[own_slot_];
    if (slot.token == own_token_) {
        slot.mem_mb += memory_mb;
        slot.cpu_rate += cpu_rate_percent;
        slot.process_count += process_count;
        slot.last_heartbeat_s = NowEpochSec();
    }

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

    // 本实例槽位占用同步扣减（不下溢）
    auto& slot = state->slots[own_slot_];
    if (slot.token == own_token_) {
        slot.mem_mb = (slot.mem_mb >= memory_mb) ? slot.mem_mb - memory_mb : 0;
        slot.cpu_rate = (slot.cpu_rate >= cpu_rate_percent)
            ? slot.cpu_rate - cpu_rate_percent : 0;
        slot.process_count = (slot.process_count >= process_count)
            ? slot.process_count - process_count : 0;
        slot.last_heartbeat_s = NowEpochSec();
    }

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
    // 顺手回收陈旧实例
    ReclaimStaleSlotsLocked(state);

    usage.active_instances = 0;
    for (uint32_t i = 0; i < kMaxInstanceSlots; ++i) {
        if (state->slots[i].token != 0) {
            usage.active_instances++;
        }
    }
    usage.used_memory_mb = state->used_memory_mb;
    usage.active_processes = state->active_processes;
    usage.used_cpu_rate = state->used_cpu_rate;

    // 刷新本实例心跳
    auto& slot = state->slots[own_slot_];
    if (slot.token == own_token_) {
        slot.last_heartbeat_s = NowEpochSec();
    }

    ReleaseMutex(mutex_handle_);
    return Result<GlobalQuotaUsage>::Ok(std::move(usage));
}

// =============================================================================
// ReclaimStaleSlotsLocked - 回收心跳超时的实例槽（必须在命名互斥锁内调用）
//
// 崩塌场景：宿主进程崩溃/kill，Unregister 不会执行 → 槽与占用残留。
// 其他实例后续 Acquire/Query 时发现心跳超时，扣除其占用并清槽。
// =============================================================================
void GlobalQuotaManagerImpl::ReclaimStaleSlotsLocked(SharedState* state) const
{
    uint32_t now = NowEpochSec();
    for (uint32_t i = 0; i < kMaxInstanceSlots; ++i) {
        auto& slot = state->slots[i];
        if (slot.token == 0) {
            continue;
        }
        if (now >= slot.last_heartbeat_s &&
            now - slot.last_heartbeat_s > kHeartbeatTimeoutSec) {
            state->used_memory_mb -= slot.mem_mb;
            state->used_cpu_rate -= slot.cpu_rate;
            state->active_processes -= slot.process_count;
            logger_->Log(LogLevel::Warn,
                "GlobalQuota: reclaimed stale instance slot=" + std::to_string(i) +
                " pid=" + std::to_string(slot.pid) + " (heartbeat expired)");
            slot = {};
        }
    }
}

// =============================================================================
// CleanupHandlesLocked - 释放共享内存句柄（不加锁）
//
// 仅供 Register 错误路径在持有 mutex_（内部锁）时调用；
// Unregister 在锁内清理槽位后也走这里。不可调用 Unregister 本身，
// 否则二次加锁死锁。
// =============================================================================
void GlobalQuotaManagerImpl::CleanupHandlesLocked()
{
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
}

} // namespace winsandbox