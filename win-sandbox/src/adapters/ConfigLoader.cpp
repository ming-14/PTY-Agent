// =============================================================================
// ConfigLoader 实现 - JSON 解析 + schema 校验 + 环境变量展开
//
// 关键流程：
//   Load(path) → 读文件 → ParseAndValidate
//   LoadFromJsonString(s) → ParseAndValidate
//   ParseAndValidate:
//     1. json::parse（捕获语法错误）
//     2. 顶层必须是 object
//     3. 严格模式：拒绝未知字段
//     4. 解析各子节点（logging/default_quota/isolation/monitoring/silo/global_quota）
//     5. 范围校验
//     6. 环境变量展开（仅路径字段）
//
// Helper 设计：
//   Require*  字段必填，类型必须匹配
//   Optional* 字段可选，存在时类型必须匹配
//   失败统一填入 err 字符串并返回 false，便于短路 return
// =============================================================================

#include "adapters/ConfigLoader.hpp"

#include "core/entities/EtwConfig.hpp"

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include <windows.h>

#include <fstream>
#include <format>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>

namespace winsandbox {

namespace {

using json = nlohmann::json;

// ----- 类型与范围 helper -----
//
// 设计：所有字段都是 Optional（缺失时用 BuildDefault 兜底），
// 因此只提供 GetOptional* 系列 helper，不提供 Require* 版本。
// 如果未来出现必填字段，再加 Require* helper。

// 取可选 uint32 字段
// max：可选上界（quota 巨值钳制），超界返回 false
bool GetOptionalUInt32(const json& obj, const std::string& key, const std::string& path,
                       std::optional<uint32_t>& out, std::string& err,
                       uint64_t max = UINT64_MAX) {
    if (!obj.contains(key)) {
        out.reset();
        return true;
    }
    const auto& v = obj[key];
    if (v.is_number_unsigned()) {
        uint64_t uv = v.get<uint64_t>();
        if (uv > max) {
            err = std::format("field {}.{} out of range [0, {}], got {}", path, key, max, uv);
            return false;
        }
        out = static_cast<uint32_t>(uv);
        return true;
    }
    if (v.is_number_integer()) {
        int64_t iv = v.get<int64_t>();
        if (iv < 0) {
            err = std::format("field {}.{} must be non-negative, got {}",
                              path, key, iv);
            return false;
        }
        if (static_cast<uint64_t>(iv) > max) {
            err = std::format("field {}.{} out of range [0, {}], got {}", path, key, max, iv);
            return false;
        }
        out = static_cast<uint32_t>(iv);
        return true;
    }
    err = std::format("field {}.{} must be integer, got {}", path, key,
                      v.type_name());
    return false;
}

// 取可选 uint64 字段
// max：可选上界（quota 巨值钳制），超界返回 false
bool GetOptionalUInt64(const json& obj, const std::string& key, const std::string& path,
                       std::optional<uint64_t>& out, std::string& err,
                       uint64_t max = UINT64_MAX) {
    if (!obj.contains(key)) {
        out.reset();
        return true;
    }
    const auto& v = obj[key];
    if (v.is_number_unsigned()) {
        uint64_t uv = v.get<uint64_t>();
        if (uv > max) {
            err = std::format("field {}.{} out of range [0, {}], got {}", path, key, max, uv);
            return false;
        }
        out = uv;
        return true;
    }
    if (v.is_number_integer()) {
        int64_t iv = v.get<int64_t>();
        if (iv < 0) {
            err = std::format("field {}.{} must be non-negative, got {}",
                              path, key, iv);
            return false;
        }
        if (static_cast<uint64_t>(iv) > max) {
            err = std::format("field {}.{} out of range [0, {}], got {}", path, key, max, iv);
            return false;
        }
        out = static_cast<uint64_t>(iv);
        return true;
    }
    err = std::format("field {}.{} must be integer, got {}", path, key,
                      v.type_name());
    return false;
}

// 取可选 string 字段
bool GetOptionalString(const json& obj, const std::string& key, const std::string& path,
                       std::optional<std::string>& out, std::string& err) {
    if (!obj.contains(key)) {
        out.reset();
        return true;
    }
    const auto& v = obj[key];
    if (!v.is_string()) {
        err = std::format("field {}.{} must be string, got {}", path, key,
                          v.type_name());
        return false;
    }
    out = v.get<std::string>();
    return true;
}

// 取可选 bool 字段
bool GetOptionalBool(const json& obj, const std::string& key, const std::string& path,
                     std::optional<bool>& out, std::string& err) {
    if (!obj.contains(key)) {
        out.reset();
        return true;
    }
    const auto& v = obj[key];
    if (!v.is_boolean()) {
        err = std::format("field {}.{} must be boolean, got {}", path, key,
                          v.type_name());
        return false;
    }
    out = v.get<bool>();
    return true;
}

// 严格模式：检查 obj 中是否有未知字段
// allowed_keys: 允许的字段名集合
bool CheckNoUnknownFields(const json& obj, const std::string& path,
                          const std::vector<std::string>& allowed_keys,
                          std::string& err) {
    for (auto it = obj.begin(); it != obj.end(); ++it) {
        const std::string& key = it.key();
        bool found = false;
        for (const auto& allowed : allowed_keys) {
            if (key == allowed) {
                found = true;
                break;
            }
        }
        if (!found) {
            err = std::format("unknown field: {}.{} (strict mode)", path, key);
            return false;
        }
    }
    return true;
}

// 范围校验：value >= min && value <= max
bool CheckRange(uint32_t value, uint32_t min, uint32_t max,
                const std::string& path, std::string& err) {
    if (value < min || value > max) {
        err = std::format("field {} out of range: {} not in [{}, {}]",
                          path, value, min, max);
        return false;
    }
    return true;
}

} // namespace

// =============================================================================
// ConfigLoader 公开实现
// =============================================================================

ConfigLoader::ConfigLoader(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger)) {
}

SandboxConfig ConfigLoader::Default() {
    return SandboxConfig::BuildDefault();
}

Result<SandboxConfig> ConfigLoader::Load(const std::string& path) {
    // 空路径 → 返回内置默认
    if (path.empty()) {
        logger_->Log(LogLevel::Info,
                     "config path empty, using built-in default");
        return Result<SandboxConfig>::Ok(Default());
    }

    // 读文件
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs.is_open()) {
        // std::ifstream 不设置 GetLastError（值是陈旧的），只记录路径
        logger_->Log(LogLevel::Error,
                     std::format("config file open failed: path={}", path));
        return Result<SandboxConfig>::Err(
            ErrorCode::ConfigFileNotFound,
            std::format("cannot open config file: {}", path));
    }

    std::ostringstream oss;
    oss << ifs.rdbuf();
    std::string content = oss.str();

    logger_->Log(LogLevel::Info,
                 std::format("config file loaded: path={} size={}",
                             path, content.size()));

    return ParseAndValidate(content, path);
}

Result<SandboxConfig> ConfigLoader::LoadFromJsonString(const std::string& json_text) {
    return ParseAndValidate(json_text, "<inline>");
}

std::string ConfigLoader::ExpandEnv(const std::string& s) {
    if (s.empty()) {
        return s;
    }

    // UTF-8 → UTF-16
    int wlen = ::MultiByteToWideChar(CP_UTF8, 0, s.c_str(),
                                     static_cast<int>(s.size()), nullptr, 0);
    if (wlen <= 0) {
        // 转换失败，原样返回
        return s;
    }
    std::wstring ws(wlen, L'\0');
    ::MultiByteToWideChar(CP_UTF8, 0, s.c_str(),
                          static_cast<int>(s.size()), ws.data(), wlen);

    // ExpandEnvironmentStringsW
    // 返回值是目标缓冲区所需字符数（含 null）
    DWORD expanded_len = ::ExpandEnvironmentStringsW(ws.c_str(), nullptr, 0);
    if (expanded_len == 0) {
        return s;
    }
    std::wstring expanded(expanded_len, L'\0');
    ::ExpandEnvironmentStringsW(ws.c_str(), expanded.data(), expanded_len);
    // 去掉末尾 null
    if (!expanded.empty() && expanded.back() == L'\0') {
        expanded.pop_back();
    }

    // UTF-16 → UTF-8
    int ulen = ::WideCharToMultiByte(CP_UTF8, 0, expanded.c_str(),
                                     static_cast<int>(expanded.size()),
                                     nullptr, 0, nullptr, nullptr);
    if (ulen <= 0) {
        return s;
    }
    std::string result(ulen, '\0');
    ::WideCharToMultiByte(CP_UTF8, 0, expanded.c_str(),
                          static_cast<int>(expanded.size()),
                          result.data(), ulen, nullptr, nullptr);
    return result;
}

// 展开环境变量（严格版）：展开后若仍含 '%'（未定义/未闭合/畸形变量按字面
//   残留，Windows 行为），视为非法返回 false；合法展开（如 %TEMP% → 真实路径）
//   返回 true。
//
// 原因：未展开的畸形变量（%UNCLOSED、%%、%A%B%、${UNCLOSED 等）被按字面传给
//   create_directories → 在磁盘上创建含 % 字面量的目录并写入日志
//   （%TEMP%\%UNCLOSED、Windows 目录 Temp、盘符根目录等）。
//   变量无法展开时应拒绝配置，而非把攻击者可控的字符串落盘。
bool ConfigLoader::ExpandEnvStrict(const std::string& s, std::string& out,
                                   std::string& err) {
    out = ExpandEnv(s);
    // '%' 是环境变量定界符；展开后仍含 '%' 说明存在未定义/未闭合变量。
    // Windows 路径合法字符包含 '%'，但配置路径语义上不应出现字面 '%'，
    // 保守拒绝以消除"按字面创建目录"风险。
    // 另检查 ${...} 字面量（非 Windows 环境变量语法，ExpandEnvironmentStringsW
    //   不处理，直接按字面保留 → 同样不应作为路径创建）。
    if (out.find('%') != std::string::npos ||
        out.find("${") != std::string::npos) {
        err = std::format("path contains unresolvable environment variable: {}", s);
        return false;
    }
    return true;
}

// =============================================================================
// ParseAndValidate - 解析 + schema 校验 + 转换
// =============================================================================

Result<SandboxConfig> ConfigLoader::ParseAndValidate(const std::string& json_text,
                                                       const std::string& source_desc) {
    // 1. JSON 解析
    json root;
    try {
        root = json::parse(json_text);
    } catch (const json::parse_error& e) {
        logger_->Log(LogLevel::Error,
                     std::format("config JSON parse failed: source={} what={}",
                                 source_desc, e.what()));
        return Result<SandboxConfig>::Err(
            ErrorCode::ConfigParseFailed,
            std::format("JSON parse error: {}", e.what()));
    }

    // 2. 顶层必须是 object
    if (!root.is_object()) {
        return Result<SandboxConfig>::Err(
            ErrorCode::ConfigSchemaValidationFailed,
            std::format("config root must be object, got {}", root.type_name()));
    }

    // 3. 严格模式：检查顶层未知字段
    //    顶层所有子节点都是可选（用 BuildDefault 兜底），但必须是已知 key
    std::string err;
    if (!CheckNoUnknownFields(root, "root",
                               {"logging", "default_quota", "isolation",
                                "monitoring", "silo", "global_quota"}, err)) {
        logger_->Log(LogLevel::Error,
                     std::format("config schema error: {} source={}", err, source_desc));
        return Result<SandboxConfig>::Err(
            ErrorCode::ConfigSchemaValidationFailed, err);
    }

    // 从默认配置开始叠加（缺失字段用默认值）
    SandboxConfig cfg = SandboxConfig::BuildDefault();

    // 4. logging
    if (root.contains("logging")) {
        const auto& lg = root["logging"];
        if (!lg.is_object()) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed,
                std::format("logging must be object, got {}", lg.type_name()));
        }
        if (!CheckNoUnknownFields(lg, "logging",
                                  {"level", "dir", "retention_days"}, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }

        std::optional<std::string> level_opt;
        if (!GetOptionalString(lg, "level", "logging", level_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (level_opt) {
            // 校验 level 取值
            const std::string& lv = *level_opt;
            if (lv != "trace" && lv != "debug" && lv != "info"
                && lv != "warn" && lv != "error") {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("logging.level invalid: {} (allowed: trace|debug|info|warn|error)",
                                lv));
            }
            cfg.logging.level = lv;
        }

        std::optional<std::string> dir_opt;
        if (!GetOptionalString(lg, "dir", "logging", dir_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (dir_opt) {
            // 展开环境变量（畸形变量拒绝，不按字面创建目录）
            std::string expanded_dir;
            if (!ExpandEnvStrict(*dir_opt, expanded_dir, err)) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("logging.dir: {}", err));
            }
            cfg.logging.dir = expanded_dir;
        }

        std::optional<uint32_t> retention_opt;
        // retention_days 加上界（999999999 是畸形值，此前静默接受）。
        // 上限 36500（100 年），足够覆盖真实需求。
        if (!GetOptionalUInt32(lg, "retention_days", "logging", retention_opt, err,
                               36500)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (retention_opt) {
            cfg.logging.retention_days = *retention_opt;
            // 0 = 永久保留，合法；不做下限校验
        }
    }

    // 5. default_quota
    if (root.contains("default_quota")) {
        const auto& q = root["default_quota"];
        if (!q.is_object()) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed,
                std::format("default_quota must be object, got {}", q.type_name()));
        }
if (!CheckNoUnknownFields(q, "default_quota",
                                  {"cpu_ms", "cpu_rate_percent", "memory_mb",
                                   "job_memory_mb", "io_rate_bytes_per_sec",
                                   "io_rate_iops", "max_processes",
                                   "wall_clock_timeout_ms", "cpu_timeout_ms",
                                   "no_ui", "breakaway_ok", "crash_silent"},
                                  err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }

        // quota size 字段加合理上界（2^40 ≈ 1TB/34 年），
        // 拒绝 2^63 等荒谬巨值（校验只有下界无上界不对称）
        constexpr uint64_t kMaxQuotaValue = 1ULL << 40;

        // CPU 时间（ms）
        std::optional<uint64_t> cpu_ms_opt;
        if (!GetOptionalUInt64(q, "cpu_ms", "default_quota", cpu_ms_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (cpu_ms_opt) {
            cfg.default_quota.cpu_ms = *cpu_ms_opt;
        }

        // CPU 占比（1-100）
        std::optional<uint32_t> cpu_rate_opt;
        if (!GetOptionalUInt32(q, "cpu_rate_percent", "default_quota", cpu_rate_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (cpu_rate_opt) {
            if (!CheckRange(*cpu_rate_opt, 1, 100, "default_quota.cpu_rate_percent", err)) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed, err);
            }
            cfg.default_quota.cpu_rate_percent = *cpu_rate_opt;
        }

        // 内存（MB）必须 > 0
        std::optional<uint64_t> mem_opt;
        if (!GetOptionalUInt64(q, "memory_mb", "default_quota", mem_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (mem_opt) {
            if (*mem_opt == 0) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    "default_quota.memory_mb must be > 0");
            }
            cfg.default_quota.memory_mb = *mem_opt;
        }

        // Job 内存（MB）
        std::optional<uint64_t> job_mem_opt;
        if (!GetOptionalUInt64(q, "job_memory_mb", "default_quota", job_mem_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (job_mem_opt) {
            if (*job_mem_opt == 0) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    "default_quota.job_memory_mb must be > 0");
            }
            cfg.default_quota.job_memory_mb = *job_mem_opt;
        }

        // IO 速率（加 kMaxQuotaValue 上界）
        std::optional<uint64_t> io_bps_opt;
        if (!GetOptionalUInt64(q, "io_rate_bytes_per_sec", "default_quota", io_bps_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (io_bps_opt) {
            cfg.default_quota.io_rate_bytes_per_sec = *io_bps_opt;
        }

        std::optional<uint64_t> io_iops_opt;
        if (!GetOptionalUInt64(q, "io_rate_iops", "default_quota", io_iops_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (io_iops_opt) {
            cfg.default_quota.io_rate_iops = *io_iops_opt;
        }

        // 进程数（上界 65536，防 1e12 荒谬值）
        std::optional<uint32_t> max_proc_opt;
        if (!GetOptionalUInt32(q, "max_processes", "default_quota", max_proc_opt, err,
                               65536)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (max_proc_opt) {
            if (*max_proc_opt == 0) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    "default_quota.max_processes must be > 0");
            }
            cfg.default_quota.max_processes = *max_proc_opt;
        }

        // 超时（加 kMaxQuotaValue 上界）
        std::optional<uint64_t> wall_opt;
        if (!GetOptionalUInt64(q, "wall_clock_timeout_ms", "default_quota", wall_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (wall_opt) {
            cfg.default_quota.wall_clock_timeout_ms = *wall_opt;
        }

        std::optional<uint64_t> cpu_timeout_opt;
        if (!GetOptionalUInt64(q, "cpu_timeout_ms", "default_quota", cpu_timeout_opt, err,
                               kMaxQuotaValue)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (cpu_timeout_opt) {
            cfg.default_quota.cpu_timeout_ms = *cpu_timeout_opt;
        }

        // UI 限制
        std::optional<bool> no_ui_opt;
        if (!GetOptionalBool(q, "no_ui", "default_quota", no_ui_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (no_ui_opt) {
            cfg.default_quota.no_ui = *no_ui_opt;
        }

        // breakaway
        std::optional<bool> breakaway_opt;
        if (!GetOptionalBool(q, "breakaway_ok", "default_quota", breakaway_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (breakaway_opt) {
            cfg.default_quota.breakaway_ok = *breakaway_opt;
        }

        // 崩溃静默
        std::optional<bool> crash_silent_opt;
        if (!GetOptionalBool(q, "crash_silent", "default_quota", crash_silent_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (crash_silent_opt) {
            cfg.default_quota.crash_silent = *crash_silent_opt;
        }
    }

    // 6. isolation（Low IL 模型的默认隔离策略）
    //    schema：
    //      "net_policy": "unrestricted" | "allowlist"
    //        （非法值显式拒绝：纯用户态无法系统级执行网络限制，杜绝静默失效）
    //      "net_allowlist": [{ "ip", "port", "protocol" }, ...]（仅 allowlist 生效）
    //      "clipboard_isolate": bool（Job UI 限制：剪贴板/全局原子表/系统参数）
    if (root.contains("isolation")) {
        const auto& iso = root["isolation"];
        if (!iso.is_object()) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed,
                std::format("isolation must be object, got {}", iso.type_name()));
        }
        if (!CheckNoUnknownFields(iso, "isolation",
                                  {"net_policy", "net_allowlist", "clipboard_isolate"}, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }

        // net_policy（收敛为 unrestricted | allowlist）
        if (iso.contains("net_policy")) {
            const auto& p = iso["net_policy"];
            if (!p.is_string()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("isolation.net_policy must be string, got {}", p.type_name()));
            }
            const std::string& s = p.get<std::string>();
            if (s == "unrestricted") {
                cfg.default_isolation_policy.net_policy = NetworkPolicy::Unrestricted;
            } else if (s == "allowlist") {
                cfg.default_isolation_policy.net_policy = NetworkPolicy::Allowlist;
            } else {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("isolation.net_policy invalid: {} "
                                "(allowed: unrestricted|allowlist)", s));
            }
        }

        // net_allowlist：IP/port 白名单规则（仅 net_policy=allowlist 时生效）
        if (iso.contains("net_allowlist")) {
            const auto& al = iso["net_allowlist"];
            if (!al.is_array()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("isolation.net_allowlist must be array, got {}", al.type_name()));
            }
            for (size_t i = 0; i < al.size(); ++i) {
                const auto& rule = al[i];
                if (!rule.is_object()) {
                    return Result<SandboxConfig>::Err(
                        ErrorCode::ConfigSchemaValidationFailed,
                        std::format("isolation.net_allowlist[{}] must be object", i));
                }
                NetworkRule nr;
                if (rule.contains("ip") && rule["ip"].is_string()) {
                    nr.ip = rule["ip"].get<std::string>();
                }
                // port/protocol 必须为整数且在可表示范围内：
                // 超界截断回绕会静默篡改白名单语义（65536→0=匹配任意端口）
                if (rule.contains("port")) {
                    const auto& pv = rule["port"];
                    if (!pv.is_number_unsigned()) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed,
                            std::format("isolation.net_allowlist[{}].port must be integer",
                                        i));
                    }
                    uint64_t port = pv.get<uint64_t>();
                    if (port > UINT16_MAX) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed,
                            std::format("isolation.net_allowlist[{}].port out of range "
                                        "[0, 65535], got {}", i, port));
                    }
                    nr.port = static_cast<uint16_t>(port);
                }
                if (rule.contains("protocol")) {
                    const auto& protov = rule["protocol"];
                    if (!protov.is_number_unsigned()) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed,
                            std::format("isolation.net_allowlist[{}].protocol must be integer",
                                        i));
                    }
                    uint64_t proto = protov.get<uint64_t>();
                    if (proto > UINT8_MAX) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed,
                            std::format("isolation.net_allowlist[{}].protocol out of range "
                                        "[0, 255], got {}", i, proto));
                    }
                    nr.protocol = static_cast<uint8_t>(proto);
                }
                cfg.default_isolation_policy.net_allowlist.push_back(std::move(nr));
            }
        }

        // clipboard_isolate（Job UI 限制开关）
        if (iso.contains("clipboard_isolate")) {
            if (!iso["clipboard_isolate"].is_boolean()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("isolation.clipboard_isolate must be bool, got {}",
                                iso["clipboard_isolate"].type_name()));
            }
            cfg.default_isolation_policy.clipboard_isolate = iso["clipboard_isolate"].get<bool>();
        }
    }

    // 7. monitoring
    if (root.contains("monitoring")) {
        const auto& mon = root["monitoring"];
        if (!mon.is_object()) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed,
                std::format("monitoring must be object, got {}", mon.type_name()));
        }
        if (!CheckNoUnknownFields(mon, "monitoring",
                {"etw_enabled", "ring_buffer_size", "dispatch_batch_size",
                 "dispatch_timeout_ms", "stats_interval_ms", "filter_pids",
                 "degraded_monitor_dirs", "force_degraded", "degraded_net_polling"}, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (mon.contains("etw_enabled")) {
            if (!mon["etw_enabled"].is_boolean()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("monitoring.etw_enabled must be boolean, got {}",
                                mon["etw_enabled"].type_name()));
            }
            cfg.monitoring.etw_enabled = mon["etw_enabled"].get<bool>();
        }
        if (cfg.monitoring.etw_enabled) {
            cfg.monitoring.etw.enabled = true;
            // 数值字段：统一走严格整数 helper（浮点/负数/超大值拒绝而非抛异常/静默忽略）
            std::optional<uint32_t> ring_opt, batch_opt, timeout_opt, stats_opt;
            if (!GetOptionalUInt32(mon, "ring_buffer_size", "monitoring", ring_opt, err)) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed, err);
            }
            if (ring_opt) {
                cfg.monitoring.etw.ring_buffer_size = *ring_opt;
            }
            if (!GetOptionalUInt32(mon, "dispatch_batch_size", "monitoring", batch_opt, err)) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed, err);
            }
            if (batch_opt) {
                cfg.monitoring.etw.dispatch_batch_size = *batch_opt;
            }
            if (!GetOptionalUInt32(mon, "dispatch_timeout_ms", "monitoring", timeout_opt, err)) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed, err);
            }
            if (timeout_opt) {
                cfg.monitoring.etw.dispatch_timeout_ms = *timeout_opt;
            }
            if (!GetOptionalUInt32(mon, "stats_interval_ms", "monitoring", stats_opt, err)) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed, err);
            }
            if (stats_opt) {
                cfg.monitoring.etw.stats_interval_ms = *stats_opt;
            }
            cfg.monitoring.etw.sessions = EtwConfig::Default().sessions;

            // filter_pids：PID 白名单（仅处理这些进程的 ETW 事件，源头减噪）
            if (mon.contains("filter_pids")) {
                if (!mon["filter_pids"].is_array()) {
                    return Result<SandboxConfig>::Err(
                        ErrorCode::ConfigSchemaValidationFailed,
                        "monitoring.filter_pids must be array of non-negative integers");
                }
                std::vector<uint32_t> pids;
                for (size_t i = 0; i < mon["filter_pids"].size(); ++i) {
                    std::optional<uint32_t> pid_opt;
                    nlohmann::json wrapped;
                    wrapped["v"] = mon["filter_pids"][i];
                    if (!GetOptionalUInt32(wrapped, "v",
                            std::format("monitoring.filter_pids[{}]", i), pid_opt, err)) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed, err);
                    }
                    pids.push_back(*pid_opt);
                }
                cfg.monitoring.etw.filter_pids = std::move(pids);
            }

            // 降级模式扩展配置
            // degraded_monitor_dirs: 文件监控目录（ReadDirectoryChangesW 递归监控，非管理员可用）
            if (mon.contains("degraded_monitor_dirs")) {
                if (!mon["degraded_monitor_dirs"].is_array()) {
                    return Result<SandboxConfig>::Err(
                        ErrorCode::ConfigSchemaValidationFailed,
                        "monitoring.degraded_monitor_dirs must be array of strings");
                }
                std::vector<std::string> dirs;
                for (const auto& item : mon["degraded_monitor_dirs"]) {
                    if (!item.is_string()) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed,
                            "monitoring.degraded_monitor_dirs must be array of strings");
                    }
                    // 严格展开：畸形环境变量（%UNCLOSED 等按字面残留）拒绝，
                    // 防止把攻击者可控字符串按字面传给 ReadDirectoryChangesW
                    std::string expanded_dir;
                    if (!ExpandEnvStrict(item.get<std::string>(), expanded_dir, err)) {
                        return Result<SandboxConfig>::Err(
                            ErrorCode::ConfigSchemaValidationFailed,
                            std::format("monitoring.degraded_monitor_dirs[{}]: {}", dirs.size(), err));
                    }
                    dirs.push_back(std::move(expanded_dir));
                }
                cfg.monitoring.etw.degraded_monitor_dirs = std::move(dirs);
            }
            // force_degraded: 强制降级模式（即使管理员也走降级路径，用于验证）
            if (mon.contains("force_degraded")) {
                if (!mon["force_degraded"].is_boolean()) {
                    return Result<SandboxConfig>::Err(
                        ErrorCode::ConfigSchemaValidationFailed,
                        std::format("monitoring.force_degraded must be boolean, got {}",
                                    mon["force_degraded"].type_name()));
                }
                cfg.monitoring.etw.force_degraded = mon["force_degraded"].get<bool>();
            }
            // degraded_net_polling: 降级模式网络轮询开关（默认开）
            if (mon.contains("degraded_net_polling")) {
                if (!mon["degraded_net_polling"].is_boolean()) {
                    return Result<SandboxConfig>::Err(
                        ErrorCode::ConfigSchemaValidationFailed,
                        std::format("monitoring.degraded_net_polling must be boolean, got {}",
                                    mon["degraded_net_polling"].type_name()));
                }
                cfg.monitoring.etw.degraded_net_polling = mon["degraded_net_polling"].get<bool>();
            }
        }
    }

    // 8. silo（候选：Server Silo 更强隔离）
    if (root.contains("silo")) {
        const auto& silo = root["silo"];
        if (!silo.is_object()) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed,
                std::format("silo must be object, got {}", silo.type_name()));
        }
        if (!CheckNoUnknownFields(silo, "silo", {"enabled"}, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (silo.contains("enabled")) {
            if (!silo["enabled"].is_boolean()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("silo.enabled must be boolean, got {}",
                                silo["enabled"].type_name()));
            }
            cfg.silo.enabled = silo["enabled"].get<bool>();
        }
    }

    // 9. global_quota（候选：多沙箱全局资源配额）
    if (root.contains("global_quota")) {
        const auto& gq = root["global_quota"];
        if (!gq.is_object()) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed,
                std::format("global_quota must be object, got {}", gq.type_name()));
        }
        if (!CheckNoUnknownFields(gq, "global_quota",
                                  {"enabled", "pool_name",
                                   "max_cpu_rate_percent", "max_memory_mb",
                                   "max_processes"}, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (gq.contains("enabled")) {
            if (!gq["enabled"].is_boolean()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("global_quota.enabled must be boolean, got {}",
                                gq["enabled"].type_name()));
            }
            cfg.global_quota.enabled = gq["enabled"].get<bool>();
        }
        if (gq.contains("pool_name")) {
            if (!gq["pool_name"].is_string()) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    std::format("global_quota.pool_name must be string, got {}",
                                gq["pool_name"].type_name()));
            }
            cfg.global_quota.pool_name = gq["pool_name"].get<std::string>();
        }
        // 数值字段：严格整数 helper（浮点等非法类型拒绝，而非 get<int> 抛异常逃逸）
        std::optional<uint32_t> gq_cpu_opt, gq_proc_opt;
        std::optional<uint64_t> gq_mem_opt;
        if (!GetOptionalUInt32(gq, "max_cpu_rate_percent", "global_quota",
                               gq_cpu_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (gq_cpu_opt) {
            if (*gq_cpu_opt < 1 || *gq_cpu_opt > 100) {
                return Result<SandboxConfig>::Err(
                    ErrorCode::ConfigSchemaValidationFailed,
                    "global_quota.max_cpu_rate_percent must be 1-100");
            }
            cfg.global_quota.max_cpu_rate_percent = *gq_cpu_opt;
        }
        if (!GetOptionalUInt64(gq, "max_memory_mb", "global_quota", gq_mem_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (gq_mem_opt) {
            cfg.global_quota.max_memory_mb = *gq_mem_opt;
        }
        if (!GetOptionalUInt32(gq, "max_processes", "global_quota", gq_proc_opt, err)) {
            return Result<SandboxConfig>::Err(
                ErrorCode::ConfigSchemaValidationFailed, err);
        }
        if (gq_proc_opt) {
            cfg.global_quota.max_processes = *gq_proc_opt;
        }
    }

    logger_->Log(LogLevel::Info,
                 std::format("config parsed OK: source={} level={} mem_mb={} max_proc={} "
                             "net_policy={} clipboard={} etw={} silo={} gq={}",
                             source_desc, cfg.logging.level,
                             cfg.default_quota.memory_mb.value_or(0),
                             cfg.default_quota.max_processes.value_or(0),
                             static_cast<int>(cfg.default_isolation_policy.net_policy),
                             cfg.default_isolation_policy.clipboard_isolate,
                             cfg.monitoring.etw_enabled,
                             cfg.silo.enabled,
                             cfg.global_quota.enabled));

    return Result<SandboxConfig>::Ok(std::move(cfg));
}

} // namespace winsandbox
