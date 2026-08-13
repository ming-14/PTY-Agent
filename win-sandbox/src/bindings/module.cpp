// =============================================================================
// win_sandbox_native - pybind11 扩展入口
//
// 注册 SandboxInstance / Process / 配置 / 回调绑定
//
// 形态：pybind11 扩展（.pyd），加载进 Python 解释器进程
//   - C++ 核心代码（同一源文件树）
//   - in-process：HANDLE 值直接共享，无需 DuplicateHandle 跨进程
//   - Python 端用 ctypes 直接 ReadFile/WriteFile 句柄
//
// 注册顺序：
//   1. RegisterConfig（枚举/常量）
//   2. RegisterCallbacks（回调辅助）
//   3. RegisterProcess（PyProcess 类，必须先于 SandboxInstance）
//   4. RegisterSandboxInstance（PySandboxInstance 类，start_process 返回 PyProcess）
// =============================================================================

#include "bindings/BindingCommon.hpp"
#include "bindings/ConfigBinding.hpp"
#include "bindings/CallbacksBinding.hpp"
#include "bindings/ProcessBinding.hpp"
#include "bindings/SandboxInstanceBinding.hpp"

#include <pybind11/pybind11.h>

namespace py = pybind11;

PYBIND11_MODULE(win_sandbox_native, m) {
    m.doc() = "win-sandbox native extension: in-process Job Object + Low IL token sandbox";

    winsandbox::bindings::RegisterConfig(m);
    winsandbox::bindings::RegisterCallbacks(m);
    winsandbox::bindings::RegisterProcess(m);
    winsandbox::bindings::RegisterSandboxInstance(m);
}
