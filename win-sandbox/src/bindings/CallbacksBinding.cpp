// =============================================================================
// CallbacksBinding.cpp - 回调相关 pybind11 绑定实现
//
// contains_access_denied_keyword 工具函数
//   Python 端读到 stderr 字节后调用此函数判断是否含 AccessDenied 关键字
// =============================================================================
#include "bindings/CallbacksBinding.hpp"
#include "adapters/StringUtils.hpp"

#include <pybind11/pybind11.h>

namespace py = pybind11;
namespace winsandbox::bindings {

void RegisterCallbacks(py::module_& m) {
    // contains_access_denied_keyword(data: bytes) -> bool
    // 检测 data 是否含 "拒绝访问"（UTF-8/GBK）或 "Access is denied"（大小写不敏感）
    m.def("contains_access_denied_keyword",
          [](py::bytes data) -> bool {
              std::string s = data.cast<std::string>();
              std::string_view sv(s.data(), s.size());
              return ContainsAccessDeniedKeywordImpl(sv);
          },
          py::arg("data"),
          "检测字节序列是否含 AccessDenied 关键字（中英文，多编码）");
}

} // namespace winsandbox::bindings
