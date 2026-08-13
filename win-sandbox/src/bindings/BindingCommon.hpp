// =============================================================================
// BindingCommon - pybind11 绑定层共享辅助
//
// 提供 py::object ↔ nlohmann::json 转换、Result 错误转换、
// 回调 payload → py::dict 转换等共享工具。
// =============================================================================
#pragma once

#include "core/entities/Callbacks.hpp"
#include "core/entities/Result.hpp"
#include "core/entities/JobAccountingInfo.hpp"
#include "core/entities/SandboxedProcess.hpp"
#include "core/ports/ILogger.hpp"

#include <pybind11/pybind11.h>
#include <nlohmann/json.hpp>

#include <memory>
#include <stdexcept>
#include <string>

namespace py = pybind11;
namespace winsandbox::bindings {

// -----------------------------------------------------------------------------
// py::object → nlohmann::json（递归转换）
// 用于把 Python dict 传给 ParseStartProcessPayload（复用现有 schema 校验）
// -----------------------------------------------------------------------------
inline nlohmann::json py_to_json(const py::handle& obj) {
    if (obj.is_none()) {
        return nullptr;
    } else if (py::isinstance<py::bool_>(obj)) {
        return obj.cast<bool>();
    } else if (py::isinstance<py::int_>(obj)) {
        // py::int_ 可能溢出 int64，用 PyLong_AsLongLongAndOverflow 检测
        return obj.cast<int64_t>();
    } else if (py::isinstance<py::float_>(obj)) {
        return obj.cast<double>();
    } else if (py::isinstance<py::str>(obj)) {
        return obj.cast<std::string>();
    } else if (py::isinstance<py::list>(obj)) {
        nlohmann::json j = nlohmann::json::array();
        for (auto item : obj) {
            j.push_back(py_to_json(item));
        }
        return j;
    } else if (py::isinstance<py::dict>(obj)) {
        nlohmann::json j = nlohmann::json::object();
        for (auto item : py::reinterpret_borrow<py::dict>(obj)) {
            j[item.first.cast<std::string>()] = py_to_json(item.second);
        }
        return j;
    }
    throw py::type_error("Cannot convert Python object to JSON: unsupported type");
}

// -----------------------------------------------------------------------------
// nlohmann::json → py::object（递归转换）
// 用于把 C++ 查询结果转回 Python dict
// -----------------------------------------------------------------------------
inline py::object json_to_py(const nlohmann::json& j) {
    switch (j.type()) {
        case nlohmann::json::value_t::null:
            return py::none();
        case nlohmann::json::value_t::boolean:
            return py::bool_(j.get<bool>());
        case nlohmann::json::value_t::number_integer:
            return py::int_(j.get<int64_t>());
        case nlohmann::json::value_t::number_unsigned:
            return py::int_(j.get<uint64_t>());
        case nlohmann::json::value_t::number_float:
            return py::float_(j.get<double>());
        case nlohmann::json::value_t::string:
            return py::str(j.get<std::string>());
        case nlohmann::json::value_t::array: {
            py::list lst;
            for (const auto& item : j) {
                lst.append(json_to_py(item));
            }
            return lst;
        }
        case nlohmann::json::value_t::object: {
            py::dict d;
            for (auto it = j.begin(); it != j.end(); ++it) {
                d[py::str(it.key())] = json_to_py(it.value());
            }
            return d;
        }
        case nlohmann::json::value_t::discarded:
        default:
            return py::none();
    }
}

// -----------------------------------------------------------------------------
// Result 错误转换：Result<T> 失败时抛 py::runtime_error
// 用法：auto val = unwrap_result(r);  // 失败时抛异常
// -----------------------------------------------------------------------------
template <typename T>
inline T unwrap_result(const Result<T>& r) {
    if (!r) {
        throw std::runtime_error(std::string("[") +
                                 std::to_string(static_cast<int>(r.Code())) +
                                 "] " + r.Message());
    }
    return r.Value();
}

inline void unwrap_result(const Result<void>& r) {
    if (!r) {
        throw std::runtime_error(std::string("[") +
                                std::to_string(static_cast<int>(r.Code())) +
                                 "] " + r.Message());
    }
}

// -----------------------------------------------------------------------------
// 日志级别字符串 → LogLevel
// -----------------------------------------------------------------------------
inline LogLevel parse_log_level(const std::string& s) {
    if (s == "trace") return LogLevel::Trace;
    if (s == "debug") return LogLevel::Debug;
    if (s == "info")  return LogLevel::Info;
    if (s == "warn")  return LogLevel::Warn;
    if (s == "error") return LogLevel::Error;
    throw std::invalid_argument("invalid log_level: " + s +
                                " (expected: trace/debug/info/warn/error)");
}

// -----------------------------------------------------------------------------
// 回调 payload → py::dict
// -----------------------------------------------------------------------------
inline py::dict resource_limit_info_to_dict(const ResourceLimitInfo& info) {
    py::dict d;
    d["type"] = info.type;
    d["pid"] = info.pid;
    d["timestamp_ms"] = info.timestamp_ms;
    return d;
}

inline py::dict job_process_started_info_to_dict(const JobProcessStartedInfo& info) {
    py::dict d;
    d["pid"] = info.pid;
    d["process_name"] = info.process_name;
    d["process_path"] = info.process_path;
    if (info.parent_pid.has_value()) {
        d["parent_pid"] = *info.parent_pid;
    }
    d["timestamp_ms"] = info.timestamp_ms;
    return d;
}

inline py::dict job_process_exited_info_to_dict(const JobProcessExitedInfo& info) {
    py::dict d;
    d["pid"] = info.pid;
    d["exit_kind"] = info.exit_kind;
    if (info.exit_code.has_value()) {
        d["exit_code"] = *info.exit_code;
    }
    d["timestamp_ms"] = info.timestamp_ms;
    return d;
}

// ETW 行为事件 → py::dict
inline py::dict behavior_event_info_to_dict(const BehaviorEventInfo& info) {
    py::dict d;
    d["event_type"] = info.event_type;
    d["pid"] = info.pid;
    d["path"] = info.path;
    d["operation"] = info.operation;
    d["status"] = info.status;
    d["timestamp_ms"] = info.timestamp_ms;
    d["source"] = info.source;
    return d;
}

// AccessDenied 专项事件 → py::dict
inline py::dict access_denied_info_to_dict(const AccessDeniedInfo& info) {
    py::dict d;
    d["pid"] = info.pid;
    d["path"] = info.path;
    d["operation"] = info.operation;
    d["source"] = info.source;
    d["timestamp_ms"] = info.timestamp_ms;
    return d;
}

// -----------------------------------------------------------------------------
// JobAccountingInfo → py::dict
// -----------------------------------------------------------------------------
inline py::dict accounting_to_dict(const JobAccountingInfo& info) {
    py::dict d;
    d["sample_time_ms"] = info.sample_time_ms;
    py::dict cpu;
    cpu["total_user_ms"] = info.total_user_time_100ns / 10000;
    cpu["total_kernel_ms"] = info.total_kernel_time_100ns / 10000;
    cpu["period_user_ms"] = info.this_period_user_time_100ns / 10000;
    cpu["period_kernel_ms"] = info.this_period_kernel_time_100ns / 10000;
    d["cpu"] = cpu;
    py::dict io;
    io["read_ops"] = info.read_operation_count;
    io["write_ops"] = info.write_operation_count;
    io["other_ops"] = info.other_operation_count;
    io["read_bytes"] = info.read_transfer_count;
    io["write_bytes"] = info.write_transfer_count;
    io["other_bytes"] = info.other_transfer_count;
    d["io"] = io;
    py::dict procs;
    procs["total"] = info.total_processes;
    procs["active"] = info.active_processes;
    procs["terminated"] = info.terminated_processes;
    d["processes"] = procs;
    py::dict mem;
    mem["peak_process_bytes"] = info.peak_process_memory;
    mem["peak_job_bytes"] = info.peak_job_memory;
    d["memory"] = mem;
    d["page_faults"] = info.total_page_faults;
    return d;
}

// -----------------------------------------------------------------------------
// SandboxedProcess → py::dict
// -----------------------------------------------------------------------------
inline py::dict process_to_dict(const SandboxedProcess& p) {
    py::dict d;
    d["process_id"] = p.process_id;
    d["pid"] = p.pid;
    d["command_line"] = p.command_line;
    d["working_dir"] = p.working_dir;
    // request_id：int | None（"" → None，数字字符串 → int）
    if (p.request_id.empty()) {
        d["request_id"] = py::none();
    } else {
        try {
            d["request_id"] = py::int_(std::stoull(p.request_id));
        } catch (const std::exception&) {
            d["request_id"] = py::none();
        }
    }
    d["start_time_ms"] = p.start_time_ms;
    d["exit_time_ms"] = p.exit_time_ms;
    d["exit_code"] = p.exit_code;
    // state → string
    switch (p.state) {
        case ProcessState::Pending:    d["state"] = "pending"; break;
        case ProcessState::Running:    d["state"] = "running"; break;
        case ProcessState::Exited:     d["state"] = "exited"; break;
        case ProcessState::Terminated: d["state"] = "terminated"; break;
    }
    d["exit_reason"] = ExitReasonToString(p.exit_reason);
    return d;
}

} // namespace winsandbox::bindings
