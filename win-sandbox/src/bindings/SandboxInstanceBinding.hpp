// =============================================================================
// SandboxInstanceBinding 声明 - pybind11 SandboxInstance 包装
// =============================================================================
#pragma once

#include <pybind11/pybind11.h>

namespace py = pybind11;
namespace winsandbox::bindings {

// 注册 PySandboxInstance 类到模块
void RegisterSandboxInstance(py::module_& m);

} // namespace winsandbox::bindings
