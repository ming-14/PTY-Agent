// =============================================================================
// ILogger - 日志器端口接口（core 层）
//
// 干净架构：core 层依赖此接口，不依赖 spdlog 等具体实现。
// 实现位于 infra/logging/Logger.cpp（SpdlogLogger）。
//
// 调用约定：
//   - 调用方负责格式化消息（可用 std::format）
//   - 实现方负责级别过滤、sink 分发
// =============================================================================
#pragma once

#include <string>
#include <string_view>

namespace winsandbox {

// 日志级别（与 spdlog 级别一一对应）
enum class LogLevel {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
};

// 日志器端口接口
class ILogger {
public:
    virtual ~ILogger() = default;

    // 设置/获取级别
    virtual void SetLevel(LogLevel level) = 0;
    virtual LogLevel GetLevel() const = 0;

    // 级别判断（用于调用方短路优化，避免不必要的格式化）
    virtual bool ShouldLog(LogLevel level) const = 0;

    // 原始日志接口（调用方已格式化）
    virtual void Log(LogLevel level, std::string_view msg) = 0;
};

} // namespace winsandbox
