// =============================================================================
// ConfigBinding - 配置转换辅助（header-only，pybind11 绑定层）
//
// 提供 Python dict → StartProcessRequest 转换，复用 ParseStartProcessPayload。
// =============================================================================
#pragma once

#include "bindings/BindingCommon.hpp"
#include "core/entities/StartProcessRequest.hpp"
#include "core/entities/IsolationPolicy.hpp"
#include "core/entities/ResourceQuota.hpp"
#include "adapters/StartProcessPayloadParser.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <string>

namespace py = pybind11;
namespace winsandbox::bindings {

// -----------------------------------------------------------------------------
// BuildStartProcessRequest - 从 Python 参数构造 StartProcessRequest
//
// 复用 ParseStartProcessPayload：把 Python 参数组装为 JSON payload，
// 再调 ParseStartProcessPayload（复用 schema 校验 + 默认值兜底）。
// -----------------------------------------------------------------------------
inline StartProcessRequest BuildStartProcessRequest(
    const std::string& command_line,
    const py::object& working_dir,
    const py::object& env_vars,
    bool inherit_env,
    const py::object& quota,
    const py::object& isolation_policy,
    bool interactive,
    size_t stream_buffer_size,
    const py::object& stdin_data,
    const py::object& hpcon,
    const ResourceQuota& default_quota,
    const IsolationPolicy& default_isolation_policy,
    const std::string& request_id = "") {

    nlohmann::json payload;
    payload["command_line"] = command_line;

    if (!working_dir.is_none()) {
        payload["working_dir"] = working_dir.cast<std::string>();
    }

    if (!env_vars.is_none()) {
        payload["env_vars"] = py_to_json(env_vars);
    }
    payload["inherit_env"] = inherit_env;

    if (!quota.is_none()) {
        payload["quota"] = py_to_json(quota);
    }

    if (!isolation_policy.is_none()) {
        payload["isolation_policy"] = py_to_json(isolation_policy);
    }

    payload["interactive"] = interactive;
    if (stream_buffer_size > 0) {
        payload["stream_buffer_size"] = stream_buffer_size;
    }

    if (!stdin_data.is_none()) {
        // 占位（实际 stdin_data 在 parser 后直接覆盖）
        payload["stdin_data"] = "";
    }

    // 直接传 json payload
    auto r = ParseStartProcessPayload(payload, default_quota, default_isolation_policy,
                                      request_id);
    if (!r) {
        throw py::value_error(std::string("invalid start_process arguments: [") +
                              std::to_string(static_cast<int>(r.Code())) + "] " + r.Message());
    }

    auto req = r.Value();

    // 直接覆盖 stdin_data（保留原始字节）
    if (!stdin_data.is_none()) {
        std::string_view data = stdin_data.cast<std::string_view>();
        req.stdin_data.assign(data.data(), data.size());
    }

    // ConPTY 模式（hpcon 非空）：外部创建的伪控制台句柄（HPCON 值）
    //   - 传 None → 匿名管道路径
    //   - 传 int（HPCON 句柄值）→ ConPTY 启动路径：子进程 stdio 由 ConPTY 提供
    if (!hpcon.is_none()) {
        int64_t h = hpcon.cast<int64_t>();
        if (h <= 0) {
            throw py::value_error("hpcon must be a positive HPCON handle value");
        }
        req.hpcon = reinterpret_cast<void*>(h);
    }

    return req;
}

// -----------------------------------------------------------------------------
// RegisterConfig - 注册配置相关枚举/常量到模块
// 枚举在 Python 侧用字符串表示，暂无需注册 C++ enum
// -----------------------------------------------------------------------------
inline void RegisterConfig(py::module_& /*m*/) {
    // 后续 Phase 如需暴露枚举可在此扩展
}

} // namespace winsandbox::bindings
