// =============================================================================
// EventRecordParser - ETW EVENT_RECORD 解析器实现
//
// 解析策略：
//   1. NT Kernel Logger 事件：用 opcode + EventDescriptor.Id 区分类型
//      属性通过 UserData 偏移提取（MOF 格式，固定布局）
//   2. Manifest-based provider 事件：用 TdhGetEventInformation 获取 schema
//      按属性名提取字段值
//
// =============================================================================
#include "infra/etw/EventRecordParser.hpp"

// InetNtopW（IP 地址格式化）
#include <ws2tcpip.h>

namespace winsandbox {

// Manifest-based providers（Session 2/3 使用）
static const GUID kKernelFileGuid =
    {0xedd08927, 0x9cc4, 0x4e65, {0xb9, 0x70, 0xcb, 0x60, 0x5d, 0x8e, 0x3e, 0x27}};
static const GUID kKernelRegistryGuid =
    {0xae53722e, 0xc863, 0x47d4, {0xa8, 0x3a, 0xa5, 0xd2, 0xc7, 0xc6, 0xe5, 0xa0}};
static const GUID kKernelNetworkGuid =
    {0x7dd42a49, 0x5329, 0x4931, {0x9a, 0x5e, 0x4c, 0x3d, 0x8a, 0x5b, 0x2e, 0x1a}};
static const GUID kKernelProcessGuid =
    {0x22fb2cd6, 0x0e7b, 0x422b, {0xa0, 0xc7, 0x2f, 0xad, 0x1f, 0xd0, 0xe7, 0xb4}};

// NT Kernel Logger 子 provider GUID（PROCESS_TRACE_MODE_EVENT_RECORD 模式下，
// NT Kernel Logger 不再以 SystemTraceControlGuid 投递，而是用具体子组 GUID）
static const GUID kNtProcessGuid =
    {0x68fdd900, 0x4a3e, 0x11d1, {0xb6, 0xaf, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
static const GUID kNtThreadGuid =
    {0x3d6fa8d0, 0xfe05, 0x11d0, {0xbd, 0x73, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
static const GUID kNtThreadGuid2 =
    {0x3d6fa8d1, 0xfe05, 0x11d0, {0xbd, 0x73, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
static const GUID kNtImageLoadGuid =
    {0xb3e1e4d0, 0x418f, 0x11d1, {0xa1, 0x05, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
static const GUID kNtDiskIoGuid =
    {0xd4378026, 0x418f, 0x11d1, {0xa1, 0x05, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
static const GUID kNtNetworkGuid =
    {0x7b417c80, 0x418f, 0x11d1, {0xa1, 0x05, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
static const GUID kNtRegistryGuid =
    {0xae53722e, 0xc863, 0x47d4, {0xa8, 0x3a, 0xa5, 0xd2, 0xc7, 0xc6, 0xe5, 0xa0}};
// 01853a65-418f-4f36 = Kernel-File (NT Kernel Logger 子组)
static const GUID kNtFileIoGuid =
    {0x01853a65, 0x418f, 0x4f36, {0xa1, 0x05, 0x00, 0xc0, 0x4f, 0xa3, 0x7a, 0x87}};
// Windows 10+ 使用不同的子组 GUID
// 90cbdc39-4a3e-11d1 = Kernel-File (Windows 10+ NT Kernel Logger 子组)
static const GUID kNtFileIoGuid2 =
    {0x90cbdc39, 0x4a3e, 0x11d1, {0xbf, 0x43, 0x00, 0xc0, 0x4f, 0xb9, 0x26, 0x3d}};
// 9a280ac0-c8e0-11d1 = Kernel-Registry (Windows 10+ NT Kernel Logger 子组)
static const GUID kNtRegistryGuid2 =
    {0x9a280ac0, 0xc8e0, 0x11d1, {0x84, 0xe2, 0x00, 0xc0, 0x4f, 0xb9, 0x98, 0xa2}};

// =============================================================================
// 公共接口
// =============================================================================

void EventRecordParser::Parse(PEVENT_RECORD record, BehaviorEvent& out, bool is_kernel_session)
{
    if (!record) return;

    const auto& hdr = record->EventHeader;
    const GUID& provider = hdr.ProviderId;

    // NT Kernel Logger 事件：provider GUID 因 Windows 版本而异，不能硬编码匹配
    // 通过 is_kernel_session 标记路由，先尝试 TDH schema 解析，失败则 MOF 回退
    if (is_kernel_session) {
        ParseKernelSessionEvent(record, out);
        return;
    }

    // Manifest-based providers（Session 2/3）
    if (provider == kKernelFileGuid) {
        ParseFileEvent(record, out);
    } else if (provider == kKernelRegistryGuid) {
        ParseRegistryEvent(record, out);
    } else if (provider == kKernelNetworkGuid) {
        ParseNetworkEvent(record, out);
    } else if (provider == kKernelProcessGuid) {
        ParseKernelProcessEvent(record, out);
    }
}

// =============================================================================
// NT Kernel Logger 事件解析（PROCESS_TRACE_MODE_EVENT_RECORD 模式）
//
// Provider GUID 因 Windows 版本而异，不能硬编码匹配。
// 策略：先尝试 TDH schema 解析，失败则按 opcode MOF 回退。
// =============================================================================

static std::string GuidToString(const GUID& g)
{
    char buf[64];
    sprintf_s(buf, "{%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x}",
              g.Data1, g.Data2, g.Data3,
              g.Data4[0], g.Data4[1], g.Data4[2], g.Data4[3],
              g.Data4[4], g.Data4[5], g.Data4[6], g.Data4[7]);
    return buf;
}

void EventRecordParser::ParseKernelSessionEvent(PEVENT_RECORD record, BehaviorEvent& out)
{
    if (!record->UserData || record->UserDataLength == 0) {
        out.type = BehaviorEventType::Unknown;
        return;
    }

    const GUID& provider = record->EventHeader.ProviderId;
    USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
    USHORT event_id = record->EventHeader.EventDescriptor.Id;
    BYTE* data = reinterpret_cast<BYTE*>(record->UserData);
    ULONG data_len = record->UserDataLength;

    // 先尝试 TDH schema 解析（EVENT_RECORD 格式的字段偏移只能通过 TDH 获取）
    auto* info = GetEventInfo(record);
    if (info) {
        // 搜索所有已知属性名
        int pid_idx = FindPropertyIndex(info, L"ProcessID");
        if (pid_idx < 0) pid_idx = FindPropertyIndex(info, L"ProcessId");
        int img_idx = FindPropertyIndex(info, L"ImageName");
        if (img_idx < 0) img_idx = FindPropertyIndex(info, L"ImagePath");
        if (img_idx < 0) img_idx = FindPropertyIndex(info, L"ImageFileName");  // NT Kernel Logger 使用
        int file_idx = FindPropertyIndex(info, L"FileName");
        if (file_idx < 0) file_idx = FindPropertyIndex(info, L"FilePath");
        if (file_idx < 0) file_idx = FindPropertyIndex(info, L"OpenPath");
        int key_idx = FindPropertyIndex(info, L"KeyName");
        if (key_idx < 0) key_idx = FindPropertyIndex(info, L"KeyPath");
        int tid_idx = FindPropertyIndex(info, L"ThreadId");

        // 0) 镜像加载事件：按 opcode 判断（provider GUID 因 Windows 版本而异）
        // opcode 10 同时被 ImageLoad 和网络 TCP 事件使用，通过 TDH schema 中的
        // ImageBase/ImageSize 属性区分真正的 ImageLoad 事件
        if (opcode == 10) {
            int img_base_idx = FindPropertyIndex(info, L"ImageBase");
            int img_size_idx = FindPropertyIndex(info, L"ImageSize");
            if (img_base_idx >= 0 || img_size_idx >= 0) {
                out.type = BehaviorEventType::ImageLoad;
                // ImageLoad schema 使用 ProcessId 属性名
                if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
                // ImageLoad schema 使用 FileName 属性名作为镜像路径
                if (file_idx >= 0) {
                    auto ws = ExtractWideString(record, info, file_idx);
                    if (!ws.empty()) out.image_path = WideToUtf8(ws);
                }
                return;
            }
        }

        // 1) 进程/镜像事件：按 provider GUID 优先路由
        if (provider == kNtProcessGuid) {
            // 进程事件
            switch (opcode) {
            case 1: case 5: case 32: out.type = BehaviorEventType::ProcessStart; break;
            case 2: case 6: out.type = BehaviorEventType::ProcessStop; break;
            default: out.type = (opcode == 1 || opcode == 5 || opcode == 32)
                                ? BehaviorEventType::ProcessStart
                                : BehaviorEventType::ProcessStop; break;
            }
            if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
            int pidx = FindPropertyIndex(info, L"ParentProcessID");
            if (pidx < 0) pidx = FindPropertyIndex(info, L"ParentProcessId");
            if (pidx >= 0) out.parent_pid = ExtractUInt32(record, info, pidx);
            if (img_idx >= 0) {
                auto ws = ExtractWideString(record, info, img_idx);
                if (!ws.empty()) out.image_path = WideToUtf8(ws);
            } else if (opcode == 1 || opcode == 5 || opcode == 32) {
                // TDH schema 缺少 ImageName，从 UserData 以 MOF 偏移提取
                bool is_64_bit = (record->EventHeader.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;
                ExtractNtKernelString(data, data_len, is_64_bit ? 24 : 16, out.image_path);
            }
            return;
        }

        if (provider == kNtImageLoadGuid) {
            // 镜像加载事件
            out.type = BehaviorEventType::ImageLoad;
            if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
            if (img_idx >= 0) {
                auto ws = ExtractWideString(record, info, img_idx);
                if (!ws.empty()) out.image_path = WideToUtf8(ws);
            }
            return;
        }

        if (provider == kNtThreadGuid || provider == kNtThreadGuid2) {
            // 线程事件
            switch (opcode) {
            case 1: case 3: out.type = BehaviorEventType::ThreadStart; break;
            case 2: case 4: out.type = BehaviorEventType::ThreadStop; break;
            default: out.type = (opcode == 1 || opcode == 3)
                                ? BehaviorEventType::ThreadStart
                                : BehaviorEventType::ThreadStop; break;
            }
            if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
            if (tid_idx >= 0) out.tid = ExtractUInt32(record, info, tid_idx);
            return;
        }

        // 注册表事件：通过 provider GUID 判断
        if (provider == kNtRegistryGuid || provider == kNtRegistryGuid2) {
            ParseRegistryEvent(record, out);
            return;
        }

        // 2) 文件/注册表/网络事件：按属性名匹配
        if (file_idx >= 0) {
            // 文件事件
            if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
            auto ws = ExtractWideString(record, info, file_idx);
            if (!ws.empty()) out.file_path = WideToUtf8(ws);

// 检测 NtStatus
            int status_idx = FindPropertyIndex(info, L"NtStatus");
            if (status_idx < 0) status_idx = FindPropertyIndex(info, L"Status");
            if (status_idx >= 0) {
                UINT32 nt_status = ExtractUInt32(record, info, status_idx);
                if (nt_status == 0xC0000022) {
                    out.type = BehaviorEventType::AccessDenied;
                    out.operation = "file_access";
                    return;
                }
            }

            switch (opcode) {
            case 36: out.type = BehaviorEventType::FileDelete; break;
            default: out.type = BehaviorEventType::FileCreate; break;
            }
            return;
        }

        if (key_idx >= 0) {
            // 注册表事件
            if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
            auto ws = ExtractWideString(record, info, key_idx);
            if (!ws.empty()) {
                out.key_path = WideToUtf8(ws);
            } else {
                // TDH 提取失败，尝试从 UserData 开头以 null-terminated wide string 提取
                ExtractNtKernelString(data, data_len, 0, out.key_path);
            }

            int status_idx = FindPropertyIndex(info, L"NtStatus");
            if (status_idx < 0) status_idx = FindPropertyIndex(info, L"Status");
            if (status_idx >= 0) {
                UINT32 nt_status = ExtractUInt32(record, info, status_idx);
                if (nt_status == 0xC0000022) {
                    out.type = BehaviorEventType::AccessDenied;
                    out.operation = "registry_access";
                    return;
                }
            }

            switch (opcode) {
            case 32: out.type = BehaviorEventType::RegistryCreateKey; break;
            case 34: case 37: out.type = BehaviorEventType::RegistryDeleteKey; break;
            default: out.type = BehaviorEventType::RegistrySetKey; break;
            }
            return;
        }

        if (tid_idx >= 0) {
            // 线程事件（无 process 但含 ThreadId 的回退）
            switch (opcode) {
            case 1: case 3: out.type = BehaviorEventType::ThreadStart; break;
            case 2: case 4: out.type = BehaviorEventType::ThreadStop; break;
            default: out.type = BehaviorEventType::ThreadStart; break;
            }
            if (pid_idx >= 0) out.pid = ExtractUInt32(record, info, pid_idx);
            out.tid = ExtractUInt32(record, info, tid_idx);
            return;
        }

        // 3) 网络事件：通过 provider GUID 判断
        if (provider == kNtNetworkGuid) {
            ParseNetworkEvent(record, out);
            return;
        }

        // 4) 进程事件回退：有 ProcessId 但没有 ImageName 的场景
        // NT Kernel Logger 的进程事件某些版本仅有 ProcessId 无 ImageName
        if (pid_idx >= 0) {
            switch (opcode) {
            case 1: case 5: case 32: out.type = BehaviorEventType::ProcessStart; break;
            case 2: case 6: out.type = BehaviorEventType::ProcessStop; break;
            default: out.type = BehaviorEventType::Unknown; return;
            }
            out.pid = ExtractUInt32(record, info, pid_idx);
            int pidx = FindPropertyIndex(info, L"ParentProcessID");
            if (pidx < 0) pidx = FindPropertyIndex(info, L"ParentProcessId");
            if (pidx >= 0) out.parent_pid = ExtractUInt32(record, info, pidx);
            // MOF 回退提取 image_path：TDH 缺少 ImageName 时从 UserData MOF 偏移提取
            if (opcode == 1 || opcode == 5 || opcode == 32) {
                bool is_64_bit = (record->EventHeader.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;
                ExtractNtKernelString(data, data_len, is_64_bit ? 24 : 16, out.image_path);
            }
            return;
        }
    }

    // MOF 回退：发生在 TDH 完全失败时
    // 使用 ParseNtKernelEvent 按 opcode 分类，但 UserData 字段不准确
    uint32_t header_pid = static_cast<uint32_t>(record->EventHeader.ProcessId);
    uint32_t header_tid = static_cast<uint32_t>(record->EventHeader.ThreadId);
    ParseNtKernelEvent(record, provider, out);

    // MOF 回退的 PID/TID 来自 EventHeader 而非 UserData（偏移不可靠）
    if (out.pid == 0 || out.pid == 0xFFFFFFFF) {
        out.pid = header_pid;
    }
    if (out.tid == 0 || out.tid == 0xFFFFFFFF) {
        out.tid = header_tid;
    }
}

// =============================================================================
// NT Kernel Logger 事件解析（MOF 格式）
// =============================================================================

void EventRecordParser::ParseNtKernelEvent(PEVENT_RECORD record, const GUID& provider, BehaviorEvent& out)
{
    const auto& hdr = record->EventHeader;
    USHORT opcode = hdr.EventDescriptor.Opcode;
    USHORT event_id = hdr.EventDescriptor.Id;
    bool is_64bit = (hdr.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;

    BYTE* data = reinterpret_cast<BYTE*>(record->UserData);
    ULONG data_len = record->UserDataLength;

    switch (opcode) {
    case 1:  // Start (MOF: event_id 区分 Process/Thread)
        if (event_id == 1) {
            out.type = BehaviorEventType::ProcessStart;
            if (data_len >= (is_64bit ? 24u : 16u)) {
                if (is_64bit) {
                    out.pid = *reinterpret_cast<ULONG*>(data + 8);
                    out.parent_pid = *reinterpret_cast<ULONG*>(data + 12);
                } else {
                    out.pid = *reinterpret_cast<ULONG*>(data + 4);
                    out.parent_pid = *reinterpret_cast<ULONG*>(data + 8);
                }
            }
            ExtractNtKernelString(data, data_len, is_64bit ? 24 : 16, out.image_path);
        } else if (event_id == 2) {
            out.type = BehaviorEventType::ThreadStart;
            if (data_len >= (is_64bit ? 16u : 12u)) {
                if (is_64bit) {
                    out.pid = *reinterpret_cast<ULONG*>(data + 8);
                    out.tid = *reinterpret_cast<ULONG*>(data + 12);
                } else {
                    out.pid = *reinterpret_cast<ULONG*>(data + 4);
                    out.tid = *reinterpret_cast<ULONG*>(data + 8);
                }
            }
        }
        break;

    case 2:  // Stop (MOF)
        if (event_id == 1) {
            out.type = BehaviorEventType::ProcessStop;
            if (data_len >= (is_64bit ? 24u : 16u)) {
                out.pid = *reinterpret_cast<ULONG*>(data + (is_64bit ? 8 : 4));
            }
        } else if (event_id == 2) {
            out.type = BehaviorEventType::ThreadStop;
            if (data_len >= (is_64bit ? 16u : 12u)) {
                if (is_64bit) {
                    out.pid = *reinterpret_cast<ULONG*>(data + 8);
                    out.tid = *reinterpret_cast<ULONG*>(data + 12);
                } else {
                    out.pid = *reinterpret_cast<ULONG*>(data + 4);
                    out.tid = *reinterpret_cast<ULONG*>(data + 8);
                }
            }
        }
        break;

    // EVENT_RECORD 格式 opcode：UserData 布局与旧 MOF 不同，不解析偏移
    // pid/tid 已由 EventHeader.ProcessId/ThreadId 设置（在 ProcessEventRecord 中）
    case 5:  // Process DCStart
        out.type = BehaviorEventType::ProcessStart;
        // DCStart 的 UserData 布局与 Start 相同
        if (data_len >= (is_64bit ? 24u : 16u)) {
            if (is_64bit) {
                out.pid = *reinterpret_cast<ULONG*>(data + 8);
                out.parent_pid = *reinterpret_cast<ULONG*>(data + 12);
            } else {
                out.pid = *reinterpret_cast<ULONG*>(data + 4);
                out.parent_pid = *reinterpret_cast<ULONG*>(data + 8);
            }
        }
        ExtractNtKernelString(data, data_len, is_64bit ? 24 : 16, out.image_path);
        break;

    case 32: // Process RundownStart (provider=kNtProcessGuid) 或 RegistryCreateKey (其他 provider)
        if (provider == kNtProcessGuid) {
            // Process RundownStart：UserData 布局与 Start 相同
            out.type = BehaviorEventType::ProcessStart;
            if (data_len >= (is_64bit ? 24u : 16u)) {
                if (is_64bit) {
                    out.pid = *reinterpret_cast<ULONG*>(data + 8);
                    out.parent_pid = *reinterpret_cast<ULONG*>(data + 12);
                } else {
                    out.pid = *reinterpret_cast<ULONG*>(data + 4);
                    out.parent_pid = *reinterpret_cast<ULONG*>(data + 8);
                }
            }
            ExtractNtKernelString(data, data_len, is_64bit ? 24 : 16, out.image_path);
        } else {
            // Registry CreateKey
            out.type = BehaviorEventType::RegistryCreateKey;
        }
        break;

    case 6:  // Process DCStop
        out.type = BehaviorEventType::ProcessStop;
        // DCStop 的 UserData 布局与 Stop 相同
        if (data_len >= (is_64bit ? 24u : 16u)) {
            out.pid = *reinterpret_cast<ULONG*>(data + (is_64bit ? 8 : 4));
        }
        break;

    case 3:  // Thread DCStart
        out.type = BehaviorEventType::ThreadStart;
        // Thread DCStart 的 UserData 布局与 Thread Start 相同
        if (data_len >= (is_64bit ? 16u : 12u)) {
            if (is_64bit) {
                out.pid = *reinterpret_cast<ULONG*>(data + 8);
                out.tid = *reinterpret_cast<ULONG*>(data + 12);
            } else {
                out.pid = *reinterpret_cast<ULONG*>(data + 4);
                out.tid = *reinterpret_cast<ULONG*>(data + 8);
            }
        }
        break;

    case 4:  // Thread DCStop
        out.type = BehaviorEventType::ThreadStop;
        // Thread DCStop 的 UserData 布局与 Thread Stop 相同
        if (data_len >= (is_64bit ? 16u : 12u)) {
            if (is_64bit) {
                out.pid = *reinterpret_cast<ULONG*>(data + 8);
                out.tid = *reinterpret_cast<ULONG*>(data + 12);
            } else {
                out.pid = *reinterpret_cast<ULONG*>(data + 4);
                out.tid = *reinterpret_cast<ULONG*>(data + 8);
            }
        }
        break;

    case 10: // Image Load (MOF format)
        out.type = BehaviorEventType::ImageLoad;
        if (is_64bit && data_len >= 24u) {
            out.pid = *reinterpret_cast<ULONG*>(data + 16);
        } else if (!is_64bit && data_len >= 16u) {
            out.pid = *reinterpret_cast<ULONG*>(data + 8);
        }
        ExtractNtKernelString(data, data_len, is_64bit ? 24 : 16, out.image_path);
        break;

    case 11: // Terminate (MOF format)
        out.type = BehaviorEventType::ProcessStop;
        // Terminate 的 UserData 布局与 Stop 相同
        if (data_len >= (is_64bit ? 24u : 16u)) {
            out.pid = *reinterpret_cast<ULONG*>(data + (is_64bit ? 8 : 4));
        }
        break;

    case 17: // FileIo Create/Open (EVENT_RECORD format)
        out.type = BehaviorEventType::FileCreate;
        break;
    case 36: // FileIo Delete（与 ParseKernelSessionEvent/MOF 回退一致，归类为删除）
        out.type = BehaviorEventType::FileDelete;
        break;

    // Registry 事件（EVENT_RECORD format，来自 NT Kernel Logger）
    case 33: // RegistryOpenKey → 作为 SetKey 处理
    case 35: // RegistryQueryValueKey → 作为 SetKey 处理
    case 38: // RegistrySetValueKey
    case 39: // RegistryEnumValueKey → 作为 SetKey 处理
        out.type = BehaviorEventType::RegistrySetKey;
        ExtractNtKernelString(data, data_len, 0, out.key_path);
        if (out.key_path.empty()) {
            // 尝试 offset 2（可能的前缀长度字段）
            ExtractNtKernelString(data, data_len, 2, out.key_path);
        }
        break;
    case 34: // RegistryDeleteKey
    case 37: // RegistryDeleteValueKey
        out.type = BehaviorEventType::RegistryDeleteKey;
        ExtractNtKernelString(data, data_len, 0, out.key_path);
        if (out.key_path.empty()) {
            ExtractNtKernelString(data, data_len, 2, out.key_path);
        }
        break;

    default:
        out.type = BehaviorEventType::Unknown;
        break;
    }
}

void EventRecordParser::ExtractNtKernelString(BYTE* data, ULONG data_len,
                                               ULONG offset, std::string& out)
{
    if (offset >= data_len || offset + sizeof(WCHAR) > data_len) return;

    WCHAR* ws = reinterpret_cast<WCHAR*>(data + offset);
    ULONG max_chars = (data_len - offset) / sizeof(WCHAR);

    ULONG len = 0;
    while (len < max_chars && ws[len] != L'\0') ++len;

    if (len > 0) {
        out = WideToUtf8(std::wstring(ws, len));
    }
}

// =============================================================================
// Manifest-based provider 事件解析（TDH schema）
// =============================================================================

void EventRecordParser::ParseFileEvent(PEVENT_RECORD record, BehaviorEvent& out)
{
    auto* info = GetEventInfo(record);
    if (!info) { out.type = BehaviorEventType::Unknown; return; }

    int idx = FindPropertyIndex(info, L"FileName");
    if (idx < 0) idx = FindPropertyIndex(info, L"FilePath");
    if (idx < 0) idx = FindPropertyIndex(info, L"OpenPath");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.file_path = WideToUtf8(ws);
    }

    idx = FindPropertyIndex(info, L"ProcessId");
    if (idx >= 0) out.pid = ExtractUInt32(record, info, idx);

    // 检测 NtStatus 属性，STATUS_ACCESS_DENIED → AccessDenied 事件
    UINT32 nt_status = 0;
    int status_idx = FindPropertyIndex(info, L"NtStatus");
    if (status_idx < 0) status_idx = FindPropertyIndex(info, L"Status");
    if (status_idx >= 0) nt_status = ExtractUInt32(record, info, status_idx);

    if (nt_status == 0xC0000022) {
        out.type = BehaviorEventType::AccessDenied;
        out.operation = "file_access";
    } else {
        USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
        switch (opcode) {
        case 36:  out.type = BehaviorEventType::FileDelete; break;
        default:  out.type = BehaviorEventType::FileCreate; break;
        }
    }
}

void EventRecordParser::ParseRegistryEvent(PEVENT_RECORD record, BehaviorEvent& out)
{
    auto* info = GetEventInfo(record);
    if (!info) { out.type = BehaviorEventType::Unknown; return; }

    int idx = FindPropertyIndex(info, L"KeyName");
    if (idx < 0) idx = FindPropertyIndex(info, L"KeyPath");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.key_path = WideToUtf8(ws);
    }

    // TDH schema 缺少 KeyName/KeyPath 时，回退到从 UserData 以 MOF 格式提取
    // NT Kernel Logger 的 Registry 事件（EVENT_RECORD 格式）TDH schema 可能
    // 不包含字符串属性，但 UserData 开头是 null-terminated wide string
    if (out.key_path.empty() && record->UserData && record->UserDataLength > 0) {
        BYTE* data = reinterpret_cast<BYTE*>(record->UserData);
        ULONG data_len = record->UserDataLength;
        ExtractNtKernelString(data, data_len, 0, out.key_path);
        if (out.key_path.empty()) {
            ExtractNtKernelString(data, data_len, 2, out.key_path);
        }
    }

    idx = FindPropertyIndex(info, L"ValueName");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.value_name = WideToUtf8(ws);
    }

    idx = FindPropertyIndex(info, L"ProcessId");
    if (idx >= 0) out.pid = ExtractUInt32(record, info, idx);

    // 检测 NtStatus 属性，STATUS_ACCESS_DENIED → AccessDenied 事件
    UINT32 nt_status = 0;
    int status_idx = FindPropertyIndex(info, L"NtStatus");
    if (status_idx < 0) status_idx = FindPropertyIndex(info, L"Status");
    if (status_idx >= 0) nt_status = ExtractUInt32(record, info, status_idx);

    if (nt_status == 0xC0000022) {
        out.type = BehaviorEventType::AccessDenied;
        out.operation = "registry_access";
    } else {
        USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
        switch (opcode) {
        case 32:  out.type = BehaviorEventType::RegistryCreateKey; break;
        case 34:  out.type = BehaviorEventType::RegistryDeleteKey; break;
        case 37:  out.type = BehaviorEventType::RegistryDeleteKey; break;
        default:  out.type = BehaviorEventType::RegistrySetKey; break;
        }
    }
}

void EventRecordParser::ParseNetworkEvent(PEVENT_RECORD record, BehaviorEvent& out)
{
    auto* info = GetEventInfo(record);
    if (!info) { out.type = BehaviorEventType::Unknown; return; }

    USHORT event_id = record->EventHeader.EventDescriptor.Id;
    switch (event_id) {
    case 11: case 12: out.type = BehaviorEventType::UdpSend; break;
    default:          out.type = BehaviorEventType::TcpConnect; break;
    }

    int idx = FindPropertyIndex(info, L"PID");
    if (idx < 0) idx = FindPropertyIndex(info, L"ProcessId");
    if (idx >= 0) out.pid = ExtractUInt32(record, info, idx);

    idx = FindPropertyIndex(info, L"RemoteAddress");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.remote_addr = WideToUtf8(ws);
    }

    idx = FindPropertyIndex(info, L"RemotePort");
    if (idx >= 0) out.remote_port = static_cast<uint16_t>(ExtractUInt32(record, info, idx));

    idx = FindPropertyIndex(info, L"LocalAddress");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.local_addr = WideToUtf8(ws);
    }

    idx = FindPropertyIndex(info, L"LocalPort");
    if (idx >= 0) out.local_port = static_cast<uint16_t>(ExtractUInt32(record, info, idx));
}

void EventRecordParser::ParseKernelProcessEvent(PEVENT_RECORD record, BehaviorEvent& out)
{
    auto* info = GetEventInfo(record);
    if (!info) { out.type = BehaviorEventType::Unknown; return; }

    USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
    switch (opcode) {
    case 1:  out.type = BehaviorEventType::ProcessStart; break;
    case 2:  out.type = BehaviorEventType::ProcessStop; break;
    default: out.type = BehaviorEventType::Unknown; break;
    }

    int idx = FindPropertyIndex(info, L"ProcessID");
    if (idx < 0) idx = FindPropertyIndex(info, L"ProcessId");
    if (idx >= 0) out.pid = ExtractUInt32(record, info, idx);

    idx = FindPropertyIndex(info, L"ParentProcessID");
    if (idx < 0) idx = FindPropertyIndex(info, L"ParentProcessId");
    if (idx >= 0) out.parent_pid = ExtractUInt32(record, info, idx);

    idx = FindPropertyIndex(info, L"ImageName");
    if (idx < 0) idx = FindPropertyIndex(info, L"ImagePath");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.image_path = WideToUtf8(ws);
    }

    idx = FindPropertyIndex(info, L"CommandLine");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.command_line = WideToUtf8(ws);
    }
}

void EventRecordParser::ParseNtProcessMof(PEVENT_RECORD record, BehaviorEvent& out)
{
    if (!record->UserData || record->UserDataLength == 0) return;

    USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
    bool is_64bit = (record->EventHeader.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;
    BYTE* data = reinterpret_cast<BYTE*>(record->UserData);
    ULONG data_len = record->UserDataLength;

    switch (opcode) {
    case 1:  // Start
    case 5:  // DCStart (rundown of existing processes)
    case 32: // RundownStart
        out.type = BehaviorEventType::ProcessStart; break;
    case 2:  // Stop
    case 6:  // DCStop
        out.type = BehaviorEventType::ProcessStop; break;
    default: out.type = BehaviorEventType::Unknown; return;
    }

    ULONG fixed_size = is_64bit ? 24u : 16u;
    if (data_len >= fixed_size) {
        if (is_64bit) {
            out.pid = *reinterpret_cast<ULONG*>(data + 8);
            out.parent_pid = *reinterpret_cast<ULONG*>(data + 12);
        } else {
            out.pid = *reinterpret_cast<ULONG*>(data + 4);
            out.parent_pid = *reinterpret_cast<ULONG*>(data + 8);
        }
    }
    ExtractNtKernelString(data, data_len, fixed_size, out.image_path);
}

void EventRecordParser::ParseNtThreadMof(PEVENT_RECORD record, BehaviorEvent& out)
{
    if (!record->UserData || record->UserDataLength == 0) return;

    USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
    bool is_64bit = (record->EventHeader.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;
    BYTE* data = reinterpret_cast<BYTE*>(record->UserData);
    ULONG data_len = record->UserDataLength;

    switch (opcode) {
    case 1:  // Start
    case 3:  // DCStart (rundown of existing threads)
        out.type = BehaviorEventType::ThreadStart; break;
    case 2:  // Stop
    case 4:  // DCStop
        out.type = BehaviorEventType::ThreadStop; break;
    default: out.type = BehaviorEventType::Unknown; return;
    }

    ULONG fixed_size = is_64bit ? 16u : 12u;
    if (data_len >= fixed_size) {
        if (is_64bit) {
            out.pid = *reinterpret_cast<ULONG*>(data + 8);
            out.tid = *reinterpret_cast<ULONG*>(data + 12);
        } else {
            out.pid = *reinterpret_cast<ULONG*>(data + 4);
            out.tid = *reinterpret_cast<ULONG*>(data + 8);
        }
    }
}

void EventRecordParser::ParseNtImageLoadMof(PEVENT_RECORD record, BehaviorEvent& out)
{
    if (!record->UserData || record->UserDataLength == 0) return;

    out.type = BehaviorEventType::ImageLoad;
    bool is_64bit = (record->EventHeader.Flags & EVENT_HEADER_FLAG_64_BIT_HEADER) != 0;
    BYTE* data = reinterpret_cast<BYTE*>(record->UserData);
    ULONG data_len = record->UserDataLength;

    ULONG fixed_size = is_64bit ? 24u : 16u;
    if (data_len >= fixed_size) {
        if (is_64bit) {
            out.pid = *reinterpret_cast<ULONG*>(data + 16);
        } else {
            out.pid = *reinterpret_cast<ULONG*>(data + 8);
        }
    }
    ExtractNtKernelString(data, data_len, fixed_size, out.image_path);
}

void EventRecordParser::ParseNtFileIoMof(PEVENT_RECORD record, BehaviorEvent& out)
{
    if (!record->UserData || record->UserDataLength == 0) return;

    USHORT opcode = record->EventHeader.EventDescriptor.Opcode;
    switch (opcode) {
    case 36:
        out.type = BehaviorEventType::FileDelete; break;
    default:
        out.type = BehaviorEventType::FileCreate; break;
    }

    auto* info = GetEventInfo(record);
    if (!info) return;

    int idx = FindPropertyIndex(info, L"FileName");
    if (idx < 0) idx = FindPropertyIndex(info, L"FilePath");
    if (idx < 0) idx = FindPropertyIndex(info, L"OpenPath");
    if (idx >= 0) {
        auto ws = ExtractWideString(record, info, idx);
        if (!ws.empty()) out.file_path = WideToUtf8(ws);
    }

    idx = FindPropertyIndex(info, L"ProcessId");
    if (idx >= 0) out.pid = ExtractUInt32(record, info, idx);
}

// =============================================================================
// TDH Schema 缓存（含失败负缓存）
//
// 无 schema 的事件（NT Kernel Logger 大量 MOF 事件）每次事件都做两次
// TdhGetEventInformation 是显著开销 → 解析失败结果也缓存（空 buffer 表示
// 已知失败），避免重复查询。
// 注意：返回的 info 指针指向缓存内 buffer，缓存不可在监控运行中清空
//（无 ClearCache 接口，防止调用方误触发 use-after-free）。
// =============================================================================

TRACE_EVENT_INFO* EventRecordParser::GetEventInfo(PEVENT_RECORD record)
{
    SchemaKey key;
    key.provider_id = record->EventHeader.ProviderId;
    key.event_id = record->EventHeader.EventDescriptor.Id;
    key.opcode = record->EventHeader.EventDescriptor.Opcode;
    key.version = record->EventHeader.EventDescriptor.Version;

    {
        std::lock_guard<std::mutex> lk(cache_mutex_);
        auto it = schema_cache_.find(key);
        if (it != schema_cache_.end()) {
            // 负缓存（空 buffer）= 已知解析失败
            return it->second.buffer.empty() ? nullptr : it->second.info();
        }
    }

    ULONG buf_size = 0;
    ULONG status = TdhGetEventInformation(record, 0, nullptr, nullptr, &buf_size);
    SchemaValue val;
    if (status == ERROR_INSUFFICIENT_BUFFER) {
        val.buffer.resize(buf_size);
        auto* info = reinterpret_cast<PTRACE_EVENT_INFO>(val.buffer.data());

        status = TdhGetEventInformation(record, 0, nullptr, info, &buf_size);
        if (status != ERROR_SUCCESS) {
            // 第二次调用失败：置为空 buffer，走负缓存
            val.buffer.clear();
        }
    }
    // 成功与失败结果都缓存（失败 = 负缓存）
    std::lock_guard<std::mutex> lk(cache_mutex_);
    auto [it, inserted] = schema_cache_.emplace(key, std::move(val));
    return it->second.buffer.empty() ? nullptr : it->second.info();
}

// =============================================================================
// 属性提取辅助
// =============================================================================

int EventRecordParser::FindPropertyIndex(PTRACE_EVENT_INFO info, const wchar_t* name)
{
    if (!info || !name) return -1;

    ULONG count = info->TopLevelPropertyCount;
    for (ULONG i = 0; i < count; ++i) {
        auto& prop = info->EventPropertyInfoArray[i];
        // NameOffset 是从 TRACE_EVENT_INFO 起始的字节偏移（以 WCHAR 为单位）
        LPCWSTR propName = reinterpret_cast<LPCWSTR>(
            reinterpret_cast<BYTE*>(info) + prop.NameOffset);
        if (wcscmp(propName, name) == 0) {
            return static_cast<int>(i);
        }
    }
    return -1;
}

std::wstring EventRecordParser::ExtractWideString(PEVENT_RECORD record,
                                                   PTRACE_EVENT_INFO info,
                                                   int propIndex)
{
    if (!record || !info || propIndex < 0) return {};

    auto& prop = info->EventPropertyInfoArray[propIndex];
    if (prop.Flags & PropertyStruct) return {};

    // 构建 PROPERTY_DATA_DESCRIPTOR
    PROPERTY_DATA_DESCRIPTOR desc;
    desc.PropertyName = reinterpret_cast<ULONGLONG>(
        reinterpret_cast<BYTE*>(info) + prop.NameOffset);
    desc.ArrayIndex = 0;
    desc.Reserved = 0;

    ULONG prop_size = 0;
    ULONG status = TdhGetPropertySize(record, 0, nullptr, 1, &desc, &prop_size);
    if (status != ERROR_SUCCESS || prop_size == 0) return {};

    std::vector<BYTE> buf(prop_size);
    status = TdhGetProperty(record, 0, nullptr, 1, &desc, prop_size, buf.data());
    if (status != ERROR_SUCCESS) return {};

    USHORT inType = prop.nonStructType.InType;
    USHORT outType = prop.nonStructType.OutType;

    if (inType == TDH_INTYPE_UNICODESTRING) {
        auto* ws = reinterpret_cast<const WCHAR*>(buf.data());
        ULONG max_chars = prop_size / sizeof(WCHAR);
        ULONG len = 0;
        while (len < max_chars && ws[len] != L'\0') ++len;
        return std::wstring(ws, len);
    }

    if (inType == TDH_INTYPE_ANSISTRING) {
        auto* s = reinterpret_cast<const char*>(buf.data());
        ULONG len = 0;
        while (len < prop_size && s[len] != '\0') ++len;
        int wlen = MultiByteToWideChar(CP_ACP, 0, s, static_cast<int>(len), nullptr, 0);
        if (wlen <= 0) return {};
        std::wstring ws(wlen, L'\0');
        MultiByteToWideChar(CP_ACP, 0, s, static_cast<int>(len), &ws[0], wlen);
        return ws;
    }

    // IP 地址类型
    if (outType == TDH_OUTTYPE_IPV4 && prop_size >= 4) {
        auto* addr = reinterpret_cast<const BYTE*>(buf.data());
        wchar_t wbuf[64];
        swprintf_s(wbuf, L"%u.%u.%u.%u", addr[0], addr[1], addr[2], addr[3]);
        return std::wstring(wbuf);
    }

    if (outType == TDH_OUTTYPE_IPV6 && prop_size >= 16) {
        // 标准 IPv6 文本格式（含 :: 压缩），与 inet_ntop 输出一致
        wchar_t wbuf[INET6_ADDRSTRLEN];
        if (InetNtopW(AF_INET6, buf.data(), wbuf, INET6_ADDRSTRLEN)) {
            return std::wstring(wbuf);
        }
        return {};
    }

    // SocketAddress 类型（SOCKADDR_STORAGE）
    if (outType == TDH_OUTTYPE_SOCKETADDRESS && prop_size >= 4) {
        auto* addr = reinterpret_cast<const BYTE*>(buf.data());
        USHORT family = *reinterpret_cast<const USHORT*>(addr);
        if (family == 2 && prop_size >= 8) {  // AF_INET
            wchar_t wbuf[64];
            swprintf_s(wbuf, L"%u.%u.%u.%u",
                       addr[4], addr[5], addr[6], addr[7]);
            return std::wstring(wbuf);
        }
    }

    return {};
}

UINT64 EventRecordParser::ExtractUInt64(PEVENT_RECORD record,
                                         PTRACE_EVENT_INFO info,
                                         int propIndex)
{
    if (!record || !info || propIndex < 0) return 0;

    auto& prop = info->EventPropertyInfoArray[propIndex];
    if (prop.Flags & PropertyStruct) return 0;

    PROPERTY_DATA_DESCRIPTOR desc;
    desc.PropertyName = reinterpret_cast<ULONGLONG>(
        reinterpret_cast<BYTE*>(info) + prop.NameOffset);
    desc.ArrayIndex = 0;
    desc.Reserved = 0;

    ULONG prop_size = 0;
    ULONG status = TdhGetPropertySize(record, 0, nullptr, 1, &desc, &prop_size);
    if (status != ERROR_SUCCESS || prop_size == 0) return 0;

    std::vector<BYTE> buf(prop_size);
    status = TdhGetProperty(record, 0, nullptr, 1, &desc, prop_size, buf.data());
    if (status != ERROR_SUCCESS) return 0;

    if (prop_size >= 8) return *reinterpret_cast<UINT64*>(buf.data());
    if (prop_size >= 4) return *reinterpret_cast<UINT32*>(buf.data());
    if (prop_size >= 2) return *reinterpret_cast<UINT16*>(buf.data());
    return *reinterpret_cast<UINT8*>(buf.data());
}

UINT32 EventRecordParser::ExtractUInt32(PEVENT_RECORD record,
                                         PTRACE_EVENT_INFO info,
                                         int propIndex)
{
    return static_cast<UINT32>(ExtractUInt64(record, info, propIndex));
}

// =============================================================================
// 工具函数
// =============================================================================

std::string EventRecordParser::WideToUtf8(const std::wstring& ws)
{
    if (ws.empty()) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), static_cast<int>(ws.size()),
                                  nullptr, 0, nullptr, nullptr);
    if (len <= 0) return {};
    std::string s(len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), static_cast<int>(ws.size()),
                        &s[0], len, nullptr, nullptr);
    return s;
}

} // namespace winsandbox
