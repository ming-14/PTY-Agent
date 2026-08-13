// =============================================================================
// IConfigLoader - 配置加载端口（core 层）
//
// 抽象"从外部源（文件/字符串）加载 SandboxConfig"的能力。
// core 层依赖此接口；adapters/ConfigLoader 提供基于 nlohmann::json 的实现。
//
// 设计要点：
//   - 接口纯虚，不暴露 nlohmann::json 或 windows.h
//   - Load(path) 失败场景通过 Result<T> 返回具体 ErrorCode：
//       ConfigFileNotFound       文件不存在
//       ConfigParseFailed        JSON 语法错误
//       ConfigSchemaValidationFailed  字段缺失/类型错/范围越界
//   - LoadFromJsonString(json) 供测试与内联配置使用（不读文件）
//   - Default() 返回内置默认配置（无文件时使用）
//
// 生命周期：
//   - 单例：进程内启动期加载一次，全局共享
//   - 不支持热重载
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"
#include "core/entities/SandboxConfig.hpp"

#include <string>

namespace winsandbox {

class IConfigLoader {
public:
    virtual ~IConfigLoader() = default;

    // 从文件加载配置
    // path 为空时返回 Default()
    // 失败场景：文件不存在 / JSON 解析失败 / schema 校验失败
    virtual Result<SandboxConfig> Load(const std::string& path) = 0;

    // 从 JSON 字符串加载配置（不读文件）
    // 供测试与内联配置使用
    virtual Result<SandboxConfig> LoadFromJsonString(const std::string& json_text) = 0;

    // 内置默认配置（无文件时使用）
    virtual SandboxConfig Default() = 0;
};

} // namespace winsandbox
