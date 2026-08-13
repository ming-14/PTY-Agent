// =============================================================================
// WriteAreaImpl 实现
//
// 手写 SYSTEM_MANDATORY_LABEL_ACE 布局（SDK 无 AddMandatoryAce 声明，
// 已验证此字节布局 + SetNamedSecurityInfo 组合成功）：
//   ACE_HEADER(4): AceType=0x11(SYSTEM_MANDATORY_LABEL_ACE_TYPE), AceFlags=0,
//                  AceSize=8+4+16=24（SID=S-1-16-4096 长 16 字节）
//   ACCESS_MASK(4): SYSTEM_MANDATORY_LABEL_NO_WRITE_UP(0x1)
//   SID(16): S-1-16-4096
// ACL 总长 = 8 + 8 + 4 + 16 = 36
//
// 标签语义：
//   - 完整性强制为单向墙：低完整性不可写高；宿主 Medium 写可写区放行
//     （高写低，属特性：宿主侧 Teardown 清理需要），威胁模型已接受
//   - Low 创建的子目录/子文件全链继承 Low+NW 标签（无需继承 ACE 标志）
//   - Host Medium 创建的嵌套对象无标签（默认 Medium 语义），不影响宿主清理
// =============================================================================

#include "infra/writearea/WriteAreaImpl.hpp"

#include <windows.h>

#include <aclapi.h>  // GetNamedSecurityInfo / SetNamedSecurityInfo

#include <filesystem>
#include <format>
#include <iterator>  // std::size
#include <string_view>
#include <vector>

#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "kernel32.lib")

namespace winsandbox {

namespace {

constexpr DWORD kMandatoryLabelLevelLow = 0x00001000;  // S-1-16-4096

// hand-rolled SYSTEM_MANDATORY_LABEL_ACE（SDK 头无声明）
struct MandatoryLabelAce {
    BYTE AceType;      // SYSTEM_MANDATORY_LABEL_ACE_TYPE
    BYTE AceFlags;
    WORD AceSize;
    DWORD Mask;        // SYSTEM_MANDATORY_LABEL_NO_WRITE_UP
    BYTE SidStart;     // SID: S-1-16-4096（16 字节，紧随其后）
};

// UTF-8 → UTF-16（可写区路径含用户名，全宽字符处理）
std::wstring Utf8ToWide(const std::string& s) {
    const int len = ::MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    if (len <= 0) {
        return L"";
    }
    std::wstring w(static_cast<size_t>(len) - 1, L'\0');
    ::MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), len);
    return w;
}

// UTF-16 → UTF-8
std::string WideToUtf8(const std::wstring& w) {
    const int len = ::WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (len <= 0) {
        return "";
    }
    std::string s(static_cast<size_t>(len) - 1, '\0');
    ::WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, s.data(), len, nullptr, nullptr);
    return s;
}

// 会话根目录：%LOCALAPPDATA%\win-sandbox\sessions
std::wstring SessionRoot() {
    // 先查长度再分配（避免 64KB 栈缓冲）
    const DWORD n = ::GetEnvironmentVariableW(L"LOCALAPPDATA", nullptr, 0);
    if (n == 0 || n > 32768) {
        return L"";
    }
    std::wstring buf(static_cast<size_t>(n) - 1, L'\0');  // n 含结尾 null
    ::GetEnvironmentVariableW(L"LOCALAPPDATA", buf.data(), n);
    return buf + L"\\win-sandbox\\sessions";
}

} // namespace

WriteAreaImpl::WriteAreaImpl(std::shared_ptr<ILogger> logger)
    : logger_(std::move(logger)) {
    logger_->Log(LogLevel::Debug, "WriteAreaImpl created");
}

Result<void> WriteAreaImpl::Create(uint32_t process_id) {
    // 幂等：已有可写区则直接校验存在性
    if (!path_.empty()) {
        if (std::filesystem::exists(Utf8ToWide(path_))) {
            logger_->Log(LogLevel::Debug,
                         std::format("WriteAreaImpl::Create: already exists, skip: {}", path_));
            return Result<void>::Ok();
        }
        path_.clear();
    }

    const std::wstring root = SessionRoot();
    if (root.empty()) {
        logger_->Log(LogLevel::Error, "WriteAreaImpl::Create: LOCALAPPDATA not resolvable");
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "LOCALAPPDATA environment variable not resolvable");
    }

    const DWORD os_pid = ::GetCurrentProcessId();
    const std::wstring session_dir = root + L"\\" + std::to_wstring(os_pid) + L"-" + std::to_wstring(process_id);
    const std::wstring writable = session_dir + L"\\writable";

    // 逐级创建（已存在按成功处理）
    for (const std::wstring& dir : {root, session_dir, writable}) {
        std::error_code ec;
        std::filesystem::create_directories(dir, ec);
        if (ec) {
            logger_->Log(LogLevel::Error,
                         std::format("WriteAreaImpl::Create: create dir failed path={} err={}",
                                     WideToUtf8(dir), ec.message()));
            return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                     "create directory failed: " + WideToUtf8(dir) + " (" + ec.message() + ")");
        }
    }

    // 打 Low 标签（核心步骤，失败即整体失败——可写区不可写则沙箱无法工作）
    Result<void> label_rc = ApplyLowLabel(writable);
    if (!label_rc) {
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed, label_rc.Message());
    }

    // 当前用户 Full Control 校验/追加
    Result<void> dacl_rc = EnsureUserFullControl(writable);
    if (!dacl_rc) {
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed, dacl_rc.Message());
    }

    path_ = WideToUtf8(writable);
    session_dir_ = WideToUtf8(session_dir);
    logger_->Log(LogLevel::Info,
                 std::format("WriteAreaImpl::Create: write area ready (Low label): {}", path_));
    return Result<void>::Ok();
}

std::string WriteAreaImpl::Path() const {
    return path_;
}

Result<void> WriteAreaImpl::Teardown() {
    if (path_.empty()) {
        return Result<void>::Ok();
    }
    // 删除整个会话目录（writable 及其父目录），防空目录残留；
    // 失败不阻断（残留句柄等），由 StartupCleanup 启动期兜底
    std::error_code ec;
    const std::wstring session_dir = Utf8ToWide(session_dir_);
    std::filesystem::remove_all(session_dir, ec);
    if (ec) {
        logger_->Log(LogLevel::Warn,
                     std::format("WriteAreaImpl::Teardown: remove_all failed path={} err={}",
                                 session_dir_, ec.message()));
        return Result<void>::Err(ErrorCode::WriteAreaTeardownFailed,
                                 "remove_all failed: " + ec.message());
    }
    logger_->Log(LogLevel::Info,
                 std::format("WriteAreaImpl::Teardown: session dir removed: {}", session_dir_));
    session_dir_.clear();
    path_.clear();  // 幂等：清空后再次 Teardown 直接 Ok
    return Result<void>::Ok();
}

Result<void> WriteAreaImpl::ApplyLowLabel(const std::wstring& dir) {
    // ACL: 8 头 + 24 ACE（4 hdr + 4 mask + 16 sid）
    alignas(4) BYTE acl_buf[32] = {};
    ACL* acl = reinterpret_cast<ACL*>(acl_buf);
    ::InitializeAcl(acl, sizeof(acl_buf), ACL_REVISION);

    // ACE 布局（小端 x64）：
    //   [0] AceType = 0x11
    //   [1] AceFlags = 0
    //   [2..3] AceSize = 24
    //   [4..7] Mask = 0x1 (NO_WRITE_UP)
    //   [8..23] SID S-1-16-4096: Rev=1, Count=2, Auth[5]=16, Sub[0]=16, Sub[1]=4096
    static_assert(offsetof(MandatoryLabelAce, SidStart) == 8);
    BYTE* p = acl_buf + sizeof(ACL);
    p[0] = 0x11;  // SYSTEM_MANDATORY_LABEL_ACE_TYPE
    p[1] = 0;     // AceFlags（无继承标志，完整性标签默认可继承——实验验证）
    *reinterpret_cast<WORD*>(p + 2) = 24;
    *reinterpret_cast<DWORD*>(p + 4) = 0x1;  // SYSTEM_MANDATORY_LABEL_NO_WRITE_UP
    p[8] = 1;                                 // SID Revision
    p[9] = 2;                                 // SubAuthorityCount
    p[15] = 16;                               // IdentifierAuthority 大端末字节 = 0x10
    *reinterpret_cast<DWORD*>(p + 16) = 16;   // SubAuthority[0] = 16（Low 前缀）
    *reinterpret_cast<DWORD*>(p + 20) = kMandatoryLabelLevelLow;  // SubAuthority[1] = 4096
    acl->AceCount = 1;

    // SetNamedSecurityInfo(SI_LABEL)：非管理员可用
    const DWORD rc = ::SetNamedSecurityInfoW(const_cast<LPWSTR>(dir.c_str()), SE_FILE_OBJECT,
                                             LABEL_SECURITY_INFORMATION,
                                             nullptr, nullptr, nullptr, acl);
    if (rc != ERROR_SUCCESS) {
        logger_->Log(LogLevel::Error,
                     std::format("WriteAreaImpl::ApplyLowLabel: SetNamedSecurityInfo(SI_LABEL) failed rc={} path={}",
                                 rc, WideToUtf8(dir)));
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "SetNamedSecurityInfo(SI_LABEL) failed rc=" + std::to_string(rc));
    }
    logger_->Log(LogLevel::Debug,
                 std::format("WriteAreaImpl::ApplyLowLabel: Low label applied: {}", WideToUtf8(dir)));
    return Result<void>::Ok();
}

Result<void> WriteAreaImpl::EnsureUserFullControl(const std::wstring& dir) {
    // 校验现有 DACL 是否含当前用户 F（%LOCALAPPDATA% 继承场景通常已含，跳过）
    HANDLE tok = nullptr;
    if (!::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &tok)) {
        const DWORD err = ::GetLastError();
        logger_->Log(LogLevel::Error,
                     std::format("WriteAreaImpl::EnsureUserFullControl: OpenProcessToken failed err={}", err));
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "OpenProcessToken failed err=" + std::to_string(err));
    }
    DWORD need = 0;
    ::GetTokenInformation(tok, TokenUser, nullptr, 0, &need);
    std::vector<BYTE> buf(need);
    const BOOL got = ::GetTokenInformation(tok, TokenUser, buf.data(), need, &need);
    ::CloseHandle(tok);
    if (!got) {
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "GetTokenInformation(TokenUser) failed err=" +
                                     std::to_string(::GetLastError()));
    }
    PSID user_sid = reinterpret_cast<TOKEN_USER*>(buf.data())->User.Sid;

    PSECURITY_DESCRIPTOR sd = nullptr;
    PACL dacl = nullptr;
    const DWORD gs_rc = ::GetNamedSecurityInfoW(dir.c_str(), SE_FILE_OBJECT,
                                                DACL_SECURITY_INFORMATION,
                                                nullptr, nullptr, &dacl, nullptr, &sd);
    if (gs_rc != ERROR_SUCCESS) {
        if (sd) ::LocalFree(sd);
        logger_->Log(LogLevel::Error,
                     std::format("WriteAreaImpl::EnsureUserFullControl: GetNamedSecurityInfo failed rc={}", gs_rc));
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "GetNamedSecurityInfo failed rc=" + std::to_string(gs_rc));
    }

    // 遍历现有 ACE，检查当前用户是否已含 FILE_ALL_ACCESS（Full Control）
    // 注意：文件对象 DACL 中常规存储的是 FILE_ALL_ACCESS (0x001F01FF)，
    // 而非 GENERIC_ALL (0x10000000)——用 GENERIC_ALL 匹配恒为 false，
    // 导致每次创建都重复追加 ACE 并改写 DACL
    bool has_full = false;
    if (dacl != nullptr && dacl->AceCount > 0) {
        for (WORD i = 0; i < dacl->AceCount; ++i) {
            ACE_HEADER* hdr = nullptr;
            if (!::GetAce(dacl, i, reinterpret_cast<void**>(&hdr))) {
                continue;
            }
            if (hdr->AceType != ACCESS_ALLOWED_ACE_TYPE) {
                continue;
            }
            auto* allow = reinterpret_cast<ACCESS_ALLOWED_ACE*>(hdr);
            constexpr ACCESS_MASK kFullControl =
                FILE_ALL_ACCESS;  // 0x001F01FF（Full Control 的位组形态）
            if (::EqualSid(&allow->SidStart, user_sid) &&
                (allow->Mask & kFullControl) == kFullControl) {
                has_full = true;
                break;
            }
        }
    }
    if (has_full) {
        ::LocalFree(sd);
        logger_->Log(LogLevel::Debug,
                     std::format("WriteAreaImpl::EnsureUserFullControl: user F already present, skip: {}",
                                 WideToUtf8(dir)));
        return Result<void>::Ok();
    }

    // 追加 (OI)(CI)F：新 ACL = 原 ACL + 新 ACE
    ACL_SIZE_INFORMATION size_info{};
    if (dacl != nullptr) {
        // 无 DACL（受保护文件的空 DACL）时按空 ACL 处理（AclBytesInUse=0）；
        // 显式判空避免对 nullptr 调 GetAclInformation 的未文档化行为
        DWORD acl_info_size = sizeof(size_info);
        ::GetAclInformation(dacl, &size_info, acl_info_size, AclSizeInformation);
    }
    const DWORD new_ace_size = sizeof(ACCESS_ALLOWED_ACE) +
                               ::GetLengthSid(user_sid) - sizeof(DWORD);  // SidStart 已含 1 DWORD
    std::vector<BYTE> new_acl_buf(size_info.AclBytesInUse + new_ace_size + sizeof(ACL));
    ACL* new_acl = reinterpret_cast<ACL*>(new_acl_buf.data());
    ::InitializeAcl(new_acl, static_cast<DWORD>(new_acl_buf.size()), ACL_REVISION);

    // 复制原 ACE
    for (WORD i = 0; i < size_info.AceCount; ++i) {
        ACE_HEADER* hdr = nullptr;
        if (!::GetAce(dacl, i, reinterpret_cast<void**>(&hdr))) {
            continue;
        }
        if (!::AddAce(new_acl, ACL_REVISION, MAXDWORD, hdr, hdr->AceSize)) {
            ::LocalFree(sd);
            return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                     "AddAce (copy) failed err=" + std::to_string(::GetLastError()));
        }
    }
    // 追加当前用户 (OI)(CI)GENERIC_ALL
    if (!::AddAccessAllowedAceEx(new_acl, ACL_REVISION,
                                 CONTAINER_INHERIT_ACE | OBJECT_INHERIT_ACE,
                                 GENERIC_ALL, user_sid)) {
        ::LocalFree(sd);
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "AddAccessAllowedAceEx failed err=" + std::to_string(::GetLastError()));
    }

    const DWORD set_rc = ::SetNamedSecurityInfoW(const_cast<LPWSTR>(dir.c_str()), SE_FILE_OBJECT,
                                                 DACL_SECURITY_INFORMATION,
                                                 nullptr, nullptr, new_acl, nullptr);
    ::LocalFree(sd);
    if (set_rc != ERROR_SUCCESS) {
        logger_->Log(LogLevel::Error,
                     std::format("WriteAreaImpl::EnsureUserFullControl: SetNamedSecurityInfo(DACL) failed rc={}", set_rc));
        return Result<void>::Err(ErrorCode::WriteAreaCreateFailed,
                                 "SetNamedSecurityInfo(DACL) failed rc=" + std::to_string(set_rc));
    }
    logger_->Log(LogLevel::Debug,
                 std::format("WriteAreaImpl::EnsureUserFullControl: user (OI)(CI)F appended: {}",
                             WideToUtf8(dir)));
    return Result<void>::Ok();
}

} // namespace winsandbox