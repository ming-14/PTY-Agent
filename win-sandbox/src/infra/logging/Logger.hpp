// =============================================================================
// Logger - 日志系统初始化器（infra 层）
//
// 封装 spdlog 初始化：
//   - file sink：按天滚动（每天 00:00），路径 %LOCALAPPDATA%\win-sandbox\logs\sandbox.log
//   - stderr sink：彩色输出
//   - 同时注册为 spdlog 默认 logger（spdlog::info 等全局接口可用）
//
// 线程安全：Init/Configure/Shutdown/Get 全部加内部锁，
// 并发 Init（多线程启动）不会双开同名日志文件。
//
// 提供 ILogger 实现（SpdlogLogger），core 层通过 ILogger 接口记录日志。
// =============================================================================
#pragma once

#include "core/ports/ILogger.hpp"

#include <memory>
#include <mutex>
#include <string>

namespace winsandbox {

// 日志系统初始化器
class Logger {
public:
    // 初始化全局日志系统
    // level: "trace"/"debug"/"info"/"warn"/"error"
    // log_dir: 日志目录（空则用默认 %LOCALAPPDATA%\win-sandbox\logs\）
    // retention_days: 日志保留天数（0 = 永久保留）。启动时清理过期日志文件与
    //   过期的 %TEMP%\win-sandbox-<pid> 日志目录
    // 返回 ILogger 实例供 core 层使用
    static std::shared_ptr<ILogger> Init(const std::string& level = "info",
                                         const std::string& log_dir = "",
                                         uint32_t retention_days = 7);

    // 按新配置重新初始化（先关闭旧 logger 再 Init）
    // 用于配置文件加载后让 logging.dir / retention_days 生效
    static std::shared_ptr<ILogger> Configure(const std::string& level,
                                              const std::string& log_dir,
                                              uint32_t retention_days);

    // 关闭日志系统（刷新所有 sink，释放资源）
    static void Shutdown();

    // 获取已初始化的 ILogger（Init 后调用；未 Init 返回 nullptr）
    static std::shared_ptr<ILogger> Get();

private:
    // 无锁初始化（调用方必须已持有 init_mutex_）
    static std::shared_ptr<ILogger> InitInternal(const std::string& level,
                                                 const std::string& log_dir,
                                                 uint32_t retention_days);

    static std::shared_ptr<ILogger> instance_;
    static std::mutex init_mutex_;  // 串行化 Init/Configure/Shutdown/Get
};

} // namespace winsandbox
