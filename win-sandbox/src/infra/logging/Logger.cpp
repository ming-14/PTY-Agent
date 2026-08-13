// =============================================================================
// Logger 实现 - spdlog 封装
//
// 实现 SpdlogLogger（ILogger 接口）+ Logger 静态初始化器。
// 日志路径：%TEMP%\win-sandbox-<pid>\sandbox.log（按天滚动）
// =============================================================================

#include "infra/logging/Logger.hpp"

#include <spdlog/spdlog.h>
#include <spdlog/sinks/daily_file_sink.h>
#include <spdlog/sinks/stdout_color_sinks.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <string>
#include <system_error>
#include <utility>

#include <windows.h>

namespace winsandbox {

namespace {

// ----- 级别转换 -----

spdlog::level::level_enum StringToLevel(const std::string& s) {
    if (s == "trace") return spdlog::level::trace;
    if (s == "debug") return spdlog::level::debug;
    if (s == "info")  return spdlog::level::info;
    if (s == "warn")  return spdlog::level::warn;
    if (s == "error") return spdlog::level::err;
    return spdlog::level::info;
}

spdlog::level::level_enum LogLevelToSpdlog(LogLevel l) {
    switch (l) {
        case LogLevel::Trace: return spdlog::level::trace;
        case LogLevel::Debug: return spdlog::level::debug;
        case LogLevel::Info:  return spdlog::level::info;
        case LogLevel::Warn:  return spdlog::level::warn;
        case LogLevel::Error: return spdlog::level::err;
    }
    return spdlog::level::info;
}

LogLevel SpdlogToLogLevel(spdlog::level::level_enum l) {
    switch (l) {
        case spdlog::level::trace: return LogLevel::Trace;
        case spdlog::level::debug: return LogLevel::Debug;
        case spdlog::level::info:  return LogLevel::Info;
        case spdlog::level::warn:  return LogLevel::Warn;
        case spdlog::level::err:   return LogLevel::Error;
        default: return LogLevel::Info;
    }
}

// ----- 日志目录： %LOCALAPPDATA%\win-sandbox\logs\ -----
// 默认目录固定为文档承诺的 %LOCALAPPDATA%\win-sandbox\logs；
// 统一目录 + retention_days 清理可避免 %TEMP% 独立目录长期无回收累积。
// 使用窄字符 API（spdlog 默认 filename_t = std::string）
std::string GetLogDir() {
    // %LOCALAPPDATA% 为当前用户的应用数据目录（含用户名）
    char buf[MAX_PATH];
    DWORD len = GetEnvironmentVariableA("LOCALAPPDATA", buf, MAX_PATH);
    if (len > 0 && len < MAX_PATH) {
        std::string base(buf, len);
        if (!base.empty() && base.back() != '\\' && base.back() != '/') {
            base += '\\';
        }
        return base + "win-sandbox\\logs\\";
    }
    // %LOCALAPPDATA% 缺失（服务场景）：回退到系统临时目录
    //（相对当前目录的 CWD 不可控，不能用）
    char temp_buf[MAX_PATH];
    DWORD tlen = GetTempPathA(MAX_PATH, temp_buf);
    if (tlen > 0 && tlen < MAX_PATH) {
        std::string base(temp_buf, tlen);
        if (!base.empty() && base.back() != '\\' && base.back() != '/') {
            base += '\\';
        }
        return base + "win-sandbox-logs\\";
    }
    return ".\\win-sandbox-logs\\";
}

// 清理过期日志文件：
//   1. 当前日志目录下过期（> retention_days）的 sandbox*.log* 文件
//   2. %TEMP%\win-sandbox-<pid>（历史日志目录，仍清理累积的过期目录）
//   不动 temp workspace 目录（win-sandbox-<pid>-<ms>，由 exit_strategy 管理）。
void CleanupStaleLogs(const std::string& log_dir, uint32_t retention_days) {
    if (retention_days == 0) return;  // 0 = 永久保留
    const auto cutoff = std::filesystem::file_time_type::clock::now() -
                        std::chrono::hours(24 * retention_days);
    std::error_code ec;

    // 1. 清理当前日志目录下的过期日志文件
    if (!log_dir.empty()) {
        for (const auto& entry : std::filesystem::directory_iterator(log_dir, ec)) {
            if (ec) break;
            if (!entry.is_regular_file(ec)) continue;
            const std::string name = entry.path().filename().string();
            if (name.rfind("sandbox", 0) != 0) continue;  // sandbox.log / sandbox.log.YYYY-MM-DD
            if (name.find(".log") == std::string::npos) continue;
            const auto last_write = entry.last_write_time(ec);
            if (ec) continue;
            if (last_write < cutoff) {
                std::filesystem::remove(entry.path(), ec);
                ec.clear();
            }
        }
    }

    // 2. 清理 %TEMP% 下过期的 win-sandbox-<pid> 日志目录
    char temp_buf[MAX_PATH];
    DWORD len = GetTempPathA(MAX_PATH, temp_buf);
    if (len == 0 || len >= MAX_PATH) return;
    std::string temp(temp_buf, strnlen(temp_buf, MAX_PATH));
    const std::string kPrefix = "win-sandbox-";
    for (const auto& entry : std::filesystem::directory_iterator(temp, ec)) {
        if (ec) break;
        if (!entry.is_directory(ec)) continue;
        const std::string name = entry.path().filename().string();
        if (name.rfind(kPrefix, 0) != 0) continue;
        const std::string rest = name.substr(kPrefix.size());
        // 仅匹配纯 pid（全数字），跳过 win-sandbox-<pid>-<ms> 工作区目录
        if (rest.empty() || !std::all_of(rest.begin(), rest.end(),
                                         [](char c) { return c >= '0' && c <= '9'; })) {
            continue;
        }
        const auto last_write = entry.last_write_time(ec);
        if (ec) continue;
        if (last_write < cutoff) {
            std::filesystem::remove_all(entry.path(), ec);
            ec.clear();
        }
    }
}

// ----- SpdlogLogger： ILogger 接口的 spdlog 实现 -----
class SpdlogLogger : public ILogger {
public:
    explicit SpdlogLogger(std::shared_ptr<spdlog::logger> spd_logger)
        : spd_logger_(std::move(spd_logger)) {}

    void SetLevel(LogLevel level) override {
        spd_logger_->set_level(LogLevelToSpdlog(level));
    }

    LogLevel GetLevel() const override {
        return SpdlogToLogLevel(spd_logger_->level());
    }

    bool ShouldLog(LogLevel level) const override {
        return LogLevelToSpdlog(level) >= spd_logger_->level();
    }

    void Log(LogLevel level, std::string_view msg) override {
        spd_logger_->log(LogLevelToSpdlog(level), std::string(msg));
    }

private:
    std::shared_ptr<spdlog::logger> spd_logger_;
};

} // namespace

// ----- Logger 静态成员定义 -----

std::shared_ptr<ILogger> Logger::instance_;
std::mutex Logger::init_mutex_;

std::shared_ptr<ILogger> Logger::Init(const std::string& level,
                                      const std::string& log_dir,
                                      uint32_t retention_days) {
    // 锁内检查+创建：并发 Init 不双开同名日志文件
    std::lock_guard<std::mutex> lk(init_mutex_);
    if (instance_) return instance_;
    return InitInternal(level, log_dir, retention_days);
}

// 无锁初始化本体（调用方必须已持有 init_mutex_）
std::shared_ptr<ILogger> Logger::InitInternal(const std::string& level,
                                              const std::string& log_dir,
                                              uint32_t retention_days) {
    auto spd_level = StringToLevel(level);

    // 创建日志目录（尊重配置的 logging.dir，而非硬编码 %TEMP%）
    // 路径拼接：dir 可能没有尾部分隔符（文档示例
    //   %LOCALAPPDATA%\win-sandbox\logs 就无尾反斜杠），直接 + "sandbox.log"
    //   会拼成 logssandbox.log。统一规范化：无尾分隔符则补一个。
    const std::string effective_dir = log_dir.empty() ? GetLogDir() : log_dir;
    std::error_code ec;
    std::filesystem::create_directories(effective_dir, ec);
    std::string dir_norm = effective_dir;
    if (!dir_norm.empty() &&
        dir_norm.back() != '/' && dir_norm.back() != '\\') {
        dir_norm += '\\';
    }
    std::string log_path = dir_norm + "sandbox.log";

    // file sink：按天滚动（每天 00:00 切割）
    auto file_sink = std::make_shared<spdlog::sinks::daily_file_sink_mt>(
        log_path, 0, 0);
    file_sink->set_level(spd_level);

    // stderr sink：彩色输出
    auto console_sink = std::make_shared<spdlog::sinks::stderr_color_sink_mt>();
    console_sink->set_level(spd_level);

    // 多 sink logger
    auto spd_logger = std::make_shared<spdlog::logger>(
        "winsandbox",
        spdlog::sinks_init_list{file_sink, console_sink});
    spd_logger->set_level(spd_level);
    spd_logger->set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%^%l%$] [%t] %v");
    // warn 及以上立即 flush，避免崩溃丢失日志
    spd_logger->flush_on(spdlog::level::warn);

    // 注册为 spdlog 默认 logger（spdlog::info 等全局接口可用）
    spdlog::set_default_logger(spd_logger);

    // 启动时按 retention_days 清理过期日志
    CleanupStaleLogs(dir_norm, retention_days);

    instance_ = std::make_shared<SpdlogLogger>(std::move(spd_logger));
    return instance_;
}

std::shared_ptr<ILogger> Logger::Configure(const std::string& level,
                                           const std::string& log_dir,
                                           uint32_t retention_days) {
    // 配置加载完成后按配置重初始化（清理旧实例，让 dir/level/retention 生效）
    // 锁内 shutdown+Init：与 Init/Shutdown 并发安全
    std::lock_guard<std::mutex> lk(init_mutex_);
    if (instance_) {
        spdlog::shutdown();
        instance_.reset();
    }
    // 复用 Init 的内部逻辑（避免双重加锁：Init 内部会再锁，std::mutex 非递归）
    // → 直接在此复制初始化过程，或用无锁内部函数
    return InitInternal(level, log_dir, retention_days);
}

void Logger::Shutdown() {
    std::lock_guard<std::mutex> lk(init_mutex_);
    spdlog::shutdown();
    instance_.reset();
}

std::shared_ptr<ILogger> Logger::Get() {
    std::lock_guard<std::mutex> lk(init_mutex_);
    return instance_;
}

} // namespace winsandbox
