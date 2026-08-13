// =============================================================================
// 验证程序：ConfigLoader isolation 段 + payload isolation_policy 解析
//
// 配置收敛为 isolation 段
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
// A. ConfigLoader 单元测试（isolation 段）
// =============================================================================

static void TestConfigLoader(ConfigLoader& loader) {
    spdlog::info("==== A. ConfigLoader isolation 段测试 ====");

    // net_policy=unrestricted
    {
        spdlog::info("---- isolation.net_policy=unrestricted ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "unrestricted"}})");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "net_policy == Unrestricted");
        }
    }

    // net_policy=allowlist
    {
        spdlog::info("---- isolation.net_policy=allowlist ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "allowlist"}})");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.net_policy == NetworkPolicy::Allowlist,
                  "net_policy == Allowlist");
        }
    }

    // isolation 缺省 → Unrestricted
    {
        spdlog::info("---- isolation 缺省 ----");
        auto r = loader.LoadFromJsonString(R"({"logging": {"level": "info"}})");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "net_policy == Unrestricted (default)");
        }
    }

    // 非法值 net_policy=none 拒绝
    {
        spdlog::info("---- net_policy=none 拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "none"}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // 非法值 net_policy=outbound 拒绝
    {
        spdlog::info("---- net_policy=outbound 拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "outbound"}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // 未知段 appcontainer 拒绝（顶层未知字段）
    {
        spdlog::info("---- appcontainer 段拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"appcontainer": {"enabled": true}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // 未知段 filesystem 拒绝
    {
        spdlog::info("---- filesystem 段拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"filesystem": {"read_paths": ["C:\\Tools"]}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // 未知段 network 拒绝
    {
        spdlog::info("---- network 段拒绝 ----");
        auto r = loader.LoadFromJsonString(R"({"network": {"policy": "unrestricted"}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // clipboard_isolate=true
    {
        spdlog::info("---- clipboard_isolate=true ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"clipboard_isolate": true}})");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().default_isolation_policy.clipboard_isolate, "clipboard_isolate");
        }
    }

    // clipboard_isolate 非 bool
    {
        spdlog::info("---- clipboard_isolate 非 bool ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"clipboard_isolate": "yes"}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // isolation 未知字段
    {
        spdlog::info("---- isolation 未知字段 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": "unrestricted", "extra": 1}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // net_policy 非字符串
    {
        spdlog::info("---- net_policy 非字符串 ----");
        auto r = loader.LoadFromJsonString(R"({"isolation": {"net_policy": 42}})");
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::ConfigSchemaValidationFailed, "code");
        }
    }

    // net_allowlist 解析
    {
        spdlog::info("---- net_allowlist ----");
        auto r = loader.LoadFromJsonString(R"({
            "isolation": {
                "net_policy": "allowlist",
                "net_allowlist": [
                    {"ip": "127.0.0.1", "port": 8080, "protocol": 6},
                    {"ip": "10.0.0.1"}
                ]
            }
        })");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            auto& al = r.Value().default_isolation_policy.net_allowlist;
            Check(al.size() == 2, "2 rules");
            Check(al.size() >= 1 && al[0].ip == "127.0.0.1" && al[0].port == 8080 && al[0].protocol == 6,
                  "rule[0]");
            Check(al.size() >= 2 && al[1].ip == "10.0.0.1", "rule[1]");
        }
    }

    // 空配置 {}
    {
        spdlog::info("---- 空配置 ----");
        auto r = loader.LoadFromJsonString("{}");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            auto& p = r.Value().default_isolation_policy;
            Check(p.net_policy == NetworkPolicy::Unrestricted, "net_policy == Unrestricted");
            Check(!p.clipboard_isolate, "clipboard_isolate == false");
        }
    }

    // 完整配置
    {
        spdlog::info("---- 完整配置 ----");
        auto r = loader.LoadFromJsonString(R"({
            "isolation": {
                "net_policy": "allowlist",
                "net_allowlist": [{"ip": "1.2.3.4"}],
                "clipboard_isolate": true
            }
        })");
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            auto& p = r.Value().default_isolation_policy;
            Check(p.net_policy == NetworkPolicy::Allowlist, "net_policy == Allowlist");
            Check(p.clipboard_isolate, "clipboard_isolate");
            Check(p.net_allowlist.size() == 1 && p.net_allowlist[0].ip == "1.2.3.4",
                  "net_allowlist");
        }
    }
}

// =============================================================================
// B. StartProcessPayloadParser payload schema 测试
// =============================================================================

static void TestIpcPayloadParser() {
    spdlog::info("==== B. StartProcessPayloadParser payload 测试 ====");

    IsolationPolicy default_policy;  // 默认：Unrestricted + 无限制
    default_policy.net_policy = NetworkPolicy::Unrestricted;

    ResourceQuota default_quota;
    default_quota.memory_mb = 256;
    default_quota.max_processes = 64;

    // payload net_policy=unrestricted
    {
        spdlog::info("---- net_policy=unrestricted ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd /c echo hi", "isolation_policy": {"net_policy": "unrestricted"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "net_policy == Unrestricted");
        }
    }

    // payload net_policy=allowlist
    {
        spdlog::info("---- net_policy=allowlist ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd /c echo hi", "isolation_policy": {"net_policy": "allowlist"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().isolation_policy.net_policy == NetworkPolicy::Allowlist,
                  "net_policy == Allowlist");
        }
    }

    // payload 无 isolation_policy → 兜底
    {
        spdlog::info("---- 兜底 ----");
        auto msg = MakePayload(R"({"command_line": "cmd /c echo hi"})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            auto& p = r.Value().isolation_policy;
            Check(p.net_policy == NetworkPolicy::Unrestricted, "net_policy 兜底");
            Check(!p.clipboard_isolate, "clipboard 兜底");
        }
    }

    // 非法值 net_policy=none 拒绝
    {
        spdlog::info("---- net_policy=none 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"net_policy": "none"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // 非法字段 fs_mode 拒绝（未知字段）
    {
        spdlog::info("---- fs_mode 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"fs_mode": "default_deny"}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // 非法字段 capabilities 拒绝
    {
        spdlog::info("---- capabilities 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"capabilities": ["internetClient"]}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // 非法字段 path_rules 拒绝
    {
        spdlog::info("---- path_rules 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"path_rules": [{"path": "C:\\X", "access": ["read"]}]}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // 非法字段 filesystem 拒绝
    {
        spdlog::info("---- filesystem 拒绝 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"filesystem": {"mode": "redirect"}}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // clipboard_isolate=true 解析
    {
        spdlog::info("---- clipboard_isolate=true ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"clipboard_isolate": true}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            Check(r.Value().isolation_policy.clipboard_isolate, "clipboard_isolate");
        }
    }

    // clipboard_isolate 非 bool
    {
        spdlog::info("---- clipboard_isolate 非 bool ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": {"clipboard_isolate": 1}})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // isolation_policy 非对象
    {
        spdlog::info("---- isolation_policy 非对象 ----");
        auto msg = MakePayload(
            R"({"command_line": "cmd", "isolation_policy": "not_an_object"})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // 缺 command_line
    {
        spdlog::info("---- 缺 command_line ----");
        auto msg = MakePayload(R"({"working_dir": "C:\\"})");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(!r, "rejected");
        if (!r) {
            Check(r.Code() == ErrorCode::IpcSchemaValidationFailed, "code");
        }
    }

    // net_allowlist 解析
    {
        spdlog::info("---- net_allowlist ----");
        auto msg = MakePayload(R"({
            "command_line": "cmd",
            "isolation_policy": {
                "net_policy": "allowlist",
                "net_allowlist": [{"ip": "192.168.1.1", "port": 443, "protocol": 6}]
            }
        })");
        auto r = ParseStartProcessPayload(msg, default_quota, default_policy);
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            auto& al = r.Value().isolation_policy.net_allowlist;
            Check(al.size() == 1 && al[0].ip == "192.168.1.1" && al[0].port == 443,
                  "net_allowlist");
        }
    }

    // 完整 payload
    {
        spdlog::info("---- 完整 payload ----");
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
        Check(static_cast<bool>(r), "parses OK");
        if (r) {
            auto& req = r.Value();
            Check(req.command_line == "cmd /c type C:\\secret.txt", "command_line");
            Check(req.working_dir == "C:\\Temp", "working_dir");
            Check(!req.inherit_env, "inherit_env=false");
            Check(req.quota.memory_mb.value() == 512, "memory_mb=512");
            Check(req.quota.max_processes.value() == 32, "max_processes=32");
            Check(req.isolation_policy.net_policy == NetworkPolicy::Unrestricted,
                  "net_policy=Unrestricted");
            Check(req.isolation_policy.clipboard_isolate, "clipboard_isolate");
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