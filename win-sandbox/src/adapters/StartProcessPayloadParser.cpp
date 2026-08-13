// =============================================================================
// StartProcessPayloadParser 实现
//
// 从 main.cpp 抽出，便于 verify_t27 单元测试 IPC payload 解析逻辑。
// =============================================================================

#include "adapters/StartProcessPayloadParser.hpp"
#include "core/entities/NetworkRule.hpp"

#include <nlohmann/json.hpp>

#include <format>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <algorithm>

namespace winsandbox {

namespace {

using json = nlohmann::json;

// C-4 修复：严格取值 helper（对齐 ConfigLoader 的 GetOptionalUInt64 标准）
//   - 类型不符（字符串/浮点/布尔等）→ 返回 false + 错误信息（不静默跳过）
//   - 负整数 → 返回 false（不因 static_cast<uint64_t> 回绕成超大值）
//   - 合法非负整数 → 写入 out，返回 true
bool GetQuotaUInt64(const json& obj, const std::string& key, const std::string& path,
                    std::optional<uint64_t>& out, std::string& err) {
    if (!obj.contains(key)) {
        out.reset();
        return true;
    }
    const auto& v = obj[key];
    if (v.is_number_unsigned()) {
        out = v.get<uint64_t>();
        return true;
    }
    if (v.is_number_integer()) {
        int64_t iv = v.get<int64_t>();
        if (iv < 0) {
            err = std::format("field {}.{} must be non-negative, got {}", path, key, iv);
            return false;
        }
        out = static_cast<uint64_t>(iv);
        return true;
    }
    err = std::format("field {}.{} must be integer, got {}", path, key, v.type_name());
    return false;
}

// C-4 修复：严格取值 helper（uint32，用于 cpu_rate_percent / max_processes）
bool GetQuotaUInt32(const json& obj, const std::string& key, const std::string& path,
                    std::optional<uint32_t>& out, std::string& err) {
    if (!obj.contains(key)) {
        out.reset();
        return true;
    }
    const auto& v = obj[key];
    if (v.is_number_unsigned()) {
        out = v.get<uint32_t>();
        return true;
    }
    if (v.is_number_integer()) {
        int64_t iv = v.get<int64_t>();
        if (iv < 0) {
            err = std::format("field {}.{} must be non-negative, got {}", path, key, iv);
            return false;
        }
        if (iv > INT32_MAX) {
            err = std::format("field {}.{} out of range: {}", path, key, iv);
            return false;
        }
        out = static_cast<uint32_t>(iv);
        return true;
    }
    err = std::format("field {}.{} must be integer, got {}", path, key, v.type_name());
    return false;
}

// C-4 修复：严格 bool 取值（no_ui / breakaway_ok）
bool GetQuotaBool(const json& obj, const std::string& key, const std::string& path,
                  bool& out, std::string& err) {
    if (!obj.contains(key)) {
        return true;
    }
    const auto& v = obj[key];
    if (!v.is_boolean()) {
        err = std::format("field {}.{} must be boolean, got {}", path, key, v.type_name());
        return false;
    }
    out = v.get<bool>();
    return true;
}

// C-4 修复：拒绝未知字段（对齐 ConfigLoader strict mode）
bool CheckNoUnknownQuotaFields(const json& obj, const std::string& path,
                               const std::vector<std::string>& allowed_keys,
                               std::string& err) {
    for (auto it = obj.begin(); it != obj.end(); ++it) {
        bool found = false;
        for (const auto& allowed : allowed_keys) {
            if (it.key() == allowed) {
                found = true;
                break;
            }
        }
        if (!found) {
            err = std::format("unknown field: {}.{} (strict mode)", path, it.key());
            return false;
        }
    }
    return true;
}

// MEDIUM-7 修复（黑盒报告 r5，2026-08-07）：检测路径中未解析的环境变量残留。
//   IPC 不展开环境变量（调用方应传绝对路径），但畸形输入（尾部 %、双 %%、${）
//   会被 FileSystemIsolator 按字面 create_directories → 在磁盘生成字面量 % 目录
//   （管理员下含 C:\ 根）。这里拒绝含 '%' 或 '${' 的路径，防按字面建目录。
bool ContainsUnresolvedEnv(std::string_view s) {
    return s.find('%') != std::string_view::npos ||
           s.find("${") != std::string_view::npos;
}

} // namespace

// 从 IPC payload 反序列化为 StartProcessRequest
// 字段缺失时使用 default_quota / default_isolation_policy 兜底
// Phase 2 T2.7：新增 isolation_policy 段，覆盖 default_isolation_policy
Result<StartProcessRequest> ParseStartProcessPayload(
    const nlohmann::json& payload,
    const ResourceQuota& default_quota,
    const IsolationPolicy& default_isolation_policy,
    const std::string& request_id) {

    StartProcessRequest req;
    req.request_id = request_id;
    req.quota = default_quota;                        // 先用默认配额兜底
    req.isolation_policy = default_isolation_policy;  // Phase 2 T2.7：默认隔离策略兜底

    const auto& p = payload;

    // command_line（必填）
    // MF-18 修复（黑盒报告，审计绕过风险）：拒绝内嵌 NUL。
    //   CreateProcessW 的命令行是 NUL 结尾的 wchar_t 数组，内嵌 NUL 会静默截断
    //   真实执行的命令行；但 process_started 事件回显完整字符串 → 审计日志记录
    //   的命令行 ≠ 真实执行命令行，可绕过基于事件日志的检测。直接拒绝。
    if (!p.contains("command_line") || !p["command_line"].is_string()) {
        return Result<StartProcessRequest>::Err(
            ErrorCode::IpcSchemaValidationFailed,
            "start_process: missing or invalid 'command_line'");
    }
    req.command_line = p["command_line"].get<std::string>();
    if (req.command_line.find('\0') != std::string::npos) {
        return Result<StartProcessRequest>::Err(
            ErrorCode::IpcSchemaValidationFailed,
            "start_process: 'command_line' must not contain NUL characters");
    }

    // working_dir（可选）
    if (p.contains("working_dir") && p["working_dir"].is_string()) {
        req.working_dir = p["working_dir"].get<std::string>();
    }

    // env_vars（可选）
    if (p.contains("env_vars") && p["env_vars"].is_object()) {
        for (auto it = p["env_vars"].begin(); it != p["env_vars"].end(); ++it) {
            if (it.value().is_string()) {
                req.env_vars.emplace_back(it.key(), it.value().get<std::string>());
            }
        }
    }

    // inherit_env（可选，默认 true）
    if (p.contains("inherit_env") && p["inherit_env"].is_boolean()) {
        req.inherit_env = p["inherit_env"].get<bool>();
    }

    // Phase 3：interactive（可选，默认 false）
    // true → 保留 stdin_write，可后续 WriteStdin 命令写入（REPL/长跑场景）
    // false → Execute 后立即关闭 stdin_write（Phase 1 行为）
    if (p.contains("interactive") && p["interactive"].is_boolean()) {
        req.interactive = p["interactive"].get<bool>();
    }

    // Phase 3：stdin_data（可选，启动时一次性写入 stdin）
    // 仅当 interactive=false 时生效；interactive=true 时忽略
    // 用途：管道式输入（如 echo "1+1" | python），避免额外 WriteStdin 往返
    if (p.contains("stdin_data") && p["stdin_data"].is_string()) {
        req.stdin_data = p["stdin_data"].get<std::string>();
    }

    // Phase 3 T3.8：stream_buffer_size（可选，默认 0 = 用 64KB 默认）
    // >0 时覆盖 StreamReader 的 ReadFile 缓冲大小，用于触发大块 stdout 输出测试消息分片
    if (p.contains("stream_buffer_size") && p["stream_buffer_size"].is_number()) {
        auto sbs = p["stream_buffer_size"].get<int64_t>();
        if (sbs < 0) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: 'stream_buffer_size' must be >= 0, got {}",
                            sbs));
        }
        req.stream_buffer_size = static_cast<size_t>(sbs);
    }

    // quota（可选，覆盖默认配额的字段）
    // C-4 修复：严格校验，对齐 ConfigLoader 标准。
    //   - 未知字段 → 拒绝（strict mode）
    //   - 类型不符（字符串/浮点/布尔）→ 拒绝（不再静默跳过）
    //   - 负值/超范围 → 拒绝（不再因 static_cast 回绕成超大值）
    if (p.contains("quota")) {
        if (!p["quota"].is_object()) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: 'quota' must be object, got {}",
                            p["quota"].type_name()));
        }
        const auto& q = p["quota"];
        std::string err;
        if (!CheckNoUnknownQuotaFields(
                q, "quota",
                {"cpu_ms", "cpu_rate_percent", "memory_mb", "job_memory_mb",
                 "max_processes", "wall_clock_timeout_ms", "cpu_timeout_ms",
                 "no_ui", "breakaway_ok", "crash_silent"},
                err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }

        // cpu_ms：> 0（0 无意义，语义与"不限制"冲突，拒绝避免误导）
        std::optional<uint64_t> cpu_ms;
        if (!GetQuotaUInt64(q, "cpu_ms", "quota", cpu_ms, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (cpu_ms.has_value()) {
            if (*cpu_ms == 0) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    "start_process: quota.cpu_ms must be > 0 (got 0)");
            }
            req.quota.cpu_ms = cpu_ms;
        }

        // cpu_rate_percent：1-100
        std::optional<uint32_t> cpu_rate;
        if (!GetQuotaUInt32(q, "cpu_rate_percent", "quota", cpu_rate, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (cpu_rate.has_value()) {
            if (*cpu_rate == 0 || *cpu_rate > 100) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    std::format("start_process: quota.cpu_rate_percent out of range "
                                "[1,100], got {}", *cpu_rate));
            }
            req.quota.cpu_rate_percent = cpu_rate;
        }

        // memory_mb：> 0
        std::optional<uint64_t> memory_mb;
        if (!GetQuotaUInt64(q, "memory_mb", "quota", memory_mb, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (memory_mb.has_value()) {
            if (*memory_mb == 0) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    "start_process: quota.memory_mb must be > 0 (got 0)");
            }
            req.quota.memory_mb = memory_mb;
        }

        // job_memory_mb：> 0
        std::optional<uint64_t> job_memory_mb;
        if (!GetQuotaUInt64(q, "job_memory_mb", "quota", job_memory_mb, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (job_memory_mb.has_value()) {
            if (*job_memory_mb == 0) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    "start_process: quota.job_memory_mb must be > 0 (got 0)");
            }
            req.quota.job_memory_mb = job_memory_mb;
        }

        // max_processes：> 0
        std::optional<uint32_t> max_processes;
        if (!GetQuotaUInt32(q, "max_processes", "quota", max_processes, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (max_processes.has_value()) {
            if (*max_processes == 0) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    "start_process: quota.max_processes must be > 0 (got 0)");
            }
            req.quota.max_processes = max_processes;
        }

        // wall_clock_timeout_ms：> 0（0 立即杀进程，非预期语义，拒绝）
        std::optional<uint64_t> wall_clock;
        if (!GetQuotaUInt64(q, "wall_clock_timeout_ms", "quota", wall_clock, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (wall_clock.has_value()) {
            if (*wall_clock == 0) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    "start_process: quota.wall_clock_timeout_ms must be > 0 (got 0)");
            }
            req.quota.wall_clock_timeout_ms = wall_clock;
        }

        // cpu_timeout_ms：> 0（同 cpu_ms，语义别名）
        std::optional<uint64_t> cpu_timeout;
        if (!GetQuotaUInt64(q, "cpu_timeout_ms", "quota", cpu_timeout, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (cpu_timeout.has_value()) {
            if (*cpu_timeout == 0) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    "start_process: quota.cpu_timeout_ms must be > 0 (got 0)");
            }
            req.quota.cpu_timeout_ms = cpu_timeout;
        }

        // no_ui / breakaway_ok：严格 bool
        if (!GetQuotaBool(q, "no_ui", "quota", req.quota.no_ui, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        if (!GetQuotaBool(q, "breakaway_ok", "quota", req.quota.breakaway_ok, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
        // Phase 8：崩溃静默（严格 bool）
        if (!GetQuotaBool(q, "crash_silent", "quota", req.quota.crash_silent, err)) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: {}", err));
        }
    }

    // isolation_policy（可选，覆盖 default_isolation_policy）
    // Phase 16 schema（AppContainer 移除后收敛，见 Phase-16 文档 4.5）：
    //   "net_policy": "unrestricted" | "allowlist"
    //     （旧值 none/loopback_only/outbound 已删除：无 AppContainer 时纯用户态
    //      无法系统级执行网络限制，显式拒绝而非静默忽略）
    //   "net_allowlist": [{"ip": str, "port": int, "protocol": int}]
    //   "clipboard_isolate": bool（Job UI 限制：剪贴板/全局原子表）
    // 注：IPC 不展开环境变量（调用方应传完整绝对路径）
    if (p.contains("isolation_policy")) {
        const auto& ip = p["isolation_policy"];
        if (!ip.is_object()) {
            return Result<StartProcessRequest>::Err(
                ErrorCode::IpcSchemaValidationFailed,
                std::format("start_process: 'isolation_policy' must be object, got {}",
                            ip.type_name()));
        }

        IsolationPolicy policy;  // 完全覆盖（不与 default 合并，与 quota 段语义一致）

        // CRITICAL-2 修复延续（黑盒报告 r5，2026-08-07）：未知字段检查，
        // 杜绝"配了但没生效"的静默失败（旧字段 fs_mode/capabilities/path_rules/
        // filesystem 已随 Phase 16 删除，传了即报错）。
        {
            static const std::vector<std::string> kIsoPolicyFields = {
                "net_policy", "net_allowlist", "clipboard_isolate"};
            for (auto it = ip.begin(); it != ip.end(); ++it) {
                const std::string& key = it.key();
                if (std::find(kIsoPolicyFields.begin(), kIsoPolicyFields.end(), key)
                    == kIsoPolicyFields.end()) {
                    return Result<StartProcessRequest>::Err(
                        ErrorCode::IpcSchemaValidationFailed,
                        std::format("start_process: isolation_policy.{} unknown field "
                                    "(strict mode). Phase 16 removed fs_mode/capabilities/"
                                    "path_rules/filesystem; use net_policy/net_allowlist/"
                                    "clipboard_isolate", key));
                }
            }
        }

        // net_policy（Phase 16 收敛为 unrestricted | allowlist）
        if (ip.contains("net_policy")) {
            const auto& np = ip["net_policy"];
            if (!np.is_string()) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    std::format("start_process: isolation_policy.net_policy must be string, got {}",
                                np.type_name()));
            }
            const std::string& s = np.get<std::string>();
            if (s == "unrestricted") {
                policy.net_policy = NetworkPolicy::Unrestricted;
            } else if (s == "allowlist") {
                policy.net_policy = NetworkPolicy::Allowlist;
            } else {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    std::format("start_process: isolation_policy.net_policy invalid: {} "
                                "(allowed: unrestricted|allowlist; none/loopback_only/"
                                "outbound were removed in Phase 16 - not enforceable "
                                "without AppContainer)", s));
            }
        }

        // net_allowlist（WFP SOCKS5 白名单规则，仅 net_policy=allowlist 时生效）
        if (ip.contains("net_allowlist")) {
            const auto& al = ip["net_allowlist"];
            if (!al.is_array()) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    std::format("start_process: isolation_policy.net_allowlist must be array, got {}",
                                al.type_name()));
            }
            for (size_t i = 0; i < al.size(); ++i) {
                const auto& rule = al[i];
                if (!rule.is_object()) {
                    return Result<StartProcessRequest>::Err(
                        ErrorCode::IpcSchemaValidationFailed,
                        std::format("start_process: isolation_policy.net_allowlist[{}] must be object", i));
                }
                NetworkRule nr;
                if (rule.contains("ip") && rule["ip"].is_string()) {
                    nr.ip = rule["ip"].get<std::string>();
                }
                if (rule.contains("port") && rule["port"].is_number_unsigned()) {
                    nr.port = static_cast<uint16_t>(rule["port"].get<unsigned>());
                }
                if (rule.contains("protocol") && rule["protocol"].is_number_unsigned()) {
                    nr.protocol = static_cast<uint8_t>(rule["protocol"].get<unsigned>());
                }
                policy.net_allowlist.push_back(std::move(nr));
            }
        }

        // clipboard_isolate（Phase 16：Job UI 限制，剪贴板/全局原子表/系统参数）
        if (ip.contains("clipboard_isolate")) {
            if (!ip["clipboard_isolate"].is_boolean()) {
                return Result<StartProcessRequest>::Err(
                    ErrorCode::IpcSchemaValidationFailed,
                    std::format("start_process: isolation_policy.clipboard_isolate must be bool, got {}",
                                ip["clipboard_isolate"].type_name()));
            }
            policy.clipboard_isolate = ip["clipboard_isolate"].get<bool>();
        }

        req.isolation_policy = std::move(policy);
    }

    return Result<StartProcessRequest>::Ok(std::move(req));
}

} // namespace winsandbox
