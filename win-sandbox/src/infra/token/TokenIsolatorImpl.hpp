// =============================================================================
// TokenIsolatorImpl - 隔离 token 派生实现（infra 层）
//
// 从当前进程 token 派生 Low IL 隔离 token，链路：
//   OpenProcessToken(TOKEN_DUPLICATE|TOKEN_ASSIGN_PRIMARY|TOKEN_ADJUST_DEFAULT|TOKEN_QUERY)
//   → DuplicateTokenEx(MAXIMUM_ALLOWED, SecurityImpersonation, TokenPrimary)
//   → SetTokenInformation(TokenIntegrityLevel, S-1-16-4096)  // Low，全盘禁写
//
// 设计决策（项目使用 plain+LOW 单路径）：
//   - 不使用 CreateRestrictedToken(DISABLE_MAX_PRIVILEGE)：其产物是 restricted
//     token，非管理员宿主 CreateProcessAsUserW 启动必然 err=1314
//     （MSDN：restricted token 需宿主 SE_ASSIGNPRIMARYTOKEN_NAME；实测
//     CreateProcessWithTokenW / NtCreateUserProcess 同样失败）
//   - 特权集 = 宿主镜像（复制状态，不新增）：非管理员场景仅 5 个无害特权
//     （SeChangeNotify 启用 + Shutdown/Undock/WorkingSet/TimeZone 禁用）；
//     威胁模型边界 = "沙箱进程能力 = 同用户宿主能力全集"（与 HKCU 可写、
//     同用户 kill 同级）
//   - 隔离核心全部来自完整性强制：IL=Low 对默认 Medium 对象强制 NO_WRITE_UP
//     （token 侧默认策略），读/执行不受限（无 NO_READ_UP，用户 SID 保留）
//
// 生命周期：GetToken() 返回的 HANDLE 由本实现持有，Close() 释放；调用方
// （ProcessLauncherImpl）仅用于 CreateProcessAsUserW。
// =============================================================================
#pragma once

#include "core/ports/ILogger.hpp"
#include "core/ports/ITokenIsolator.hpp"

#include <memory>

namespace winsandbox {

class TokenIsolatorImpl : public ITokenIsolator {
public:
    explicit TokenIsolatorImpl(std::shared_ptr<ILogger> logger);
    ~TokenIsolatorImpl() override;

    // 派生隔离 primary token（幂等：已派生直接 Ok）
    Result<void> Prepare() override;

    // 隔离 token 句柄（HANDLE 以 void* 传递，实现层拥有）
    void* GetToken() const override;

    // 释放 token 句柄（幂等）
    void Close() override;

private:
    void* token_ = nullptr;  // HANDLE（DuplicateTokenEx 产物，IL=Low）
    std::shared_ptr<ILogger> logger_;
};

} // namespace winsandbox