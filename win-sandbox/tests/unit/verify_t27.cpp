// =============================================================================
// T2.7 验证程序：ConfigLoader isolation 段 + payload isolation_policy 解析
//
// Phase 16 重构版：appcontainer/filesystem/network 段删除，收敛为 isolation 段
// （net_policy=unrestricted|allowlist + net_allowlist + clipboard_isolate）。
//
// 测试组：
//   A. ConfigLoader 单元测试（isolation 段）
//   B. StartProcessPayloadParser payload 测试
// =============================================================================

#include "adapters/ConfigLoader.hpp"
#include "adapters/StartProcessPayloadParser.hpp"
#include "core/entities/ErrorCode.hpp"
#include "core/entities/IsolationPolicy.hpp"
#include "infra/logging/Logger.hpp"

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include <format>
#include <string>

using namespace winsandbox;

// ----- 测试框架 -----

static int g_passed = 0;
static int g_failed = 0;

static void Check(bool cond, const std::string& name) {
    if (cond) {
        ++g_passed;
        spdlog::info("[PASS] {}", name);
    } else {
        ++g_failed;
        spdlog::error("[FAIL] {}", name);
    }
}

static nlohmann::json MakePayload(const std::string& payload_json) {
    return nlohmann::json::parse(payload_json);
}

// =============================================================================
// A. ConfigLoader 单元测试（Phase 16 isolation 段）
// =============================================================================

static void TestConfigLoader(ConfigLoader& loader) {
    spdlog::info("==== A. ConfigLoader isolation 段测试 ====");

    // T1: net_policy=unrestricted
    {
        spdlog::info("---- T1: isolation.net_policy=unrestricted ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "unrestricted"}})");
        Check(static_cast<bool>(r), "T1: parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "T1: net_policy == Unrestricted");
        }
    }

    // T2: net_policy=allowlist
    {
        spdlog::info("---- T2: isolation.net_policy=allowlist ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "allowlist"}})");
        Check(static_cast<bool>(r), "T2: parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.net_policy == NetworkPolicy::Allowlist,
                  "T2: net_policy == Allowlist");
        }
    }

    // T3: isolation 缺省 → Unrestricted
    {
        spdlog::info("---- T3: isolation 缺省 ----");
        auto r = loader.LoadFromJsonString(R"({"logging": {"level": "info"}})");
        Check(static_cast<bool>(r), "T3: parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "T3: net_policy == Unrestricted (default)");
        }
    }

    // T4: 旧值 net_policy=none 拒绝（Phase 16 已删除）
    {
        spdlog::info("---- T4: net_policy=none 拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "none"}})");
        Check(!r, "T4: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T4: code");
        }
    }

    // T5: 旧值 net_policy=outbound 拒绝
    {
        spdlog::info("---- T5: net_policy=outbound 拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "outbound"}})");
        Check(!r, "T5: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T5: code");
        }
    }

    // T6: 旧段 appcontainer 拒绝（顶层未知字段）
    {
        spdlog::info("---- T6: appcontainer 段拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"appcontainer": {"enabled": true}})");
        Check(!r, "T6: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T6: code");
        }
    }

    // T7: 旧段 filesystem 拒绝
    {
        spdlog::info("---- T7: filesystem 段拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"filesystem": {"read_paths": ["C:\\Tools"]}})");
        Check(!r, "T7: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T7: code");
        }
    }

    // T8: 旧段 network 拒绝
    {
        spdlog::info("---- T8: network 段拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"network": {"policy": "unrestricted"}})");
        Check(!r, "T8: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T8: code");
        }
    }

    // T9: clipboard_isolate=true
    {
        spdlog::info("---- T9: clipboard_isolate=true ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"clipboard_isolate": true}})");
        Check(static_cast<bool>(r), "T9: parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.clipboard_isolate, "T9: clipboard_isolate");
        }
    }

    // T10: clipboard_isolate 非 bool
    {
        spdlog::info("---- T10: clipboard_isolate 非 bool ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"clipboard_isolate": "yes"}})");
        Check(!r, "T10: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T10: code");
        }
    }

    // T11: isolation 未知字段
    {
        spdlog::info("---- T11: isolation 未知字段 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "unrestricted", "extra": 1}})");
        Check(!r, "T11: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T11: code");
        }
    }

    // T12: net_policy 非字符串
    {
        spdlog::info("---- T12: net_policy 非字符串 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": 42}})");
        Check(!r, "T12: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "T12: code");
        }
    }

    // T13: net_allowlist 解析
    {
        spdlog::info("---- T13: net_allowlist ----");
        auto r = loader.LoadFromJsonString(R"({
            "isolation": {
                "net_policy": "allowlist",
                "net_allowlist": [
                    {"ip": "127.0.0.1", "port": 8080, "protocol": 6},
                    {"ip": "10.0.0.1"}
                ]
            }
        })");
        Check(static_cast<bool>(r), "T13: parses OK");
        if (r) {
            auto& al = r.Value().default_isolation_policy.net_allowlist;
            Check(al.size() == 2, "T13: 2 rules");
            Check(al.size() >= 1 && al[0].ip == "127.0.0.1" && al[0].port == 8080 && al[0].protocol == 6,
                  "T13: rule[0]");
            Check(al.size() >= 2 && al[1].ip == "10.0.0.1", "T13: rule[1]");
        }
    }

    // T14: 空配置 {}
    {
        spdlog::info("---- T14: 空配置 ----");
        auto r = loader.LoadFromJsonString("{}");
        Check(static_cast<bool>(r), "T14: parses OK");
        if (r) {
            auto& p = r.Value().default_isolation_policy;
            Check(p.net_policy == NetworkPolicy::Unrestricted, "T14: net_policy == Unrestricted");
            Check(!p.clipboard_isolate, "T14: clipboard_isolate == false");
        }
    }

    // T15: 完整配置
    {
        spdlog::info("---- T15: 完整配置 ----");
        auto r = loader.LoadFromJsonString(R"({
            "isolation": {
                "net_policy": "allowlist",
                "net_allowlist": [{"ip": "1.2.3.4"}],
                "clipboard_isolate": true
            }
        })");
        Check(static_cast<bool>(r), "T15: parses OK");
        if (r) {
            auto& p = r.Value().default_isolation_policy;
            Check(p.net_policy == NetworkPolicy::Allowlist, "T15: net_policy == Allowlist");
            Check(p.clipboard_isolate, "T15: clipboard_isolate");
            Check(p.net_allowlist.size() == 1 && p.net_allowlist[0].ip == "1.2.3.4",
                  "T15: net_allowlist");
        }
    }
}

// =============================================================================
// B. StartProcessPayloadParser payload 测试（Phase 16 schema）
// =============================================================================

static void TestIpcPayloadParser() {
    spdlog::info("==== B. StartProcessPayloadParser payload 测试 ====");

    IsolationPolicy default_policy;  // 默认：Unrestricted + 无限制
    default_policy.net_policy = NetworkPolicy::Unrestricted;

    ResourceQuota default_quota;
    default_quota.memory_mb = 256;
    default_quota.max_processes = 64;

    // T16: payload net_policy=unrestricted
    {
        spdlog::info("---- T16: net_policy=unrestricted ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd /c echo hi", "isolation_policy": {"net_policy": "unrestricted"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "T16: parses OK");
        if (r) {
            Check(r.Value().isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "T16: net_policy == Unrestricted");
        }
    }

    // T17: payload net_policy=allowlist
    {
        spdlog::info("---- T17: net_policy=allowlist ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd /c echo hi", "isolation_policy": {"net_policy": "allowlist"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "T17: parses OK");
        if (r) {
            Check(r.Value().isolation_policy.net_policy == NetworkPolicy::Allowlist,
                  "T17: net_policy == Allowlist");
        }
    }

    // T18: payload 无 isolation_policy → 兜底
    {
        spdlog::info("---- T18: 兜底 ----");
        auto msg = MakePayload(R"({"command_line": "cmd /c echo hi"})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "T18: parses OK");
        if (r) {
            auto& p = r.Value().isolation_policy;
            Check(p.net_policy == NetworkPolicy::Unrestricted, "T18: net_policy 兜底");
            Check(!p.clipboard_isolate, "T18: clipboard 兜底");
        }
    }

    // T19: 旧值 net_policy=none 拒绝
    {
        spdlog::info("---- T19: net_policy=none 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"net_policy": "none"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T19: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T19: code");
        }
    }

    // T20: 旧字段 fs_mode 拒绝（未知字段）
    {
        spdlog::info("---- T20: fs_mode 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"fs_mode": "default_deny"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T20: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T20: code");
        }
    }

    // T21: 旧字段 capabilities 拒绝
    {
        spdlog::info("---- T21: capabilities 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"capabilities": ["internetClient"]}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T21: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T21: code");
        }
    }

    // T22: 旧字段 path_rules 拒绝
    {
        spdlog::info("---- T22: path_rules 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"path_rules": [{"path": "C:\\X", "access": ["read"]}]}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T22: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T22: code");
        }
    }

    // T23: 旧字段 filesystem 拒绝
    {
        spdlog::info("---- T23: filesystem 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"filesystem": {"mode": "redirect"}}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T23: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T23: code");
        }
    }

    // T24: clipboard_isolate=true 解析
    {
        spdlog::info("---- T24: clipboard_isolate=true ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"clipboard_isolate": true}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "T24: parses OK");
        if (r) {
            Check(r.Value().isolation_policy.clipboard_isolate, "T24: clipboard_isolate");
        }
    }

    // T25: clipboard_isolate 非 bool
    {
        spdlog::info("---- T25: clipboard_isolate 非 bool ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"clipboard_isolate": 1}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T25: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T25: code");
        }
    }

    // T26: isolation_policy 非对象
    {
        spdlog::info("---- T26: isolation_policy 非对象 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": "not_an_object"})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T26: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T26: code");
        }
    }

    // T27: 缺 command_line
    {
        spdlog::info("---- T27: 缺 command_line ----");
        auto msg = MakePayload(R"({"working_dir": "C:\\"})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "T27: rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "T27: code");
        }
    }

    // T28: net_allowlist 解析
    {
        spdlog::info("---- T28: net_allowlist ----");
        auto msg = MakePayload(R"({
            "command_line": "cmd",
            "isolation_policy": {
                "net_policy": "allowlist",
                "net_allowlist": [{"ip": "192.168.1.1", "port": 443, "protocol": 6}]
            }
        })");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "T28: parses OK");
        if (r) {
            auto& al = r.Value().isolation_policy.net_allowlist;
            Check(al.size() == 1 && al[0].ip == "192.168.1.1" && al[0].port == 443,
                  "T28: net_allowlist");
        }
    }

    // T29: 完整 payload
    {
        spdlog::info("---- T29: 完整 payload ----");
        auto msg = MakePayload(R"({
            "command_line": "cmd /c type C:\\secret.txt",
            "working_dir": "C:\\Temp",
            "inherit_env": false,
            "quota": {"memory_mb": 512, "max_processes": 32},
            "isolation_policy": {
                "net_policy": "unrestricted",
                "clipboard_isolate": true
            }
        })");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "T29: parses OK");
        if (r) {
            auto& req = r.Value();
            Check(req.command_line == "cmd /c type C:\\secret.txt", "T29: command_line");
            Check(req.working_dir == "C:\\Temp", "T29: working_dir");
            Check(!req.inherit_env, "T29: inherit_env=false");
            Check(req.quota.memory_mb.value() == 512, "T29: memory_mb=512");
            Check(req.quota.max_processes.value() == 32, "T29: max_processes=32");
            Check(req.isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "T29: net_policy=Unrestricted");
            Check(req.isolation_policy.clipboard_isolate, "T29: clipboard_isolate");
        }
    }
}

// =============================================================================
// 主函数
// =============================================================================

static int RunTests() {
    auto logger = Logger::Init("info");

    ConfigLoader loader(logger);
    TestConfigLoader(loader);
    TestIpcPayloadParser();

    spdlog::info("==== Summary: {} passed, {} failed ====", g_passed, g_failed);
    Logger::Shutdown();
    return g_failed == 0 ? 0 : 1;
}

int main() {
    try {
        return RunTests();
    } catch (const std::exception& e) {
        spdlog::error("exception: {}", e.what());
        return 2;
    }
}