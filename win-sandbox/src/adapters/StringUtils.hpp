// =============================================================================
// StringUtils - 纯字符串工具函数（adapters 层）
//
// 独立工具函数，供 pybind11 绑定层和其他模块复用。
//
// 职责：
//   - 检测 stderr chunk 是否含 AccessDenied 关键字（中英文，多编码）
//
// 依赖：无（纯标准库，无 Win32 / 无第三方）
// =============================================================================
#pragma once

#include <string_view>
#include <string>
#include <cctype>

namespace winsandbox {

// -----------------------------------------------------------------------------
// ContainsAccessDeniedKeywordImpl
//
// 检测 data 是否含 "拒绝访问"（中文）或 "Access is denied"（英文）关键字。
//
// 关键字（任一命中即返回 true）：
//   - "拒绝访问"  UTF-8 编码（chcp 65001 / 终端主活载 UTF-8 输出）
//   - "拒绝访问"  GBK 编码（cmd.exe 中文系统默认 CP936）
//   - "Access is denied"  英文，大小写不敏感
//
// 纯函数，无状态，线程安全。
// -----------------------------------------------------------------------------
inline bool ContainsAccessDeniedKeywordImpl(std::string_view data) {
    // 中文关键字 "拒绝访问"，同时检测 UTF-8 和 GBK 编码
    //
    // UTF-8 编码：
    //   "拒" = 0xE6 0x8B 0x92
    //   "绝" = 0xE7 0xBB 0x9D
    //   "访" = 0xE8 0xAE 0xBF
    //   "问" = 0xE9 0x97 0xAE
    //
    // GBK 编码（cmd.exe 中文系统默认 CP936）：
    //   "拒" = 0xBE 0xDC
    //   "绝" = 0xBE 0xF8
    //   "访" = 0xB7 0xC3
    //   "问" = 0xCE 0xCA
    static constexpr std::string_view kZhUtf8 = "\xE6\x8B\x92\xE7\xBB\x9D\xE8\xAE\xBF\xE9\x97\xAE";
    static constexpr std::string_view kZhGbk  = "\xBE\xDC\xBE\xF8\xB7\xC3\xCE\xCA";
    if (data.find(kZhUtf8) != std::string_view::npos) {
        return true;
    }
    if (data.find(kZhGbk) != std::string_view::npos) {
        return true;
    }

    // 英文关键字：大小写不敏感匹配 "Access is denied"
    // strlen("access is denied") == 16
    if (data.size() < 16) {
        return false;
    }
    std::string lower;
    lower.reserve(data.size());
    for (char c : data) {
        lower.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    return lower.find("access is denied") != std::string::npos;
}

} // namespace winsandbox
