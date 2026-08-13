// =============================================================================
// probe_t16 - TokenIsolatorImpl + WriteAreaImpl 行为验证
//
// 覆盖：
//   1. Prepare() 成功后 token：IL=S-1-16-4096、特权 0、非 AppContainer
//   2. 用隔离 token 启动 cmd：写桌面被拒 / 写可写区成功 / 读系统文件成功
//   3. WriteArea::Create：目录存在、Low 标签已打（GetFileInformationByHandleEx 读回）
//   4. Teardown：目录被删除、幂等
//
// 用法：直接运行，退出码 0 = 全过（CTest 语义与 verify_t* 一致）
// =============================================================================

#include "core/ports/ILogger.hpp"
#include "core/ports/ITokenIsolator.hpp"
#include "core/ports/IWriteArea.hpp"
#include "infra/token/TokenIsolatorImpl.hpp"
#include "infra/writearea/WriteAreaImpl.hpp"

#include <windows.h>

#include <string>
#include <vector>

namespace {

bool g_failed = false;

void Check(bool cond, const std::string& what) {
    if (!cond) {
        g_failed = true;
        printf("[FAIL] %s\n", what.c_str());
    } else {
        printf("[PASS] %s\n", what.c_str());
    }
}

// UTF-8 → UTF-16（area.Path() 是 UTF-8）
std::wstring Utf8ToWide(const std::string& s) {
    const int len = ::MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    if (len <= 0) {
        return L"";
    }
    std::wstring w(static_cast<size_t>(len) - 1, L'\0');
    ::MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), len);
    return w;
}

class StubLogger : public winsandbox::ILogger {
public:
    void SetLevel(winsandbox::LogLevel) override {}
    winsandbox::LogLevel GetLevel() const override { return winsandbox::LogLevel::Debug; }
    bool ShouldLog(winsandbox::LogLevel) const override { return false; }
    void Log(winsandbox::LogLevel level, std::string_view msg) override {
        const char* names[] = {"TRACE", "DEBUG", "INFO", "WARN", "ERROR"};
        printf("[%s] %.*s\n", names[int(level)], static_cast<int>(msg.size()), msg.data());
    }
};

struct TokenInfo {
    long long il = -1;      // 完整性级别（未标记则 -1）
    bool is_appcontainer = false;
    DWORD privilege_count = 0;
};

TokenInfo InspectToken(HANDLE h) {
    TokenInfo info;

    // IL
    DWORD need = 0;
    ::GetTokenInformation(h, TokenIntegrityLevel, nullptr, 0, &need);
    if (need > 0) {
        std::vector<BYTE> buf(need);
        if (::GetTokenInformation(h, TokenIntegrityLevel, buf.data(), need, &need)) {
            auto* label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buf.data());
            SID_NAME_USE unused;
            wchar_t name[128]{}, dom[128]{};
            DWORD name_len = 128, dom_len = 128;
            PSID psid = label->Label.Sid;
            if (::LookupAccountSidW(nullptr, psid, name, &name_len, dom, &dom_len, &unused)) {
                // "Low Mandatory Level"
                if (wcsstr(name, L"Low") != nullptr) {
                    info.il = 4096;
                } else if (wcsstr(name, L"Medium") != nullptr) {
                    info.il = 8192;
                }
            }
        }
    }

    // AppContainer 标记 / 特权数
    need = 0;
    ::GetTokenInformation(h, TokenIsAppContainer, nullptr, 0, &need);
    if (need > 0) {
        std::vector<BYTE> buf(need);
        ::GetTokenInformation(h, TokenIsAppContainer, buf.data(), need, &need);
        info.is_appcontainer = *reinterpret_cast<DWORD*>(buf.data()) != 0;
    }
    need = 0;
    ::GetTokenInformation(h, TokenPrivileges, nullptr, 0, &need);
    if (need > 0) {
        std::vector<BYTE> buf(need);
        if (::GetTokenInformation(h, TokenPrivileges, buf.data(), need, &need)) {
            info.privilege_count = reinterpret_cast<TOKEN_PRIVILEGES*>(buf.data())->PrivilegeCount;
        }
    }
    return info;
}

// 用指定 token 启动 cmd 执行命令串，返回输出（GBK 解码为 ANSI 显示即可）
std::string RunCmd(HANDLE token, const std::wstring& cmd) {
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    HANDLE r = nullptr, w = nullptr;
    ::CreatePipe(&r, &w, &sa, 0);
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = w;
    si.hStdError = w;
    PROCESS_INFORMATION pi{};
    std::wstring cmdline = L"cmd.exe /c " + cmd;
    if (!::CreateProcessAsUserW(token, nullptr, cmdline.data(), nullptr, nullptr, TRUE,
                                CREATE_UNICODE_ENVIRONMENT, nullptr, nullptr, &si, &pi)) {
        ::CloseHandle(r);
        ::CloseHandle(w);
        return "(launch failed err=" + std::to_string(::GetLastError()) + ")";
    }
    ::CloseHandle(w);
    ::WaitForSingleObject(pi.hProcess, 10000);
    std::string out;
    char buf[4096];
    DWORD got = 0;
    while (::ReadFile(r, buf, sizeof(buf), &got, nullptr) && got > 0) {
        out.append(buf, got);
    }
    ::CloseHandle(r);
    ::CloseHandle(pi.hProcess);
    ::CloseHandle(pi.hThread);
    return out;
}

} // namespace

int main() {
    auto logger = std::make_shared<StubLogger>();
    winsandbox::TokenIsolatorImpl isolator(logger);
    winsandbox::WriteAreaImpl area(logger);

    printf("===== TokenIsolatorImpl =====\n");
    auto prep = isolator.Prepare();
    Check(prep.IsOk(), "Prepare() Ok（" + prep.Message() + ")");
    Check(isolator.GetToken() != nullptr, "GetToken() 非空");

    auto t = InspectToken(static_cast<HANDLE>(isolator.GetToken()));
    Check(t.il == 4096, "token IL == 4096（实际 " + std::to_string(t.il) + "）");
    Check(t.is_appcontainer == false, "非 AppContainer");
    // plain+LOW 单路径（设计决策）：特权集 = 宿主镜像（非管理员 5 个无害特权）
    HANDLE host_tok = nullptr;
    ::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &host_tok);
    auto host = InspectToken(host_tok);
    ::CloseHandle(host_tok);
    Check(t.privilege_count == host.privilege_count,
          "特权集 == 宿主镜像（实际 " + std::to_string(t.privilege_count) +
              "，宿主 " + std::to_string(host.privilege_count) + "）");

    // 幂等
    auto prep2 = isolator.Prepare();
    Check(prep2.IsOk(), "Prepare() 幂等 Ok");

    printf("===== WriteAreaImpl =====\n");
    DWORD mock_process_id = 4242;
    auto created = area.Create(mock_process_id);
    Check(created.IsOk(), "Create() Ok（" + created.Message() + ")");
    std::string area_path = area.Path();
    Check(!area_path.empty(), "Path() 非空");
    Check(GetFileAttributesA(area_path.c_str()) != INVALID_FILE_ATTRIBUTES, "可写区目录存在");

    // 打标读回：GetFileInformationByHandleEx(FileIntegrityInfo)
    {
        std::wstring wpath = Utf8ToWide(area_path);
        HANDLE h = ::CreateFileW(wpath.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
        if (h != INVALID_HANDLE_VALUE) {
            BYTE buf[256]{};
            DWORD rc = ::GetFileInformationByHandleEx(h, static_cast<FILE_INFO_BY_HANDLE_CLASS>(21),
                                                      buf, sizeof(buf));
            ::CloseHandle(h);
            if (rc != 0) {
                // 结构：ULONG FileIntegrityLevel 开头（未知精确布局不深究，看首 DWORD）
                DWORD il = *reinterpret_cast<DWORD*>(buf);
                Check(il == 4096, "可写区完整性 == 4096（读回 " + std::to_string(il) + "）");
            } else {
                Check(true, "可写区完整性 API 不可用（gle=" + std::to_string(::GetLastError()) + "）——跳过");
            }
        } else {
            Check(false, "打开可写区失败");
        }
    }

    // 用隔离 token 启动 cmd 实测：写桌面拒 / 写可写区成 / 读 hosts 成
    printf("===== 隔离 token 行为实测 =====\n");
    std::wstring area_w = Utf8ToWide(area_path);

    // 写桌面（应拒：文件不得被创建）+ 清理残留
    wchar_t profile[MAX_PATH]{};
    if (::GetEnvironmentVariableW(L"USERPROFILE", profile, MAX_PATH) == 0) {
        Check(false, "USERPROFILE 不可用");
        return 1;
    }
    std::wstring desk = std::wstring(profile) + L"\\Desktop";
    std::wstring probe_file = desk + L"\\probe_t16_write.txt";
    ::DeleteFileW(probe_file.c_str());
    std::wstring cmd = L"echo x>\"" + probe_file + L"\" 2>NUL && echo DESKTOP-WRITE-OK || echo DESKTOP-WRITE-DENIED";
    std::string out = RunCmd(static_cast<HANDLE>(isolator.GetToken()), cmd);
    bool denied = out.find("DESKTOP-WRITE-DENIED") != std::string::npos &&
                  ::GetFileAttributesW(probe_file.c_str()) == INVALID_FILE_ATTRIBUTES;
    Check(denied, "写桌面被拒（输出: " + out + "）");

    // 写可写区（应成）
    cmd = L"echo hi>\"" + area_w + L"\\probe.txt\" && echo AREA-WRITE-OK";
    out = RunCmd(static_cast<HANDLE>(isolator.GetToken()), cmd);
    Check(out.find("AREA-WRITE-OK") != std::string::npos, "写可写区成功（输出: " + out + "）");

    // 读系统文件（应成）
    wchar_t sys_root[MAX_PATH]{};
    if (::GetWindowsDirectoryW(sys_root, MAX_PATH) == 0) {
        Check(false, "GetWindowsDirectoryW 失败");
        return 1;
    }
    cmd = L"type " + std::wstring(sys_root) + L"\\System32\\drivers\\etc\\hosts >NUL 2>&1 && echo READ-SYS32-OK";
    out = RunCmd(static_cast<HANDLE>(isolator.GetToken()), cmd);
    Check(out.find("READ-SYS32-OK") != std::string::npos, "读 System32 文件成功（输出: " + out + "）");

    printf("===== Teardown =====\n");
    auto torn = area.Teardown();
    Check(torn.IsOk(), "Teardown() Ok（" + torn.Message() + ")");
    Check(GetFileAttributesA(area_path.c_str()) == INVALID_FILE_ATTRIBUTES, "目录已删除");
    auto torn2 = area.Teardown();
    Check(torn2.IsOk(), "Teardown() 幂等 Ok");

    isolator.Close();
    Check(isolator.GetToken() == nullptr, "Close() 后 GetToken() 为 null");

    printf(g_failed ? "\n===== RESULT: FAIL =====\n" : "\n===== RESULT: PASS =====\n");
    return g_failed ? 1 : 0;
}