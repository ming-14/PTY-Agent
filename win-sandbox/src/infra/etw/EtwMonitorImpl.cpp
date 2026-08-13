// =============================================================================
// EtwMonitorImpl - ETW 行为监控实现（infra 层）
//
// 实现分两部分：
// 1. 管理员模式：真 ETW session（StartTraceW + EnableTraceEx2 + ProcessTrace）
// 2. 降级模式：非管理员，通过轮询进程列表模拟 ProcessStart/Stop 事件
//
// 降级模式（2026-08-06 扩展）：
//   - 进程事件：Toolhelp32Snapshot 轮询进程列表（ProcessStart/Stop）
//   - 文件事件：ReadDirectoryChangesW 监控 degraded_monitor_dirs（FileCreate/Write/Delete）
//   - 网络事件：GetExtendedTcpTable/GetUdpTable 轮询连接表（TcpConnect/UdpSend）
//   - 首次轮询仅建立基线，不产生 ProcessStart 噪音（此前把全系统进程当新进程）
// 注册表事件仍不可用（RegNotifyChangeKeyValue 无法全局监控，文档说明）。
// =============================================================================
#include "infra/etw/EtwMonitorImpl.hpp"

#include <algorithm>
#include <chrono>
#include <psapi.h>
#include <tlhelp32.h>
#include <objbase.h>
#include <iphlpapi.h>
#include <ws2tcpip.h>

#pragma comment(lib, "psapi.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "iphlpapi.lib")

namespace winsandbox {

// 静态成员初始化
std::mutex EtwMonitorImpl::instance_mutex_;
EtwMonitorImpl* EtwMonitorImpl::instance_ = nullptr;
thread_local bool EtwMonitorImpl::tl_is_kernel_consumer_ = false;

EtwMonitorImpl::EtwMonitorImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger))
{
}

EtwMonitorImpl::~EtwMonitorImpl()
{
    if (running_.load(std::memory_order_acquire)) {
        Stop();
    }
}

bool EtwMonitorImpl::IsElevated()
{
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        return false;
    }
    TOKEN_ELEVATION elev = {};
    DWORD ret_len = 0;
    BOOL ok = GetTokenInformation(token, TokenElevation, &elev, sizeof(elev), &ret_len);
    CloseHandle(token);
    return ok && elev.TokenIsElevated != 0;
}

Result<void> EtwMonitorImpl::Start(const EtwConfig& config, BehaviorEventCallback callback)
{
    if (running_.load(std::memory_order_acquire)) {
        return Result<void>::Err(ErrorCode::EtwNotRunning, "ETW monitor already running");
    }
    if (!callback) {
        return Result<void>::Err(ErrorCode::InvalidArgument, "callback cannot be null");
    }

    callback_ = std::move(callback);
    ring_buffer_ = std::make_unique<RingBuffer>(config.ring_buffer_size);
    filter_types_ = config.filter_types;  // T6.10: 事件类型过滤
    degraded_monitor_dirs_ = config.degraded_monitor_dirs;
    degraded_net_polling_ = config.degraded_net_polling;

    bool elevated = IsElevated() && !config.force_degraded;

    if (!elevated) {
        logger_->Log(LogLevel::Warn,
            "ETW: Non-admin mode (or force_degraded), degrading to simulated monitoring "
            "(process polling + optional dir file watch + network polling)");

        running_.store(true, std::memory_order_release);
        dispatch_thread_ = std::thread(&EtwMonitorImpl::DispatchLoop, this);
        degraded_thread_ = std::thread(&EtwMonitorImpl::DegradedMonitorLoop, this);

        // 降级模式文件监控：仅当配置了监控目录时启动
        if (!degraded_monitor_dirs_.empty()) {
            degraded_file_thread_ = std::thread(&EtwMonitorImpl::DegradedFileMonitorLoop, this);
            logger_->Log(LogLevel::Info,
                "ETW: Degraded file monitor started for " +
                std::to_string(degraded_monitor_dirs_.size()) + " dir(s)");
        }

        started_.store(true, std::memory_order_release);
        return Result<void>::Ok();
    }

    // 管理员模式：创建 ETW sessions
    sessions_.reserve(config.sessions.size());

    {
        std::lock_guard<std::mutex> lk(instance_mutex_);
        instance_ = this;
    }

    for (const auto& sc : config.sessions) {
        EtwSession session;
        session.name = sc.session_name;
        session.is_kernel = sc.is_kernel_session;
        session.providers = sc.providers;

        for (const auto& p : sc.providers) {
            session.enable_flags |= p.enable_flags;
        }

        auto r = StartSession(session);
        if (!r) {
            logger_->Log(LogLevel::Warn,
                "ETW: Failed to start session '" + session.name + "': " + r.Message());
            // 继续尝试其他 session，不因单个失败而中断
        }
        sessions_.push_back(std::move(session));
    }

    int started_count = 0;
    for (const auto& s : sessions_) {
        if (s.session_handle != 0 || s.consumer_thread.joinable()) {
            ++started_count;
        }
    }

    if (started_count == 0) {
        logger_->Log(LogLevel::Warn,
            "ETW: All sessions failed to start, falling back to degraded mode");
        // 回退到降级模式
        degraded_thread_ = std::thread(&EtwMonitorImpl::DegradedMonitorLoop, this);
    }

    running_.store(true, std::memory_order_release);
    dispatch_thread_ = std::thread(&EtwMonitorImpl::DispatchLoop, this);

    started_.store(true, std::memory_order_release);
    logger_->Log(LogLevel::Info,
        "ETW: Started with " + std::to_string(sessions_.size()) + " sessions (admin mode)");
    return Result<void>::Ok();
}

Result<void> EtwMonitorImpl::StartSession(EtwSession& session)
{
    std::wstring wname(session.name.begin(), session.name.end());

    // 先尝试停止已有同名 session（避免 ERROR_ALREADY_EXISTS）
    // 注意：NT Kernel Logger 是系统特殊会话，不能在此停止，否则会导致 consumer 立即退出
    if (!session.is_kernel) {
        ULONG stop_size = sizeof(EVENT_TRACE_PROPERTIES) + (wname.size() + 1) * sizeof(wchar_t);
        EVENT_TRACE_PROPERTIES* stop_props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(calloc(1, stop_size));
        if (stop_props) {
            stop_props->Wnode.BufferSize = stop_size;
            stop_props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
            // 复制 session name 到 LoggerNameOffset 位置（ETW API 要求）
            memcpy(reinterpret_cast<BYTE*>(stop_props) + stop_props->LoggerNameOffset,
                   wname.c_str(), (wname.size() + 1) * sizeof(wchar_t));
            ControlTraceW(0, wname.c_str(), stop_props, EVENT_TRACE_CONTROL_STOP);
            free(stop_props);
        }
    }

    ULONG prop_size = sizeof(EVENT_TRACE_PROPERTIES) + (wname.size() + 1) * sizeof(wchar_t);
    EVENT_TRACE_PROPERTIES* props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(calloc(1, prop_size));
    if (!props) {
        return Result<void>::Err(ErrorCode::EtwSessionFailed, "calloc failed for trace properties");
    }
    props->Wnode.BufferSize = prop_size;
    props->Wnode.Flags = WNODE_FLAG_TRACED_GUID;
    props->Wnode.ClientContext = 1;  // QPC 时间戳
    props->LogFileMode = EVENT_TRACE_REAL_TIME_MODE;
    props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
    // 复制 session name 到 LoggerNameOffset 位置（ETW API 要求）
    memcpy(reinterpret_cast<BYTE*>(props) + props->LoggerNameOffset,
           wname.c_str(), (wname.size() + 1) * sizeof(wchar_t));
    props->BufferSize = 1024;
    props->MinimumBuffers = 4;
    props->MaximumBuffers = 32;

    if (session.is_kernel) {
        // NT Kernel Logger：使用 SystemTraceControlGuid + EnableFlags
        props->Wnode.Guid = SystemTraceControlGuid;
        props->EnableFlags = session.enable_flags;

        ULONG status = StartTraceW(&session.session_handle, wname.c_str(), props);
        logger_->Log(LogLevel::Info, std::string("ETW: StartTraceW(kernel) returned ") +
            std::to_string(status) + " handle=" + std::to_string(static_cast<ULONG64>(session.session_handle)));
        if (status == ERROR_ALREADY_EXISTS) {
            // 尝试更新现有 session 的 EnableFlags（确保获取了所需的事件类别）
            ULONG update_status = ControlTraceW(0, wname.c_str(), props,
                                                  EVENT_TRACE_CONTROL_UPDATE);
            if (update_status != ERROR_SUCCESS) {
                logger_->Log(LogLevel::Warn,
                    "ETW: Failed to update existing NT Kernel Logger EnableFlags: " +
                    std::to_string(update_status));
            } else {
                logger_->Log(LogLevel::Info,
                    "ETW: Updated existing NT Kernel Logger with new EnableFlags");
            }
        }
        free(props);
        if (status != ERROR_SUCCESS && status != ERROR_ALREADY_EXISTS) {
            return Result<void>::Err(ErrorCode::EtwSessionFailed,
                "StartTraceW(kernel) failed: " + std::to_string(status));
        }
        if (status == ERROR_ALREADY_EXISTS) {
            logger_->Log(LogLevel::Info,
                "ETW: NT Kernel Logger already exists, using existing session");
        }
    } else {
        // 用户态 session：StartTraceW + EnableTraceEx2
        // 重试机制：可能的残留 session 需要时间完全停止
        ULONG status = ERROR_ALREADY_EXISTS;
        for (int retry = 0; retry < 3 && status == ERROR_ALREADY_EXISTS; ++retry) {
            if (retry > 0) {
                logger_->Log(LogLevel::Info,
                    "ETW: Retrying StartTraceW for " + session.name +
                    " (attempt " + std::to_string(retry + 1) + ")");
                Sleep(200);
            }
            status = StartTraceW(&session.session_handle, wname.c_str(), props);
        }
        free(props);
        if (status != ERROR_SUCCESS) {
            return Result<void>::Err(ErrorCode::EtwSessionFailed,
                "StartTraceW(user) failed: " + std::to_string(status));
        }

        for (const auto& p : session.providers) {
            GUID providerGuid;
            std::wstring wguid(p.provider_guid.begin(), p.provider_guid.end());
            if (CLSIDFromString(wguid.c_str(), &providerGuid) != NOERROR) {
                logger_->Log(LogLevel::Warn,
                    "ETW: CLSIDFromString failed for " + p.provider_guid);
                continue;
            }
            ENABLE_TRACE_PARAMETERS enableParams = {};
            enableParams.Version = ENABLE_TRACE_PARAMETERS_VERSION_2;
            ULONG es = EnableTraceEx2(session.session_handle,
                                      &providerGuid,
                                      EVENT_CONTROL_CODE_ENABLE_PROVIDER,
                                      p.level,
                                      p.keyword_mask,
                                      0, 0, &enableParams);
            if (es != ERROR_SUCCESS) {
                logger_->Log(LogLevel::Warn,
                    "ETW: EnableTraceEx2 failed for " + p.provider_guid +
                    ": " + std::to_string(es));
            }
        }
    }

    // 启动消费线程（按值捕获 session 名，避免悬垂引用）
    std::string session_name_copy = session.name;
    bool is_kernel_consumer = session.is_kernel;
    session.consumer_thread = std::thread([this, session_name_copy, wname, is_kernel_consumer]() {
        // 标记当前线程是否为 NT Kernel Logger consumer
        // NT Kernel Logger 事件的 provider GUID 因 Windows 版本而异，
        // 不能硬编码匹配，需通过 consumer 线程身份路由
        tl_is_kernel_consumer_ = is_kernel_consumer;

        EVENT_TRACE_LOGFILEW logfile = {};
        logfile.LoggerName = const_cast<LPWSTR>(wname.c_str());
        // 统一使用 EventRecordCallback + PROCESS_TRACE_MODE_EVENT_RECORD
        logfile.EventRecordCallback = &EtwMonitorImpl::EventRecordCallback;
        logfile.ProcessTraceMode = PROCESS_TRACE_MODE_EVENT_RECORD |
                                   PROCESS_TRACE_MODE_REAL_TIME |
                                   PROCESS_TRACE_MODE_RAW_TIMESTAMP;

        TRACEHANDLE consumer_handle = OpenTraceW(&logfile);
        if (consumer_handle == INVALID_PROCESSTRACE_HANDLE) {
            logger_->Log(LogLevel::Warn,
                "ETW: OpenTraceW failed for " + session_name_copy +
                ": " + std::to_string(GetLastError()));
            return;
        }

        logger_->Log(LogLevel::Info,
            std::string("ETW: Consumer started for ") + session_name_copy +
            " handle=" + std::to_string(static_cast<ULONG64>(consumer_handle)));

        ULONG ret = ProcessTrace(&consumer_handle, 1, nullptr, nullptr);
        logger_->Log(LogLevel::Info,
            "ETW: Consumer exited for " + session_name_copy +
            " ret=" + std::to_string(ret));
        CloseTrace(consumer_handle);
    });

    logger_->Log(LogLevel::Info, "ETW: Session '" + session.name + "' started");
    return Result<void>::Ok();
}

void EtwMonitorImpl::StopSession(EtwSession& session)
{
    // 始终用 session 名称停止 trace（即使 session_handle == 0）
    // 避免 ProcessTrace 无法退出导致 consumer_thread.join() 死锁
    {
        ULONG prop_size = sizeof(EVENT_TRACE_PROPERTIES) +
                          (session.name.size() + 1) * sizeof(wchar_t);
        EVENT_TRACE_PROPERTIES* props = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(calloc(1, prop_size));
        if (props) {
            props->Wnode.BufferSize = prop_size;
            props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
            std::wstring wname(session.name.begin(), session.name.end());
            ControlTraceW(session.session_handle, wname.c_str(), props,
                          EVENT_TRACE_CONTROL_STOP);
            free(props);
        }
        session.session_handle = 0;
    }

    if (session.consumer_thread.joinable()) {
        session.consumer_thread.join();
    }
}

void EtwMonitorImpl::EventRecordCallback(PEVENT_RECORD record)
{
    std::lock_guard<std::mutex> lk(instance_mutex_);
    if (instance_) {
        instance_->ProcessEventRecord(record);
    }
}

void EtwMonitorImpl::LegacyEventCallback(PEVENT_TRACE event)
{
    std::lock_guard<std::mutex> lk(instance_mutex_);
    if (instance_) {
        instance_->ProcessLegacyEvent(event);
    }
}

void EtwMonitorImpl::ProcessLegacyEvent(PEVENT_TRACE event)
{
    if (!event || !ring_buffer_) return;

    BehaviorEvent beh;
    beh.pid = static_cast<uint32_t>(event->Header.ProcessId);
    beh.tid = static_cast<uint32_t>(event->Header.ThreadId);

    ULARGE_INTEGER ft;
    ft.LowPart = event->Header.TimeStamp.LowPart;
    ft.HighPart = event->Header.TimeStamp.HighPart;
    beh.timestamp_ms = (ft.QuadPart - 116444736000000000ULL) / 10000;

    // 通过 EVENT_HEADER.Class.Type（opcode）区分事件类型
    // 旧式 EVENT_TRACE_HEADER 用 Class.Type 作为 opcode
    UCHAR opcode = event->Header.Class.Type;
    UCHAR class_id = event->Header.Class.Level;  // 在 NT Kernel Logger 中，Class.Level 存 event id

    if (event->Header.Guid == SystemTraceControlGuid) {
        switch (opcode) {
        case 1:  // EVENT_TRACE_TYPE_START
            if (class_id == 1) beh.type = BehaviorEventType::ProcessStart;
            else if (class_id == 2) beh.type = BehaviorEventType::ThreadStart;
            break;
        case 2:  // EVENT_TRACE_TYPE_END
            if (class_id == 1) beh.type = BehaviorEventType::ProcessStop;
            else if (class_id == 2) beh.type = BehaviorEventType::ThreadStop;
            break;
        case 3:
            if (class_id == 2) beh.type = BehaviorEventType::ThreadStart;
            break;
        case 4:
            if (class_id == 2) beh.type = BehaviorEventType::ThreadStop;
            break;
        case 10:  // Image Load
            beh.type = BehaviorEventType::ImageLoad;
            break;
        case 11:
            beh.type = BehaviorEventType::ProcessStop;
            break;
        default:
            beh.type = BehaviorEventType::Unknown;
            break;
        }
    } else {
        beh.type = BehaviorEventType::Unknown;
    }

    if (beh.type == BehaviorEventType::Unknown) return;

    // 从 MoF UserData 提取字段（简单偏移读取）
    if (event->MofData && event->MofLength > 0) {
        BYTE* data = reinterpret_cast<BYTE*>(event->MofData);
        ULONG data_len = event->MofLength;
        bool is_64bit = (event->Header.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;

        if (beh.type == BehaviorEventType::ProcessStart) {
            if (data_len >= (is_64bit ? 24u : 16u)) {
                beh.pid = *reinterpret_cast<ULONG*>(data + (is_64bit ? 8 : 4));
                beh.parent_pid = *reinterpret_cast<ULONG*>(data + (is_64bit ? 12 : 8));
            }
            // 字符串在固定字段后
            ULONG str_off = is_64bit ? 24 : 16;
            if (str_off + sizeof(WCHAR) <= data_len) {
                WCHAR* ws = reinterpret_cast<WCHAR*>(data + str_off);
                ULONG max_chars = (data_len - str_off) / sizeof(WCHAR);
                ULONG len = 0;
                while (len < max_chars && ws[len] != L'\0') ++len;
                if (len > 0) {
                    int utf8_len = WideCharToMultiByte(CP_UTF8, 0, ws, len, nullptr, 0, nullptr, nullptr);
                    if (utf8_len > 0) {
                        beh.image_path.resize(utf8_len);
                        WideCharToMultiByte(CP_UTF8, 0, ws, len, &beh.image_path[0], utf8_len, nullptr, nullptr);
                    }
                }
            }
        } else if (beh.type == BehaviorEventType::ImageLoad) {
            ULONG str_off = is_64bit ? 24 : 16;
            if (str_off + sizeof(WCHAR) <= data_len) {
                WCHAR* ws = reinterpret_cast<WCHAR*>(data + str_off);
                ULONG max_chars = (data_len - str_off) / sizeof(WCHAR);
                ULONG len = 0;
                while (len < max_chars && ws[len] != L'\0') ++len;
                if (len > 0) {
                    int utf8_len = WideCharToMultiByte(CP_UTF8, 0, ws, len, nullptr, 0, nullptr, nullptr);
                    if (utf8_len > 0) {
                        beh.image_path.resize(utf8_len);
                        WideCharToMultiByte(CP_UTF8, 0, ws, len, &beh.image_path[0], utf8_len, nullptr, nullptr);
                    }
                }
            }
        }
    }

    logger_->Log(LogLevel::Debug,
        std::format("ETW: LegacyEvent type={} pid={}", static_cast<int>(beh.type), beh.pid));

    total_events_.fetch_add(1, std::memory_order_relaxed);
    ring_buffer_->Push(std::move(beh));
}

void EtwMonitorImpl::ProcessEventRecord(PEVENT_RECORD record)
{
    if (!record || !ring_buffer_) return;

    BehaviorEvent event;
    event.pid = static_cast<uint32_t>(record->EventHeader.ProcessId);
    event.tid = static_cast<uint32_t>(record->EventHeader.ThreadId);

    ULARGE_INTEGER ft;
    ft.LowPart = record->EventHeader.TimeStamp.LowPart;
    ft.HighPart = record->EventHeader.TimeStamp.HighPart;
    event.timestamp_ms = (ft.QuadPart - 116444736000000000ULL) / 10000;

    // 使用 EventRecordParser 解析事件
    event.type = BehaviorEventType::Unknown;
    parser_.Parse(record, event, tl_is_kernel_consumer_);

    // 临时调试：打印 TDH schema 信息和 UserData hex dump
    if (tl_is_kernel_consumer_ && record->UserData && record->UserDataLength > 0) {
        USHORT op = record->EventHeader.EventDescriptor.Opcode;
        USHORT ev_id = record->EventHeader.EventDescriptor.Id;
        const GUID& pguid = record->EventHeader.ProviderId;
        char guid_str[64];
        sprintf_s(guid_str, "{%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x}",
            pguid.Data1, pguid.Data2, pguid.Data3,
            pguid.Data4[0], pguid.Data4[1], pguid.Data4[2], pguid.Data4[3],
            pguid.Data4[4], pguid.Data4[5], pguid.Data4[6], pguid.Data4[7]);
        if (op == 10 || op == 1) {
            // 尝试直接获取 TDH schema 信息
            ULONG buf_size2 = 0;
            ULONG tdh_status = TdhGetEventInformation(record, 0, nullptr, nullptr, &buf_size2);
            if (tdh_status == ERROR_INSUFFICIENT_BUFFER) {
                std::vector<BYTE> tdh_buf(buf_size2);
                auto* tdh_info = reinterpret_cast<PTRACE_EVENT_INFO>(tdh_buf.data());
                tdh_status = TdhGetEventInformation(record, 0, nullptr, tdh_info, &buf_size2);
                if (tdh_status == ERROR_SUCCESS) {
                    logger_->Log(LogLevel::Debug,
                        std::format("ETW: TDH op={} prop_count={}",
                            op, tdh_info->TopLevelPropertyCount));
                    // 打印所有属性名
                    if (tdh_info->TopLevelPropertyCount > 0) {
                        std::string all_props;
                        for (ULONG pi = 0; pi < tdh_info->TopLevelPropertyCount; ++pi) {
                            auto& prop2 = tdh_info->EventPropertyInfoArray[pi];
                            LPCWSTR pname2 = reinterpret_cast<LPCWSTR>(
                                reinterpret_cast<BYTE*>(tdh_info) + prop2.NameOffset);
                            int wlen2 = 0;
                            while (pname2[wlen2] != L'\0') ++wlen2;
                            if (wlen2 > 0) {
                                int utf8_len2 = WideCharToMultiByte(CP_UTF8, 0, pname2, wlen2, nullptr, 0, nullptr, nullptr);
                                if (utf8_len2 > 0) {
                                    std::string pname_utf8_2(utf8_len2, '\0');
                                    WideCharToMultiByte(CP_UTF8, 0, pname2, wlen2, &pname_utf8_2[0], utf8_len2, nullptr, nullptr);
                                    if (!all_props.empty()) all_props += ", ";
                                    all_props += pname_utf8_2;
                                }
                            }
                        }
                        logger_->Log(LogLevel::Debug,
                            std::format("ETW: TDH op={} props=[{}]", op, all_props));

                        auto& prop = tdh_info->EventPropertyInfoArray[0];
                        LPCWSTR pname = reinterpret_cast<LPCWSTR>(
                            reinterpret_cast<BYTE*>(tdh_info) + prop.NameOffset);
                        // 转换宽字符串到窄字符串用于日志
                        int wlen = 0;
                        while (pname[wlen] != L'\0') ++wlen;
                        std::string pname_utf8;
                        if (wlen > 0) {
                            int utf8_len = WideCharToMultiByte(CP_UTF8, 0, pname, wlen, nullptr, 0, nullptr, nullptr);
                            if (utf8_len > 0) {
                                pname_utf8.resize(utf8_len);
                                WideCharToMultiByte(CP_UTF8, 0, pname, wlen, &pname_utf8[0], utf8_len, nullptr, nullptr);
                            }
                        }
                        logger_->Log(LogLevel::Debug,
                            std::format("ETW: TDH first_prop=\"{}\" inType={} outType={}",
                                pname_utf8, prop.nonStructType.InType, prop.nonStructType.OutType));
                    }
                }
            } else {
                logger_->Log(LogLevel::Debug,
                    std::format("ETW: TDH op={} no_schema status={}", op, tdh_status));
            }
            BYTE* d = reinterpret_cast<BYTE*>(record->UserData);
            ULONG dl = record->UserDataLength < 64 ? record->UserDataLength : 64;
            char hex[256];
            char* p = hex;
            for (ULONG i = 0; i < dl; ++i) {
                p += sprintf_s(p, sizeof(hex) - (p - hex), "%02x ", d[i]);
            }
            *p = '\0';
            logger_->Log(LogLevel::Debug,
                std::format("ETW: HEX op={} pid={} tid={} data_len={} hex={}",
                    op, record->EventHeader.ProcessId, record->EventHeader.ThreadId,
                    record->UserDataLength, hex));
        }
    }

    // 过滤 Unknown 事件（减少噪音）
    if (event.type == BehaviorEventType::Unknown) {
        return;
    }

    logger_->Log(LogLevel::Debug,
        std::format("ETW: ProcessEventRecord type={} pid={}", static_cast<int>(event.type), event.pid));

    total_events_.fetch_add(1, std::memory_order_relaxed);
    ring_buffer_->Push(std::move(event));
}

void EtwMonitorImpl::DispatchLoop()
{
    std::vector<BehaviorEvent> batch;
    constexpr size_t MAX_BATCH = 100;

    // T6.10: 预计算过滤集合（空 = 全部通过）
    const bool has_filter = !filter_types_.empty();

    while (running_.load(std::memory_order_acquire)) {
        batch.clear();
        ring_buffer_->PopBatch(batch, MAX_BATCH);

        if (!batch.empty()) {
            if (has_filter) {
                batch.erase(
                    std::remove_if(batch.begin(), batch.end(),
                        [this](const BehaviorEvent& ev) {
                            int t = static_cast<int>(ev.type);
                            return std::find(filter_types_.begin(), filter_types_.end(), t)
                                   == filter_types_.end();
                        }),
                    batch.end());
            }
            if (!batch.empty()) {
                callback_(batch);
            }
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    }

    batch.clear();
    ring_buffer_->PopBatch(batch, MAX_BATCH);
    if (has_filter && !batch.empty()) {
        batch.erase(
            std::remove_if(batch.begin(), batch.end(),
                [this](const BehaviorEvent& ev) {
                    int t = static_cast<int>(ev.type);
                    return std::find(filter_types_.begin(), filter_types_.end(), t)
                           == filter_types_.end();
                }),
            batch.end());
    }
    if (!batch.empty()) {
        callback_(batch);
    }
}

void EtwMonitorImpl::DegradedMonitorLoop()
{
    bool first_poll = true;  // 首次轮询只建基线，不产生 ProcessStart 噪音

    while (running_.load(std::memory_order_acquire)) {
        std::vector<BehaviorEvent> events;

        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snap != INVALID_HANDLE_VALUE) {
            PROCESSENTRY32W pe = {};
            pe.dwSize = sizeof(pe);

            std::map<uint32_t, BehaviorEvent> current_procs;

            if (Process32FirstW(snap, &pe)) {
                do {
                    uint32_t pid = pe.th32ProcessID;
                    if (pid == 0 || pid == 4) continue;

                    BehaviorEvent ev;
                    ev.type = BehaviorEventType::ProcessStart;
                    ev.pid = pid;
                    ev.parent_pid = pe.th32ParentProcessID;

                    std::wstring wexe(pe.szExeFile);
                    ev.image_path = std::string(wexe.begin(), wexe.end());

                    auto now = std::chrono::system_clock::now();
                    ev.timestamp_ms = std::chrono::duration_cast<
                        std::chrono::milliseconds>(
                        now.time_since_epoch()).count();

                    current_procs[pid] = ev;
                } while (Process32NextW(snap, &pe));
            }
            CloseHandle(snap);

            std::lock_guard<std::mutex> lk(degraded_mutex_);

            // 首次轮询：仅建立基线，不产生事件（避免把全系统进程当新进程）
            if (!first_poll) {
                for (auto& [pid, ev] : current_procs) {
                    if (degraded_known_procs_.find(pid) == degraded_known_procs_.end()) {
                        events.push_back(ev);
                    }
                }

                for (auto it = degraded_known_procs_.begin();
                     it != degraded_known_procs_.end();) {
                    if (current_procs.find(it->first) == current_procs.end()) {
                        BehaviorEvent stop_ev;
                        stop_ev.type = BehaviorEventType::ProcessStop;
                        stop_ev.pid = it->first;
                        stop_ev.timestamp_ms = std::chrono::duration_cast<
                            std::chrono::milliseconds>(
                            std::chrono::system_clock::now().time_since_epoch()).count();
                        events.push_back(stop_ev);
                        it = degraded_known_procs_.erase(it);
                    } else {
                        ++it;
                    }
                }
            }

            degraded_known_procs_ = std::move(current_procs);
            first_poll = false;
        }

        // 降级模式网络轮询（仅当启用）
        if (degraded_net_polling_) {
            auto net_events = PollNetworkEvents();
            events.insert(events.end(), net_events.begin(), net_events.end());
        }

        for (auto& ev : events) {
            PushEvent(std::move(ev));
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
}

// 线程安全 Push（降级模式下进程轮询线程与文件监控线程并发调用）
void EtwMonitorImpl::PushEvent(BehaviorEvent&& ev)
{
    std::lock_guard<std::mutex> lk(push_mutex_);
    total_events_.fetch_add(1, std::memory_order_relaxed);
    ring_buffer_->Push(std::move(ev));
}

// 降级模式文件系统监控：ReadDirectoryChangesW 递归监控配置目录
// 生成 FileCreate / FileWrite / FileDelete 事件
void EtwMonitorImpl::DegradedFileMonitorLoop()
{
    // 为每个监控目录打开目录句柄 + 建立 wake 事件
    struct DirWatch {
        HANDLE dir_handle = INVALID_HANDLE_VALUE;
        std::string dir_path;            // UTF-8，用于路径拼接
        std::wstring dir_wide;           // 宽字符，用于 API
        std::vector<BYTE> buffer;        // notify 缓冲区
        OVERLAPPED ov = {};              // 异步 I/O
    };

    std::vector<DirWatch> watches;
    for (const auto& dir : degraded_monitor_dirs_) {
        if (dir.empty()) continue;

        std::wstring wdir(dir.begin(), dir.end());
        HANDLE h = CreateFileW(wdir.c_str(), FILE_LIST_DIRECTORY,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, nullptr);
        if (h == INVALID_HANDLE_VALUE) {
            logger_->Log(LogLevel::Warn,
                "ETW: ReadDirectoryChangesW open failed for '" + dir +
                "': " + std::to_string(GetLastError()));
            continue;
        }

        DirWatch w;
        w.dir_handle = h;
        w.dir_path = dir;
        w.dir_wide = wdir;
        w.buffer.resize(64 * 1024);
        w.ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (w.ov.hEvent == nullptr) {
            CloseHandle(h);
            continue;
        }
        watches.push_back(std::move(w));
    }

    if (watches.empty()) {
        logger_->Log(LogLevel::Warn, "ETW: no valid directories to watch, file monitor exiting");
        return;
    }

    // 首次建立基线：ReadDirectoryChangesW 完成后的首轮通知视为已有文件状态，不产生事件
    // （与进程轮询首次基线策略一致，避免把目录已有内容当成新文件）

    // wake 事件：Shutdown 时由 Stop() 置位，唤醒等待以便 join
    HANDLE wake_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    degraded_file_wake_.store(wake_event, std::memory_order_release);

    // 发起所有目录的初始异步读
    for (auto& w : watches) {
        DWORD filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME |
                       FILE_NOTIFY_CHANGE_LAST_WRITE | FILE_NOTIFY_CHANGE_SIZE;
        if (!ReadDirectoryChangesW(w.dir_handle, w.buffer.data(),
                static_cast<DWORD>(w.buffer.size()), TRUE, filter,
                nullptr, &w.ov, nullptr)) {
            logger_->Log(LogLevel::Warn,
                "ETW: ReadDirectoryChangesW start failed for '" + w.dir_path +
                "': " + std::to_string(GetLastError()));
        }
    }

    // 主循环：等待任意目录变化或 wake 事件
    std::vector<HANDLE> handles;
    handles.push_back(wake_event);
    for (auto& w : watches) {
        handles.push_back(w.ov.hEvent);
    }

    while (running_.load(std::memory_order_acquire)) {
        DWORD wait = WaitForMultipleObjects(
            static_cast<DWORD>(handles.size()), handles.data(), FALSE, 500);

        if (wait == WAIT_TIMEOUT) {
            continue;
        }
        if (wait == WAIT_FAILED) {
            logger_->Log(LogLevel::Warn,
                "ETW: WaitForMultipleObjects failed: " + std::to_string(GetLastError()));
            break;
        }
        if (wait == WAIT_OBJECT_0) {
            // wake 事件（Shutdown），退出
            break;
        }

        // 某个目录有变化
        DWORD idx = wait - WAIT_OBJECT_0 - 1;
        if (idx >= watches.size()) {
            // 未知句柄，重置并继续
            for (size_t i = 1; i < handles.size(); ++i) {
                ResetEvent(handles[i]);
            }
            continue;
        }
        DirWatch& w = watches[idx];

        DWORD bytes = 0;
        BOOL ok = GetOverlappedResult(w.dir_handle, &w.ov, &bytes, FALSE);
        if (ok && bytes > 0) {
            std::vector<BehaviorEvent> file_events;
            ParseFileNotifyEvents(w.dir_path, w.buffer.data(), bytes, file_events);
            if (!file_events.empty()) {
                for (auto& ev : file_events) {
                    PushEvent(std::move(ev));
                }
            }
        }

        ResetEvent(w.ov.hEvent);
        // 重新发起该目录的异步读
        DWORD filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME |
                       FILE_NOTIFY_CHANGE_LAST_WRITE | FILE_NOTIFY_CHANGE_SIZE;
        if (!ReadDirectoryChangesW(w.dir_handle, w.buffer.data(),
                static_cast<DWORD>(w.buffer.size()), TRUE, filter,
                nullptr, &w.ov, nullptr)) {
            logger_->Log(LogLevel::Warn,
                "ETW: ReadDirectoryChangesW re-arm failed for '" + w.dir_path +
                "': " + std::to_string(GetLastError()));
        }
    }

    // 清理
    degraded_file_wake_.store(nullptr, std::memory_order_release);
    for (auto& w : watches) {
        if (w.ov.hEvent) CloseHandle(w.ov.hEvent);
        CloseHandle(w.dir_handle);
    }
    if (wake_event) CloseHandle(wake_event);
    logger_->Log(LogLevel::Info, "ETW: degraded file monitor stopped");
}

// 解析 FILE_NOTIFY_INFORMATION 链 → BehaviorEvent
void EtwMonitorImpl::ParseFileNotifyEvents(const std::string& base_path, const BYTE* data,
                                           DWORD len, std::vector<BehaviorEvent>& out)
{
    DWORD offset = 0;
    while (offset + sizeof(FILE_NOTIFY_INFORMATION) <= len) {
        const auto* fni = reinterpret_cast<const FILE_NOTIFY_INFORMATION*>(data + offset);

        // 文件名长度（字节），转换到 UTF-8
        DWORD name_len_chars = fni->FileNameLength / sizeof(WCHAR);
        std::wstring wname(fni->FileName, name_len_chars);
        std::string name;
        int utf8_len = WideCharToMultiByte(CP_UTF8, 0, wname.c_str(),
            static_cast<int>(wname.size()), nullptr, 0, nullptr, nullptr);
        if (utf8_len > 0) {
            name.resize(utf8_len);
            WideCharToMultiByte(CP_UTF8, 0, wname.c_str(),
                static_cast<int>(wname.size()), &name[0], utf8_len, nullptr, nullptr);
        }

        if (!name.empty()) {
            BehaviorEvent ev;
            ev.pid = 0;  // ReadDirectoryChangesW 不提供 PID
            ev.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            // 拼接完整路径
            ev.file_path = base_path;
            if (!ev.file_path.empty() && ev.file_path.back() != '\\' && ev.file_path.back() != '/') {
                ev.file_path += "\\";
            }
            ev.file_path += name;

            switch (fni->Action) {
            case FILE_ACTION_ADDED:            ev.type = BehaviorEventType::FileCreate; break;
            case FILE_ACTION_REMOVED:          ev.type = BehaviorEventType::FileDelete; break;
            case FILE_ACTION_MODIFIED:         ev.type = BehaviorEventType::FileWrite;  break;
            case FILE_ACTION_RENAMED_OLD_NAME: ev.type = BehaviorEventType::FileDelete; break;
            case FILE_ACTION_RENAMED_NEW_NAME: ev.type = BehaviorEventType::FileCreate; break;
            default:                           ev.type = BehaviorEventType::Unknown;    break;
            }

            if (ev.type != BehaviorEventType::Unknown) {
                out.push_back(std::move(ev));
            }
        }

        if (fni->NextEntryOffset == 0) {
            break;
        }
        offset += fni->NextEntryOffset;
    }
}

// 降级模式网络轮询：对比 TCP/UDP 连接快照，检测新连接 → TcpConnect/UdpSend
// 首次调用只建立基线（避免把存量连接当成新事件）
std::vector<BehaviorEvent> EtwMonitorImpl::PollNetworkEvents()
{
    std::vector<BehaviorEvent> events;
    bool first_poll = false;
    {
        std::lock_guard<std::mutex> lk(degraded_net_mutex_);
        first_poll = !degraded_net_baseline_ready_;
    }

    // ---- TCP 连接表（IPv4 + IPv6）----
    for (int family : {AF_INET, AF_INET6}) {
        ULONG size = 0;
        DWORD r = GetExtendedTcpTable(nullptr, &size, FALSE, family,
                                      TCP_TABLE_OWNER_PID_ALL, 0);
        if (r != ERROR_INSUFFICIENT_BUFFER || size == 0) {
            continue;
        }
        std::vector<BYTE> buf(size);
        r = GetExtendedTcpTable(buf.data(), &size, FALSE, family,
                                TCP_TABLE_OWNER_PID_ALL, 0);
        if (r != NO_ERROR) {
            continue;
        }

        auto emit_tcp = [&](uint32_t pid, const std::string& local, uint16_t local_port,
                            const std::string& remote, uint16_t remote_port) {
            BehaviorEvent ev;
            ev.type = BehaviorEventType::TcpConnect;
            ev.pid = pid;
            ev.local_addr = local;
            ev.local_port = local_port;
            ev.remote_addr = remote;
            ev.remote_port = remote_port;
            ev.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            events.push_back(std::move(ev));
        };

        if (family == AF_INET) {
            const auto* table = reinterpret_cast<MIB_TCPTABLE_OWNER_PID*>(buf.data());
            std::set<std::string> current;
            for (DWORD i = 0; i < table->dwNumEntries; ++i) {
                const auto& row = table->table[i];
                // 仅关注已建立连接（ESTABLISHED=5），避免大量 LISTEN/SYN 噪音
                if (row.dwState != MIB_TCP_STATE_ESTAB) {
                    continue;
                }
                char local[64] = {}, remote[64] = {};
                {
                    DWORD a = ntohl(row.dwLocalAddr);
                    snprintf(local, sizeof(local), "%u.%u.%u.%u",
                        (a >> 24) & 0xFF, (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF);
                    DWORD b = ntohl(row.dwRemoteAddr);
                    snprintf(remote, sizeof(remote), "%u.%u.%u.%u",
                        (b >> 24) & 0xFF, (b >> 16) & 0xFF, (b >> 8) & 0xFF, b & 0xFF);
                }
                uint16_t lp = static_cast<uint16_t>(ntohs(static_cast<u_short>(row.dwLocalPort)));
                uint16_t rp = static_cast<uint16_t>(ntohs(static_cast<u_short>(row.dwRemotePort)));
                std::string key = std::to_string(row.dwOwningPid) + "|" + local + ":" +
                                  std::to_string(lp) + "->" + remote + ":" + std::to_string(rp);
                current.insert(key);

                std::lock_guard<std::mutex> lk(degraded_net_mutex_);
                if (!first_poll && degraded_known_tcp_.find(key) == degraded_known_tcp_.end()) {
                    emit_tcp(row.dwOwningPid, local, lp, remote, rp);
                }
            }
            std::lock_guard<std::mutex> lk(degraded_net_mutex_);
            degraded_known_tcp_ = std::move(current);
        } else {
            const auto* table = reinterpret_cast<MIB_TCP6TABLE_OWNER_PID*>(buf.data());
            std::set<std::string> current;
            for (DWORD i = 0; i < table->dwNumEntries; ++i) {
                const auto& row = table->table[i];
                if (row.dwState != MIB_TCP_STATE_ESTAB) {
                    continue;
                }
                char local[64] = {}, remote[64] = {};
                {
                    // IPv6 地址格式化为可读字符串（InetNtopA 优于手工 snprintf）
                    if (InetNtopA(AF_INET6, row.ucLocalAddr, local, sizeof(local)) == nullptr) {
                        strcpy_s(local, "<unresolved>");
                    }
                    if (InetNtopA(AF_INET6, row.ucRemoteAddr, remote, sizeof(remote)) == nullptr) {
                        strcpy_s(remote, "<unresolved>");
                    }
                }
                uint16_t lp = static_cast<uint16_t>(ntohs(static_cast<u_short>(row.dwLocalPort)));
                uint16_t rp = static_cast<uint16_t>(ntohs(static_cast<u_short>(row.dwRemotePort)));
                std::string key = std::to_string(row.dwOwningPid) + "|" + local + ":" +
                                  std::to_string(lp) + "->" + remote + ":" + std::to_string(rp);
                current.insert(key);

                std::lock_guard<std::mutex> lk(degraded_net_mutex_);
                if (!first_poll && degraded_known_tcp_.find(key) == degraded_known_tcp_.end()) {
                    emit_tcp(row.dwOwningPid, local, lp, remote, rp);
                }
            }
            std::lock_guard<std::mutex> lk(degraded_net_mutex_);
            degraded_known_tcp_ = std::move(current);
        }
    }

    // ---- UDP 端点表（IPv4 + IPv6）----
    for (int family : {AF_INET, AF_INET6}) {
        ULONG size = 0;
        DWORD r = GetExtendedUdpTable(nullptr, &size, FALSE, family, UDP_TABLE_OWNER_PID, 0);
        if (r != ERROR_INSUFFICIENT_BUFFER || size == 0) {
            continue;
        }
        std::vector<BYTE> buf(size);
        r = GetExtendedUdpTable(buf.data(), &size, FALSE, family, UDP_TABLE_OWNER_PID, 0);
        if (r != NO_ERROR) {
            continue;
        }

        auto emit_udp = [&](uint32_t pid, const std::string& local, uint16_t local_port) {
            BehaviorEvent ev;
            ev.type = BehaviorEventType::UdpSend;
            ev.pid = pid;
            ev.local_addr = local;
            ev.local_port = local_port;
            ev.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            events.push_back(std::move(ev));
        };

        if (family == AF_INET) {
            const auto* table = reinterpret_cast<MIB_UDPTABLE_OWNER_PID*>(buf.data());
            std::set<std::string> current;
            for (DWORD i = 0; i < table->dwNumEntries; ++i) {
                const auto& row = table->table[i];
                char local[64] = {};
                DWORD a = ntohl(row.dwLocalAddr);
                snprintf(local, sizeof(local), "%u.%u.%u.%u",
                    (a >> 24) & 0xFF, (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF);
                uint16_t lp = static_cast<uint16_t>(ntohs(static_cast<u_short>(row.dwLocalPort)));
                std::string key = std::to_string(row.dwOwningPid) + "|" + local + ":" + std::to_string(lp);
                current.insert(key);

                std::lock_guard<std::mutex> lk(degraded_net_mutex_);
                if (!first_poll && degraded_known_udp_.find(key) == degraded_known_udp_.end()) {
                    emit_udp(row.dwOwningPid, local, lp);
                }
            }
            std::lock_guard<std::mutex> lk(degraded_net_mutex_);
            degraded_known_udp_ = std::move(current);
        } else {
            const auto* table = reinterpret_cast<MIB_UDP6TABLE_OWNER_PID*>(buf.data());
            std::set<std::string> current;
            for (DWORD i = 0; i < table->dwNumEntries; ++i) {
                const auto& row = table->table[i];
                char local[64] = {};
                if (InetNtopA(AF_INET6, row.ucLocalAddr, local, sizeof(local)) == nullptr) {
                    strcpy_s(local, "<unresolved>");
                }
                uint16_t lp = static_cast<uint16_t>(ntohs(static_cast<u_short>(row.dwLocalPort)));
                std::string key = std::to_string(row.dwOwningPid) + "|" + local + ":" + std::to_string(lp);
                current.insert(key);

                std::lock_guard<std::mutex> lk(degraded_net_mutex_);
                if (!first_poll && degraded_known_udp_.find(key) == degraded_known_udp_.end()) {
                    emit_udp(row.dwOwningPid, local, lp);
                }
            }
            std::lock_guard<std::mutex> lk(degraded_net_mutex_);
            degraded_known_udp_ = std::move(current);
        }
    }

    // 首次轮询完成，建立基线（后续轮询才开始产生网络事件）
    {
        std::lock_guard<std::mutex> lk(degraded_net_mutex_);
        degraded_net_baseline_ready_ = true;
    }

    return events;
}

Result<void> EtwMonitorImpl::Stop()
{
    if (!running_.load(std::memory_order_acquire)) {
        return Result<void>::Ok();
    }

    running_.store(false, std::memory_order_release);

    for (auto& session : sessions_) {
        StopSession(session);
    }
    sessions_.clear();

    if (dispatch_thread_.joinable()) {
        dispatch_thread_.join();
    }

    if (degraded_thread_.joinable()) {
        degraded_thread_.join();
    }

    // 唤醒并等待文件监控线程退出
    if (degraded_file_thread_.joinable()) {
        HANDLE wake = degraded_file_wake_.load(std::memory_order_acquire);
        if (wake) {
            SetEvent(wake);
        }
        degraded_file_thread_.join();
    }

    {
        std::lock_guard<std::mutex> lk(instance_mutex_);
        instance_ = nullptr;
    }

    started_.store(false, std::memory_order_release);
    logger_->Log(LogLevel::Info,
        "ETW: Monitor stopped (total_events=" +
        std::to_string(GetTotalEventCount()) +
        ", dropped=" + std::to_string(GetDroppedCount()) + ")");
    return Result<void>::Ok();
}

uint64_t EtwMonitorImpl::GetDroppedCount() const
{
    if (ring_buffer_) {
        return ring_buffer_->GetDroppedCount();
    }
    return 0;
}

} // namespace winsandbox
