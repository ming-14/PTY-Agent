// token.cpp — WRITE_RESTRICTED token construction.
//
// Duplicates the caller's token into a write-restricted token whose
// restricting-SID list follows the mode:
//   read-only:       [logon SID, Everyone]
//   workspace-write: [logon SID, Everyone, workspace SID, temp SID]
// The logon SID + Everyone keep-alive group is shared by both modes: early
// DLL init dies with 0xC0000142 and CNG crashes pwsh without them. Every call
// is checked; any failure throws — the child is never spawned unrestricted.
#include "winacl.h"

#include <aclapi.h>
#include <sddl.h>

#include <stdexcept>

#pragma comment(lib, "advapi32.lib")

namespace winacl {
namespace {

// Open the current process token with the rights CreateRestrictedToken needs.
HANDLE openCurrentProcessToken() {
  HANDLE token = nullptr;
  const DWORD access = TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ADJUST_DEFAULT | TOKEN_ASSIGN_PRIMARY;
  if (!OpenProcessToken(GetCurrentProcess(), access, &token) || token == nullptr) {
    throw std::runtime_error("OpenProcessToken failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
  return token;
}

// Copy the logon session SID (SE_GROUP_LOGON_ID) out of the token's groups.
PSID findLogonSid(HANDLE token) {
  DWORD needed = 0;
  GetTokenInformation(token, TokenGroups, nullptr, 0, &needed);
  if (needed == 0) throw std::runtime_error("GetTokenInformation(TokenGroups) size query failed");
  std::vector<uint8_t> buffer(needed);
  if (!GetTokenInformation(token, TokenGroups, buffer.data(), needed, &needed)) {
    throw std::runtime_error("GetTokenInformation(TokenGroups) failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
  const auto* groups = reinterpret_cast<const TOKEN_GROUPS*>(buffer.data());
  for (DWORD i = 0; i < groups->GroupCount; i++) {
    const SID_AND_ATTRIBUTES& entry = groups->Groups[i];
    if ((entry.Attributes & SE_GROUP_LOGON_ID) == SE_GROUP_LOGON_ID && entry.Sid != nullptr) {
      const DWORD len = GetLengthSid(entry.Sid);
      PSID copy = LocalAlloc(LPTR, len);
      if (copy == nullptr || !CopySid(len, copy, entry.Sid)) {
        if (copy != nullptr) LocalFree(copy);
        throw std::runtime_error("CopySid(logon SID) failed");
      }
      return copy;
    }
  }
  throw std::runtime_error("CreateRestrictedToken prerequisite failed: no logon SID found");
}

PSID makeWellKnownSid(WELL_KNOWN_SID_TYPE type) {
  const DWORD size = SECURITY_MAX_SID_SIZE;
  PSID sid = LocalAlloc(LPTR, size);
  if (sid == nullptr) throw std::runtime_error("LocalAlloc(well-known SID) failed");
  DWORD written = size;
  if (!CreateWellKnownSid(type, nullptr, sid, &written) || !IsValidSid(sid)) {
    LocalFree(sid);
    throw std::runtime_error("CreateWellKnownSid(type " + std::to_string(type) + ") failed");
  }
  return sid;
}

}  // namespace

HANDLE createRestrictedToken(Mode mode, const std::vector<PSID>& writeSids, PSID& outLogonSid, PSID& outWorldSid, bool writeRestricted) {
  HANDLE current = openCurrentProcessToken();
  PSID logonSid = nullptr;
  PSID worldSid = nullptr;
  HANDLE restricted = nullptr;
  try {
    logonSid = findLogonSid(current);
    worldSid = makeWellKnownSid(WinWorldSid);

    std::vector<PSID> restricting;
    restricting.push_back(logonSid);
    restricting.push_back(worldSid);
    if (mode == Mode::WorkspaceWrite) {
      if (writeSids.empty()) {
        throw std::runtime_error("workspace-write restricting list requires at least one write SID");
      }
      restricting.insert(restricting.end(), writeSids.begin(), writeSids.end());
    }

    std::vector<SID_AND_ATTRIBUTES> list;
    list.reserve(restricting.size());
    for (PSID sid : restricting) {
      list.push_back(SID_AND_ATTRIBUTES{sid, 0});
    }
    if (!CreateRestrictedToken(current, writeRestricted ? kRestrictFlags : (DISABLE_MAX_PRIVILEGE | LUA_TOKEN), 0, nullptr, 0, nullptr,
                               static_cast<DWORD>(list.size()), list.data(), &restricted)
        || restricted == nullptr) {
      throw std::runtime_error("CreateRestrictedToken failed (Win32 " + std::to_string(GetLastError()) + ")");
    }
    outLogonSid = logonSid;  // owned by the caller
    logonSid = nullptr;      // ownership transferred
    outWorldSid = worldSid;
    worldSid = nullptr;
    CloseHandle(current);
    return restricted;
  } catch (...) {
    if (logonSid != nullptr) LocalFree(logonSid);
    if (worldSid != nullptr) LocalFree(worldSid);
    if (restricted != nullptr) CloseHandle(restricted);
    CloseHandle(current);
    throw;
  }
}

void setTokenDefaultDaclGrant(HANDLE token, PSID sid) {
  DWORD needed = 0;
  GetTokenInformation(token, TokenDefaultDacl, nullptr, 0, &needed);
  if (needed == 0) throw std::runtime_error("GetTokenInformation(TokenDefaultDacl) size query failed");
  std::vector<uint8_t> buffer(needed);
  if (!GetTokenInformation(token, TokenDefaultDacl, buffer.data(), needed, &needed)) {
    throw std::runtime_error("GetTokenInformation(TokenDefaultDacl) failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
  PACL current = reinterpret_cast<TOKEN_DEFAULT_DACL*>(buffer.data())->DefaultDacl;
  if (current == nullptr) throw std::runtime_error("the token carries no default DACL to extend");

  // Build the EXPLICIT_ACCESS_W naming the restricting SID with FILE_ALL_ACCESS.
  EXPLICIT_ACCESS_W entry{};
  entry.grfAccessPermissions = FILE_ALL_ACCESS;
  entry.grfAccessMode = GRANT_ACCESS;
  entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
  entry.Trustee.TrusteeForm = TRUSTEE_IS_SID;
  entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN;
  entry.Trustee.ptstrName = reinterpret_cast<LPWCH>(sid);

  PACL merged = nullptr;
  const DWORD rc = SetEntriesInAclW(1, &entry, current, &merged);  if (rc != ERROR_SUCCESS || merged == nullptr) {
    if (merged != nullptr) LocalFree(merged);
    throw std::runtime_error("SetEntriesInAclW(default DACL) failed (Win32 " + std::to_string(rc) + ")");
  }
  TOKEN_DEFAULT_DACL info{};
  info.DefaultDacl = merged;
  if (!SetTokenInformation(token, TokenDefaultDacl, &info, sizeof(info))) {
    const DWORD err = GetLastError();
    LocalFree(merged);
    throw std::runtime_error("SetTokenInformation(TokenDefaultDacl) failed (Win32 " + std::to_string(err) + ")");
  }
  LocalFree(merged);
}

}  // namespace winacl
