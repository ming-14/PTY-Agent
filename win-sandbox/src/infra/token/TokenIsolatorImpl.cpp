// =============================================================================
// TokenIsolatorImpl 实现
//
// 与实验等价的关键参数：
//   - DuplicateTokenEx 请求 MAXIMUM_ALLOWED（实测 0x1FF 亦可），SecurityImpersonation
//     级别只是中间步骤要求，TokenPrimary 输出为 primary token
//   - TokenIntegrityLevel(25) + S-1-16-4096（S-1-16-<LOW_IL>），
//     Attributes = SE_GROUP_INTEGRITY|SE_GROUP_INTEGRITY_ENABLED
//   - 特权不清除（plain 单路径决策，见头文件注释）
//
// 错误映射：任一步失败 → ErrorCode::TokenIsolatorFailed（message 带步骤与 GetLastError）
// =============================================================================

#include "infra/token/TokenIsolatorImpl.hpp"

#include <windows.h>

#include <sddl.h>  // ConvertStringSidToSidW

#include <format>

#pragma comment(lib, "advapi32.lib")

namespace winsandbox {

namespace {

// 隔离 token 派生所需访问权限
constexpr DWORD kTokenAccess = TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT | TOKEN_QUERY;

} // namespace

TokenIsolatorImpl::TokenIsolatorImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger)) {
    logger_->Log(LogLevel::Debug, "TokenIsolatorImpl created");
}

TokenIsolatorImpl::~TokenIsolatorImpl() {
    Close();
}

Result<void> TokenIsolatorImpl::Prepare() {
    // 幂等：已派生直接成功
    if (token_ != nullptr) {
        logger_->Log(LogLevel::Debug, "TokenIsolatorImpl::Prepare: already prepared, skip");
        return Result<void>::Ok();
    }

    HANDLE current = nullptr;
    if (!::OpenProcessToken(::GetCurrentProcess(), kTokenAccess, &current)) {
        const DWORD err = ::GetLastError();
        logger_->Log(LogLevel::Error,
                     std::format("TokenIsolatorImpl::Prepare: OpenProcessToken failed err={}", err));
        return Result<void>::Err(ErrorCode::TokenIsolatorFailed,
                                 "OpenProcessToken failed err=" + std::to_string(err));
    }

    // 1) 复制为 primary token（保留用户 SID/组与特权集，非 restricted——决策见头文件）
    HANDLE duplicated = nullptr;
    const BOOL dup_ok = ::DuplicateTokenEx(current, MAXIMUM_ALLOWED, nullptr,
                                           SecurityImpersonation, TokenPrimary, &duplicated);
    ::CloseHandle(current);
    if (!dup_ok) {
        const DWORD err = ::GetLastError();
        logger_->Log(LogLevel::Error,
                     std::format("TokenIsolatorImpl::Prepare: DuplicateTokenEx failed err={}", err));
        return Result<void>::Err(ErrorCode::TokenIsolatorFailed,
                                 "DuplicateTokenEx failed err=" + std::to_string(err));
    }

    // 2) 完整性级别降为 Low（S-1-16-4096）：全盘禁写的强制来源
    PSID low_sid = nullptr;
    if (!::ConvertStringSidToSidW(L"S-1-16-4096", &low_sid)) {
        const DWORD err = ::GetLastError();
        ::CloseHandle(duplicated);
        logger_->Log(LogLevel::Error,
                     std::format("TokenIsolatorImpl::Prepare: ConvertStringSidToSidW failed err={}", err));
        return Result<void>::Err(ErrorCode::TokenIsolatorFailed,
                                 "ConvertStringSidToSidW failed err=" + std::to_string(err));
    }

    TOKEN_MANDATORY_LABEL label{};
    label.Label.Sid = low_sid;
    label.Label.Attributes = SE_GROUP_INTEGRITY | SE_GROUP_INTEGRITY_ENABLED;
    const BOOL il_ok = ::SetTokenInformation(duplicated, TokenIntegrityLevel,
                                             &label, sizeof(label));
    ::LocalFree(low_sid);
    if (!il_ok) {
        const DWORD err = ::GetLastError();
        ::CloseHandle(duplicated);
        logger_->Log(LogLevel::Error,
                     std::format("TokenIsolatorImpl::Prepare: SetTokenInformation(IL) failed err={}", err));
        return Result<void>::Err(ErrorCode::TokenIsolatorFailed,
                                 "SetTokenInformation(IL) failed err=" + std::to_string(err));
    }

    token_ = duplicated;
    logger_->Log(LogLevel::Info,
                 "TokenIsolatorImpl::Prepare: isolated token ready (IL=S-1-16-4096)");
    return Result<void>::Ok();
}

void* TokenIsolatorImpl::GetToken() const {
    return token_;
}

void TokenIsolatorImpl::Close() {
    if (token_ != nullptr) {
        ::CloseHandle(static_cast<HANDLE>(token_));
        token_ = nullptr;
        logger_->Log(LogLevel::Debug, "TokenIsolatorImpl::Close: token closed");
    }
}

} // namespace winsandbox