// =============================================================================
// ITokenIsolator - 隔离 token 派生端口（core 层）
//
// 从当前进程 token 派生隔离 primary token，链路：
//   OpenProcessToken → DuplicateTokenEx(primary)
//   → SetTokenInformation(TokenIntegrityLevel, Low)  // IL=4096，全盘禁写
//
// 设计决策（项目使用 plain 单路径）：特权集=宿主镜像，不 CreateRestrictedToken
// （restricted token 非管理员 CreateProcessAsUserW 启动必 err=1314）
//
// 句柄约定（干净架构折中）：
//   - GetToken() 返回 void* = HANDLE（实现层拥有，调用方不可 CloseHandle）
//   - 调用方（ProcessLauncherImpl）用 GetToken() 调 CreateProcessAsUserW
//   - Close() 释放句柄（usecase 清理时调用）
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"

namespace winsandbox {

class ITokenIsolator {
public:
    virtual ~ITokenIsolator() = default;

    // 派生隔离 primary token（幂等：重复调用返回 Ok 不重复派生）
    virtual Result<void> Prepare() = 0;

    // 隔离 token 句柄（void* 形式，实现层拥有）
    virtual void* GetToken() const = 0;

    // 释放 token 句柄（幂等）
    virtual void Close() = 0;
};

} // namespace winsandbox
