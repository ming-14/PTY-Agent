// acl.cpp — capability-SID write-grant materialization on directory DACLs.
//
// grantWrite merges an Allow ACE (kGrantMask, OI|CI inheritance, the
// capability SID) into a directory's explicit DACL via SetEntriesInAclW +
// SetNamedSecurityInfoW, under a per-path LockFileEx lock so concurrent
// launcher/harness instances cannot clobber each other's ACEs. When the exact
// ACE already stands the apply is skipped — SetNamedSecurityInfoW would
// otherwise eagerly re-propagate the identical ACE across the whole tree.
#include "winacl.h"

#include <aclapi.h>
#include <bcrypt.h>

#include <array>
#include <cstdio>
#include <stdexcept>

#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "bcrypt.lib")

namespace winacl {
namespace {

// <GetTempPathW()>\dsh-acl-locks\<sha256(lowercased path) 前16 hex>.lock —
// the same lock-root convention as the TS implementation.
std::wstring lockFilePath(const std::wstring& path) {
  wchar_t temp[MAX_PATH + 1] = {};
  const DWORD len = GetTempPathW(MAX_PATH + 1, temp);
  if (len == 0 || len > MAX_PATH) throw std::runtime_error("GetTempPathW failed");
  std::wstring lower(path.size() + 1, L'\0');
  const int mapped = LCMapStringW(LOCALE_INVARIANT, LCMAP_LOWERCASE, path.c_str(), -1,
                                  lower.data(), static_cast<int>(lower.size()));
  if (mapped == 0) throw std::runtime_error("LCMapStringW(lowercase) failed (Win32 "
                                            + std::to_string(GetLastError()) + ")");
  lower.resize(static_cast<size_t>(mapped) - 1);  // strip the copied NUL
  const std::string utf8 = [&] {
    const int n = WideCharToMultiByte(CP_UTF8, 0, lower.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(n) - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, lower.c_str(), -1, s.data(), n, nullptr, nullptr);
    return s;
  }();
  // Reuse the shared SHA-256 (winacl::sha256, streaming BCrypt) on the UTF-8
  // bytes, take the first 8 bytes as 16 hex chars (matches TS slice(0,16)).
  const std::array<uint8_t, 32> digest = sha256(
      reinterpret_cast<const uint8_t*>(utf8.data()), utf8.size());
  wchar_t hex[17] = {};
  for (int i = 0; i < 8; i++) {
    swprintf_s(hex + i * 2, 3, L"%02x", digest[i]);
  }
  return std::wstring(temp) + L"dsh-acl-locks\\" + hex + L".lock";
}

template <typename F>
void withPathLock(const std::wstring& path, F&& action) {
  const std::wstring lockPath = lockFilePath(path);
  const std::wstring lockDir = lockPath.substr(0, lockPath.find_last_of(L'\\'));
  CreateDirectoryW(lockDir.c_str(), nullptr);  // best-effort: exists is fine
  HANDLE handle = CreateFileW(lockPath.c_str(), GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS, 0, nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    throw std::runtime_error("CreateFileW(lock) failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
  OVERLAPPED overlapped{};
  if (!LockFileEx(handle, LOCKFILE_EXCLUSIVE_LOCK, 0, 1, 0, &overlapped)) {
    const DWORD err = GetLastError();
    CloseHandle(handle);
    throw std::runtime_error("LockFileEx failed (Win32 " + std::to_string(err) + ")");
  }
  try {
    action();
  } catch (...) {
    UnlockFileEx(handle, 0, 1, 0, &overlapped);
    CloseHandle(handle);
    throw;
  }
  if (!UnlockFileEx(handle, 0, 1, 0, &overlapped)) {
    const DWORD err = GetLastError();
    CloseHandle(handle);
    throw std::runtime_error("UnlockFileEx failed (Win32 " + std::to_string(err) + ")");
  }
  if (!CloseHandle(handle)) {
    throw std::runtime_error("CloseHandle(lock) failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
}

struct CurrentDacl {
  PACL acl = nullptr;
  PSECURITY_DESCRIPTOR descriptor = nullptr;
};

CurrentDacl readCurrentDacl(const std::wstring& path) {
  CurrentDacl out;
  const DWORD rc = GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                                         nullptr, nullptr, &out.acl, nullptr, &out.descriptor);
  if (rc != ERROR_SUCCESS) {
    throw std::runtime_error("GetNamedSecurityInfoW failed (Win32 " + std::to_string(rc) + ")");
  }
  return out;
}

void mergeAndApply(const std::wstring& path, const EXPLICIT_ACCESS_W& entry, PACL oldAcl,
                   PSECURITY_DESCRIPTOR descriptor, const char* label) {
  PACL merged = nullptr;
  EXPLICIT_ACCESS_W mutableEntry = entry;
  const DWORD mergeRc = SetEntriesInAclW(1, &mutableEntry, oldAcl, &merged);
  if (mergeRc != ERROR_SUCCESS || merged == nullptr) {
    if (descriptor != nullptr) LocalFree(descriptor);
    if (merged != nullptr) LocalFree(merged);
    throw std::runtime_error(std::string(label) + ": SetEntriesInAclW failed (Win32 " + std::to_string(mergeRc) + ")");
  }
  // The descriptor block (oldAcl included) is dead after the merge.
  if (descriptor != nullptr) LocalFree(descriptor);
  const DWORD applyRc = SetNamedSecurityInfoW(const_cast<LPWSTR>(path.c_str()), SE_FILE_OBJECT,
                                              DACL_SECURITY_INFORMATION, nullptr, nullptr, merged, nullptr);
  LocalFree(merged);
  if (applyRc != ERROR_SUCCESS) {
    throw std::runtime_error(std::string(label) + ": SetNamedSecurityInfoW failed (Win32 " + std::to_string(applyRc) + ")");
  }
}

// True when the explicit DACL already carries the exact grant this module
// adds: Allow ACE, OI|CI inheritance, kGrantMask, the capability SID.
bool hasExactGrant(PACL acl, PSID sid) {
  if (acl == nullptr) return false;
  for (DWORD i = 0; i < acl->AceCount; i++) {
    ACE_HEADER* header = nullptr;
    if (!GetAce(acl, i, reinterpret_cast<void**>(&header)) || header == nullptr) continue;
    if (header->AceType != ACCESS_ALLOWED_ACE_TYPE) continue;
    const auto* ace = reinterpret_cast<const ACCESS_ALLOWED_ACE*>(header);
    if (ace->Header.AceFlags != (CONTAINER_INHERIT_ACE | OBJECT_INHERIT_ACE)) continue;
    if (ace->Mask != kGrantMask) continue;
    if (EqualSid(reinterpret_cast<PSID>(const_cast<DWORD*>(&ace->SidStart)), sid)) return true;
  }
  return false;
}

void grantWriteLocked(const std::wstring& path, PSID sid) {
  CurrentDacl current = readCurrentDacl(path);
  if (current.acl != nullptr && hasExactGrant(current.acl, sid)) {
    if (current.descriptor != nullptr) LocalFree(current.descriptor);
    return;
  }
  EXPLICIT_ACCESS_W entry{};
  entry.grfAccessPermissions = kGrantMask;
  entry.grfAccessMode = GRANT_ACCESS;
  entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
  entry.Trustee.TrusteeForm = TRUSTEE_IS_SID;
  entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN;
  entry.Trustee.ptstrName = reinterpret_cast<LPWCH>(sid);
  mergeAndApply(path, entry, current.acl, current.descriptor, "grantWrite");
}

void revokeWriteLocked(const std::wstring& path, PSID sid) {
  CurrentDacl current = readCurrentDacl(path);
  if (current.acl == nullptr) {
    if (current.descriptor != nullptr) LocalFree(current.descriptor);
    return;
  }
  EXPLICIT_ACCESS_W entry{};
  entry.grfAccessPermissions = 0;
  entry.grfAccessMode = REVOKE_ACCESS;
  entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
  entry.Trustee.TrusteeForm = TRUSTEE_IS_SID;
  entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN;
  entry.Trustee.ptstrName = reinterpret_cast<LPWCH>(sid);
  mergeAndApply(path, entry, current.acl, current.descriptor, "revokeWrite");
}

// Write-class process rights a confined child must never hold on the host:
// termination, memory write, thread/process creation, handle duplication,
// suspend/resume, quota/limit mutation, DELETE and the DACL/owner writes that
// would let it rewrite the boundary itself. Read-class rights (query, VM read,
// synchronize, read-control) stay allowed — process visibility is outside the
// sandbox vocabulary.
constexpr DWORD kDenyProcessMask =
    PROCESS_TERMINATE | PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
    PROCESS_VM_WRITE | PROCESS_DUP_HANDLE | PROCESS_CREATE_PROCESS |
    PROCESS_SET_QUOTA | PROCESS_SET_INFORMATION | PROCESS_SUSPEND_RESUME |
    PROCESS_SET_LIMITED_INFORMATION | DELETE | WRITE_DAC | WRITE_OWNER;

// True when `acl` already carries a Deny ACE for `sid` covering every bit of
// `mask` (idempotency guard for hardenHostProcessDacl).
bool hasExactDenyAce(PACL acl, PSID sid, DWORD mask) {
  if (acl == nullptr) return false;
  for (DWORD i = 0; i < acl->AceCount; i++) {
    ACE_HEADER* header = nullptr;
    if (!GetAce(acl, i, reinterpret_cast<void**>(&header)) || header == nullptr) continue;
    if (header->AceType != ACCESS_DENIED_ACE_TYPE) continue;
    const auto* ace = reinterpret_cast<const ACCESS_DENIED_ACE*>(header);
    if ((ace->Mask & mask) != mask) continue;
    if (EqualSid(reinterpret_cast<PSID>(const_cast<DWORD*>(&ace->SidStart)), sid)) return true;
  }
  return false;
}

}  // namespace

void hardenHostProcessDacl(PSID logonSid, PSID worldSid) {
  // Read the host process's current DACL (SE_KERNEL_OBJECT, DACL only).
  PACL currentDacl = nullptr;
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  const DWORD getRc = GetSecurityInfo(GetCurrentProcess(), SE_KERNEL_OBJECT,
                                      DACL_SECURITY_INFORMATION, nullptr, nullptr,
                                      &currentDacl, nullptr, &descriptor);
  if (getRc != ERROR_SUCCESS) {
    throw std::runtime_error("hardenHostProcessDacl: GetSecurityInfo failed (Win32 "
                             + std::to_string(getRc) + ")");
  }
  // Idempotent: both Deny ACEs already stand — skip the re-apply.
  if (currentDacl != nullptr && hasExactDenyAce(currentDacl, logonSid, kDenyProcessMask)
      && hasExactDenyAce(currentDacl, worldSid, kDenyProcessMask)) {
    LocalFree(descriptor);
    return;
  }
  // Build the two Deny ACEs (logon SID + Everyone); SetEntriesInAclW orders
  // Deny entries ahead of the ambient Allows.
  EXPLICIT_ACCESS_W entries[2] = {};
  for (EXPLICIT_ACCESS_W& entry : entries) {
    entry.grfAccessPermissions = kDenyProcessMask;
    entry.grfAccessMode = DENY_ACCESS;
    entry.grfInheritance = NO_INHERITANCE;
    entry.Trustee.TrusteeForm = TRUSTEE_IS_SID;
    entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN;
  }
  entries[0].Trustee.ptstrName = reinterpret_cast<LPWCH>(logonSid);
  entries[1].Trustee.ptstrName = reinterpret_cast<LPWCH>(worldSid);
  PACL merged = nullptr;
  const DWORD mergeRc = SetEntriesInAclW(2, entries, currentDacl, &merged);
  if (descriptor != nullptr) LocalFree(descriptor);
  if (mergeRc != ERROR_SUCCESS || merged == nullptr) {
    if (merged != nullptr) LocalFree(merged);
    throw std::runtime_error("hardenHostProcessDacl: SetEntriesInAclW failed (Win32 "
                             + std::to_string(mergeRc) + ")");
  }
  const DWORD applyRc = SetSecurityInfo(GetCurrentProcess(), SE_KERNEL_OBJECT,
                                        DACL_SECURITY_INFORMATION, nullptr, nullptr,
                                        merged, nullptr);
  LocalFree(merged);
  if (applyRc != ERROR_SUCCESS) {
    throw std::runtime_error("hardenHostProcessDacl: SetSecurityInfo failed (Win32 "
                             + std::to_string(applyRc) + ")");
  }
}

void grantWrite(const std::wstring& dir, PSID sid) {
  withPathLock(dir, [&] { grantWriteLocked(dir, sid); });
}

void revokeWrite(const std::wstring& dir, PSID sid) {
  withPathLock(dir, [&] { revokeWriteLocked(dir, sid); });
}

}  // namespace winacl
