// =============================================================================
// CallbacksBinding - 回调相关绑定（pybind11 绑定层）
//
// 绑定 contains_access_denied_keyword 工具函数
// =============================================================================
#pragma once

#include <pybind11/pybind11.h>

namespace py = pybind11;
namespace winsandbox::bindings {

// 注册回调相关绑定（工具函数等）
void RegisterCallbacks(py::module_& m);

} // namespace winsandbox::bindings
