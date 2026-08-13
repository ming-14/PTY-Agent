// =============================================================================
// ConfigLoader 运行时验证
//
// 验证 JSON 解析、schema 校验、环境变量展开、错误码。
//
// 测试场景：
//   1. 合法完整配置 → 解析成功，字段值与预期一致
//   2. 部分字段（仅 logging）→ 缺失字段用 BuildDefault 兜底
//   3. 空配置 {} → 全部用默认值
//   4. JSON 语法错误 → ConfigParseFailed
//   5. 顶层非 object → ConfigSchemaValidationFailed
//   6. 未知字段 → ConfigSchemaValidationFailed
//   7. memory_mb=0 → ConfigSchemaValidationFailed（必须 > 0）
//   8. memory_mb 负数 → ConfigSchemaValidationFailed
//   9. cpu_rate_percent=150 → ConfigSchemaValidationFailed（范围 1-100）
//  10. logging.level="verbose" → ConfigSchemaValidationFailed（取值非法）
//  11. logging.dir 含 %TEMP% → 展开为实际路径
//  12. no_ui=true（bool）→ 解析成功
//  13. Load(不存在文件) → ConfigFileNotFound
//  14. Load("") → 返回 Default
// =============================================================================

#include "adapters/ConfigLoader.hpp"
#include "core/entities/ErrorCode.hpp"
#include "infra/logging/Logger.hpp"

#include <spdlog/spdlog.h>

#include <windows.h>

#include <cstdio>
#include <filesystem>
#include <format>
#include <fstream>
#include <string>

using namespace winsandbox;

static int RunTests() {
    auto logger = Logger::Init("info");

    int passed = 0;
    int failed = 0;

    auto check = [&](bool cond, const std::string& name) {
        if (cond) {
            ++passed;
            spdlog::info("[PASS] {}", name);
        } else {
            ++failed;
            spdlog::error("[FAIL] {}", name);
        }
    };

    ConfigLoader loader(logger);

    // ----- 测试 1：合法完整配置 -----
    {
        spdlog::info("---- Test 1: full valid config ----");
        std::string json = R"({
            "logging": {"level": "debug", "dir": "C:\\logs", "retention_days": 14},
            "default_quota": {
                "cpu_ms": 10000,
                "cpu_rate_percent": 50,
                "memory_mb": 512,
                "max_processes": 128,
                "wall_clock_timeout_ms": 30000,
                "no_ui": true,
                "breakaway_ok": false
            }
        })";
        auto r = loader.LoadFromJsonString(json);
        check(static_cast<bool>(r), "full config parses OK");
        if (r) {
            auto& cfg = r.Value();
            check(cfg.logging.level == "debug", "logging.level == debug");
            check(cfg.logging.dir == "C:\\logs", "logging.dir == C:\\logs");
            check(cfg.logging.retention_days == 14, "retention_days == 14");
            check(cfg.default_quota.cpu_ms.value() == 10000, "cpu_ms == 10000");
            check(cfg.default_quota.cpu_rate_percent.value() == 50, "cpu_rate_percent == 50");
            check(cfg.default_quota.memory_mb.value() == 512, "memory_mb == 512");
            check(cfg.default_quota.max_processes.value() == 128, "max_processes == 128");
            check(cfg.default_quota.wall_clock_timeout_ms.value() == 30000,
                  "wall_clock_timeout_ms == 30000");
            check(cfg.default_quota.no_ui == true, "no_ui == true");
            check(cfg.default_quota.breakaway_ok == false, "breakaway_ok == false");
        }
    }

    // ----- 测试 2：部分字段（仅 logging）-----
    {
        spdlog::info("---- Test 2: partial config (logging only) ----");
        std::string json = R"({"logging": {"level": "warn"}})";
        auto r = loader.LoadFromJsonString(json);
        check(static_cast<bool>(r), "partial config parses OK");
        if (r) {
            auto& cfg = r.Value();
            check(cfg.logging.level == "warn", "logging.level == warn");
            // 缺失字段用默认
            check(cfg.logging.retention_days == 7, "retention_days default == 7");
            check(cfg.default_quota.memory_mb.value() == 256,
                  "default_quota.memory_mb default == 256");
        }
    }

    // ----- 测试 3：空配置 {} -----
    {
        spdlog::info("---- Test 3: empty config {} ----");
        auto r = loader.LoadFromJsonString("{}");
        check(static_cast<bool>(r), "empty config parses OK");
        if (r) {
            auto& cfg = r.Value();
            auto def = SandboxConfig::BuildDefault();
            check(cfg.logging.level == def.logging.level, "level == default");
            check(cfg.default_quota.memory_mb == def.default_quota.memory_mb,
                  "memory_mb == default");
        }
    }

    // ----- 测试 4：JSON 语法错误 -----
    {
        spdlog::info("---- Test 4: invalid JSON syntax ----");
        auto r = loader.LoadFromJsonString("{not valid json");
        check(!r && r.Code() == ErrorCode::ConfigParseFailed,
              std::format("invalid JSON → ConfigParseFailed (code={}, msg={})",
                          static_cast<int>(r.Code()), r.Message()));
    }

    // ----- 测试 5：顶层非 object -----
    {
        spdlog::info("---- Test 5: root not object ----");
        auto r = loader.LoadFromJsonString("[1,2,3]");
        check(!r && r.Code() == ErrorCode::ConfigSchemaValidationFailed,
              std::format("root array → ConfigSchemaValidationFailed (code={})",
                          static_cast<int>(r.Code())));
    }

    // ----- 测试 6：未知字段 -----
    {
        spdlog::info("---- Test 6: unknown field ----");
        auto r = loader.LoadFromJsonString(R"({"unknown_field": 1})");
        check(!r && r.Code() == ErrorCode::ConfigSchemaValidationFailed,
              std::format("unknown field → ConfigSchemaValidationFailed (code={}, msg={})",
                          static_cast<int>(r.Code()), r.Message()));
    }

    // ----- 测试 7：memory_mb = 0 -----
    {
        spdlog::info("---- Test 7: memory_mb = 0 ----");
        std::string json = R"({"default_quota": {"memory_mb": 0}})";
        auto r = loader.LoadFromJsonString(json);
        check(!r && r.Code() == ErrorCode::ConfigSchemaValidationFailed,
              std::format("memory_mb=0 → ConfigSchemaValidationFailed (msg={})",
                          r.Message()));
    }

    // ----- 测试 8：memory_mb 负数 -----
    {
        spdlog::info("---- Test 8: memory_mb = -100 ----");
        std::string json = R"({"default_quota": {"memory_mb": -100}})";
        auto r = loader.LoadFromJsonString(json);
        check(!r && r.Code() == ErrorCode::ConfigSchemaValidationFailed,
              std::format("memory_mb=-100 → ConfigSchemaValidationFailed (msg={})",
                          r.Message()));
    }

    // ----- 测试 9：cpu_rate_percent = 150 -----
    {
        spdlog::info("---- Test 9: cpu_rate_percent = 150 ----");
        std::string json = R"({"default_quota": {"cpu_rate_percent": 150}})";
        auto r = loader.LoadFromJsonString(json);
        check(!r && r.Code() == ErrorCode::ConfigSchemaValidationFailed,
              std::format("cpu_rate_percent=150 → ConfigSchemaValidationFailed (msg={})",
                          r.Message()));
    }

    // ----- 测试 10：logging.level 非法 -----
    {
        spdlog::info("---- Test 10: logging.level = verbose ----");
        std::string json = R"({"logging": {"level": "verbose"}})";
        auto r = loader.LoadFromJsonString(json);
        check(!r && r.Code() == ErrorCode::ConfigSchemaValidationFailed,
              std::format("level=verbose → ConfigSchemaValidationFailed (msg={})",
                          r.Message()));
    }

    // ----- 测试 11：%TEMP% 环境变量展开 -----
    {
        spdlog::info("---- Test 11: %TEMP% expansion ----");
        std::string json = R"({"logging": {"dir": "%TEMP%\\win-sandbox"}})";
        auto r = loader.LoadFromJsonString(json);
        check(static_cast<bool>(r), "config with %TEMP% parses OK");
        if (r) {
            auto& cfg = r.Value();
            // %TEMP% 应该被展开，不再包含 %
            check(cfg.logging.dir.find('%') == std::string::npos,
                  std::format("dir has no '%' after expansion (actual={})",
                              cfg.logging.dir));
            check(cfg.logging.dir.find("win-sandbox") != std::string::npos,
                  std::format("dir contains 'win-sandbox' (actual={})",
                              cfg.logging.dir));
            spdlog::info("  expanded dir={}", cfg.logging.dir);
        }
    }

    // ----- 测试 12：no_ui = true（bool）-----
    {
        spdlog::info("---- Test 12: no_ui = true ----");
        std::string json = R"({"default_quota": {"no_ui": true}})";
        auto r = loader.LoadFromJsonString(json);
        check(static_cast<bool>(r), "no_ui=true parses OK");
        if (r) {
            check(r.Value().default_quota.no_ui == true, "no_ui == true");
        }
    }

    // ----- 测试 14：Load 不存在文件 -----
    {
        spdlog::info("---- Test 13: Load non-existent file ----");
        auto r = loader.Load("Z:\\non_existent_path_12345\\config.json");
        check(!r && r.Code() == ErrorCode::ConfigFileNotFound,
              std::format("non-existent file → ConfigFileNotFound (code={}, msg={})",
                          static_cast<int>(r.Code()), r.Message()));
    }

    // ----- 测试 15：Load("") → Default -----
    {
        spdlog::info("---- Test 14: Load(\"\") → Default ----");
        auto r = loader.Load("");
        check(static_cast<bool>(r), "Load(\"\") returns Ok");
        if (r) {
            auto def = SandboxConfig::BuildDefault();
            check(r.Value().logging.level == def.logging.level,
                  "Load(\"\") == BuildDefault");
        }
    }

    // ----- 测试 16：Load 实际文件（写临时文件）-----
    {
        spdlog::info("---- Test 15: Load actual file ----");
        // 用 %TEMP% 写临时配置文件
        char temp_dir[MAX_PATH] = {0};
        ::GetTempPathA(MAX_PATH, temp_dir);
        std::string temp_path = std::string(temp_dir) + "win_sandbox_test_config.json";

        std::string json = R"({"logging": {"level": "trace"}, "default_quota": {"memory_mb": 1024}})";
        std::ofstream ofs(temp_path, std::ios::binary);
        ofs << json;
        ofs.close();

        auto r = loader.Load(temp_path);
        check(static_cast<bool>(r),
              std::format("Load actual file OK (path={})", temp_path));
        if (r) {
            check(r.Value().logging.level == "trace", "level == trace");
            check(r.Value().default_quota.memory_mb.value() == 1024,
                  "memory_mb == 1024");
        }

        // 清理
        std::error_code ec;
        std::filesystem::remove(temp_path, ec);
    }

    spdlog::info("==== Summary: {} passed, {} failed ====", passed, failed);
    Logger::Shutdown();
    return failed == 0 ? 0 : 1;
}

int main() {
    try {
        return RunTests();
    } catch (const std::exception& e) {
        spdlog::error("exception: {}", e.what());
        return 2;
    }
}
