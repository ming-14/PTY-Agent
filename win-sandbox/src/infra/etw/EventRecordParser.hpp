// =============================================================================
// EventRecordParser - ETW EVENT_RECORD 解析器（infra 层）
//
// 使用 TdhGetEventInformation 获取事件 schema，然后提取属性值。
// 解析结果填充到 BehaviorEvent 的对应字段。
//
// TDH schema 缓存：同一 Provider+EventDescriptor 的 schema 只查询一次，
// 后续使用缓存，避免 TdhGetEventInformation 的重复开销。
//
// EventRecord 解析
// =============================================================================
#pragma once

#include "core/entities/BehaviorEvent.hpp"

#include <windows.h>
#include <initguid.h>
#include <evntrace.h>
#include <tdh.h>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#pragma comment(lib, "tdh.lib")

namespace winsandbox {

class EventRecordParser {
public:
    EventRecordParser() = default;
    ~EventRecordParser() = default;

    EventRecordParser(const EventRecordParser&) = delete;
    EventRecordParser& operator=(const EventRecordParser&) = delete;

    // 解析单个事件
    // record: ETW 事件记录
    // out: 输出事件
    // is_kernel_session: 事件来自 NT Kernel Logger（provider GUID 因 Windows
    //   版本而异，不能硬编码匹配，需按消费线程身份路由）
    void Parse(PEVENT_RECORD record, BehaviorEvent& out, bool is_kernel_session = false);

private:
    void ParseNtKernelEvent(PEVENT_RECORD record, const GUID& provider, BehaviorEvent& out);
    void ParseKernelSessionEvent(PEVENT_RECORD record, BehaviorEvent& out);
    void ExtractNtKernelString(BYTE* data, ULONG data_len,
                               ULONG offset, std::string& out);

    void ParseFileEvent(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseRegistryEvent(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseNetworkEvent(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseKernelProcessEvent(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseNtProcessMof(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseNtThreadMof(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseNtImageLoadMof(PEVENT_RECORD record, BehaviorEvent& out);
    void ParseNtFileIoMof(PEVENT_RECORD record, BehaviorEvent& out);

    TRACE_EVENT_INFO* GetEventInfo(PEVENT_RECORD record);

    std::wstring ExtractWideString(PEVENT_RECORD record,
                                   PTRACE_EVENT_INFO info,
                                   int propIndex);

    UINT64 ExtractUInt64(PEVENT_RECORD record,
                         PTRACE_EVENT_INFO info,
                         int propIndex);

    UINT32 ExtractUInt32(PEVENT_RECORD record,
                         PTRACE_EVENT_INFO info,
                         int propIndex);

    int FindPropertyIndex(PTRACE_EVENT_INFO info, const wchar_t* name);

    static std::string WideToUtf8(const std::wstring& ws);

    struct SchemaKey {
        GUID provider_id;
        USHORT event_id;
        UCHAR opcode;
        UCHAR version;

        bool operator<(const SchemaKey& o) const {
            if (auto c = memcmp(&provider_id, &o.provider_id, sizeof(GUID)); c != 0) return c < 0;
            if (event_id != o.event_id) return event_id < o.event_id;
            if (opcode != o.opcode) return opcode < o.opcode;
            return version < o.version;
        }
    };

    struct SchemaValue {
        std::vector<BYTE> buffer;
        PTRACE_EVENT_INFO info() const {
            return reinterpret_cast<PTRACE_EVENT_INFO>(const_cast<BYTE*>(buffer.data()));
        }
    };

    std::mutex cache_mutex_;
    std::map<SchemaKey, SchemaValue> schema_cache_;
};

} // namespace winsandbox
