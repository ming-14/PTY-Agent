// =============================================================================
// StartProcessPayloadParser - start_process payload 反序列化器
//
// 让 payload 解析逻辑可独立单元测试。
//
// 职责：
//   - 从 nlohmann::json payload 反序列化为 StartProcessRequest
//   - 缺失字段使用 default_quota / default_isolation_policy 兜底
//   - 严格 schema 校验：类型错/取值非法 → IpcSchemaValidationFailed
//
// 不负责：
//   - 进程启动（NativeSandboxedProcess）
//   - 配置文件加载（ConfigLoader）
//
// 依赖：
//   - core 层实体（StartProcessRequest / IsolationPolicy / ResourceQuota）
//   - nlohmann::json（仅在 .cpp 中暴露，头文件保持纯净）
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"
#include "core/entities/StartProcessRequest.hpp"
#include "core/entities/IsolationPolicy.hpp"
#include "core/entities/ResourceQuota.hpp"

#include <nlohmann/json.hpp>

#include <string>

namespace winsandbox {

// 从 JSON payload 反序列化为 StartProcessRequest
// 字段缺失时使用 default_quota / default_isolation_policy 兜底
// 参数直接接受 nlohmann::json
Result<StartProcessRequest> ParseStartProcessPayload(
    const nlohmann::json& payload,
    const ResourceQuota& default_quota,
    const IsolationPolicy& default_isolation_policy,
    const std::string& request_id = "");

} // namespace winsandbox
