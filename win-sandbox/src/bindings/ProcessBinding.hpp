// =============================================================================
// ProcessBinding 声明 - pybind11 Process 包装
//
// PyProcess 包装 NativeSandboxedProcess + NativeExecuteResult，暴露给 Python。
// MakePyProcess 是工厂函数，供 SandboxInstanceBinding::start_process 调用。
// =============================================================================
#pragma once

#include "core/usecases/NativeSandboxedProcess.hpp"

#include <pybind11/pybind11.h>

namespace py = pybind11;
namespace winsandbox::bindings {

// 注册 PyProcess 类到模块
void RegisterProcess(py::module_& m);

// 工厂函数：创建 PyProcess 实例（供 SandboxInstanceBinding::start_process 调用）
// usecase: NativeSandboxedProcess 共享所有权（PyProcess 持有）
// exec_result: Execute 返回的句柄
// process_id: 沙箱内部进程 ID
py::object MakePyProcess(std::shared_ptr<NativeSandboxedProcess> usecase,
                         NativeExecuteResult exec_result,
                         uint32_t process_id);

} // namespace winsandbox::bindings
