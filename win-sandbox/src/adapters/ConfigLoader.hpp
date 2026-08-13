// =============================================================================
// ConfigLoader - 基于 nlohmann::json 的配置加载器（adapters 层）
//
// 实现 IConfigLoader 端口，把 JSON 文件/字符串解析为 SandboxConfig 领域对象。
//
// 设计要点：
//   1. schema 校验：手工实现（不引入 json-schema 库）
//      - 字段少，手工校验更可控且无新依赖
//      - 用 helper 函数（Require* / Optional*）让校验代码读起来像声明式
//      - 严格模式：未知字段拒绝（防止配置漂移）
//   2. 环境变量展开：%LOCALAPPDATA% 等通过 ExpandEnvironmentStringsW 展开
//      仅对路径类字符串字段展开（logging.dir）
//   3. 范围校验：
//      - memory_mb > 0
//      - cpu_rate_percent ∈ [1, 100]
//      - max_processes > 0
//      - retention_days 不做下限校验（0 = 永久保留，合法）
//   4. 错误信息：尽可能附带字段路径与实际值，便于运维定位
//
// 文件读取：
//   - 用 std::ifstream（跨平台，避免 windows.h 依赖）
//   - UTF-8 文件 assumed（nlohmann::json 原生支持）
//
// 线程安全：
//   - 无共享状态，Load 可并发调用（但通常启动期单线程加载一次）
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"
#include "core/entities/SandboxConfig.hpp"
#include "core/ports/IConfigLoader.hpp"
#include "core/ports/ILogger.hpp"

#include <memory>

namespace winsandbox {

class ConfigLoader : public IConfigLoader {
public:
    explicit ConfigLoader(std::shared_ptr<ILogger> logger);
    ~ConfigLoader() override = default;

    ConfigLoader(const ConfigLoader&) = delete;
    ConfigLoader& operator=(const ConfigLoader&) = delete;
    ConfigLoader(ConfigLoader&&) = delete;
    ConfigLoader& operator=(ConfigLoader&&) = delete;

    // ----- IConfigLoader 实现 -----
    Result<SandboxConfig> Load(const std::string& path) override;
    Result<SandboxConfig> LoadFromJsonString(const std::string& json_text) override;
    SandboxConfig Default() override;

private:
    // 解析 + 校验 + 转换 nlohmann::json → SandboxConfig
    // 失败场景：JSON 语法错误 / schema 校验失败
    Result<SandboxConfig> ParseAndValidate(const std::string& json_text, const std::string& source_desc);

    // 展开字符串中的环境变量（%VAR% 形式）
    // 用 ExpandEnvironmentStringsW 实现
    static std::string ExpandEnv(const std::string& s);

    // 展开环境变量（严格版）：展开后仍含 '%'（未定义/未闭合/畸形变量按字面
    // 残留）→ 返回 false + err。用于路径字段，防止畸形变量被按字面创建为
    // 磁盘目录。
    static bool ExpandEnvStrict(const std::string& s, std::string& out,
                                std::string& err);

    std::shared_ptr<ILogger> logger_;
};

} // namespace winsandbox
