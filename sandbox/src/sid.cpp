// sid.cpp — capability-SID derivation and SID parsing.
//
// The workspace write SID is a deterministic S-1-4-x-y derived from the
// canonical workspace path; the temp SID is S-1-4-x-y-1 derived from the
// private temp path. Derivation must be byte-compatible with the TS
// workspaceWriteSid/tempWriteSid (node:crypto sha256, little-endian
// readUInt32LE of the first eight digest bytes), so ACEs materialized by
// earlier TS-run sessions keep working and the runner can verify --write-sid
// against --workspace.
#include "winacl.h"

#include <bcrypt.h>
#include <sddl.h>

#include <array>
#include <stdexcept>

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "advapi32.lib")

namespace winacl {

// SHA-256 via the BCrypt streaming API (this SDK ships no one-shot BCryptHash).
std::array<uint8_t, 32> sha256(const uint8_t* data, size_t size) {
  BCRYPT_ALG_HANDLE alg = nullptr;
  if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0) {
    throw std::runtime_error("BCryptOpenAlgorithmProvider(SHA256) failed");
  }
  BCRYPT_HASH_HANDLE hash = nullptr;
  NTSTATUS status = BCryptCreateHash(alg, &hash, nullptr, 0, nullptr, 0, 0);
  if (status != 0) {
    BCryptCloseAlgorithmProvider(alg, 0);
    throw std::runtime_error("BCryptCreateHash failed");
  }
  status = BCryptHashData(hash, const_cast<uint8_t*>(data), static_cast<ULONG>(size), 0);
  if (status != 0) {
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(alg, 0);
    throw std::runtime_error("BCryptHashData failed");
  }
  std::array<uint8_t, 32> digest{};
  status = BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0);
  BCryptDestroyHash(hash);
  BCryptCloseAlgorithmProvider(alg, 0);
  if (status != 0) {
    throw std::runtime_error("BCryptFinishHash failed");
  }
  return digest;
}

namespace {

// The TS derivation: (readUInt32LE % (2^30 - 1)) + 1 — node reads the first
// four digest bytes as a little-endian uint32.
uint32_t subauthority(const std::array<uint8_t, 32>& digest, size_t offset) {
  const uint32_t le = static_cast<uint32_t>(digest[offset])
      | (static_cast<uint32_t>(digest[offset + 1]) << 8)
      | (static_cast<uint32_t>(digest[offset + 2]) << 16)
      | (static_cast<uint32_t>(digest[offset + 3]) << 24);
  return (le % ((1u << 30) - 1)) + 1;
}

std::wstring utf8ToWide(const std::string& utf8) {
  if (utf8.empty()) return {};
  const int len = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
  if (len <= 0) throw std::runtime_error("MultiByteToWideChar failed");
  std::wstring wide(static_cast<size_t>(len) - 1, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, wide.data(), len);
  return wide;
}

std::string wideToUtf8(const std::wstring& wide) {
  if (wide.empty()) return {};
  const int len = WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, nullptr, 0, nullptr, nullptr);
  if (len <= 0) throw std::runtime_error("WideCharToMultiByte failed");
  std::string utf8(static_cast<size_t>(len) - 1, '\0');
  WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, utf8.data(), len, nullptr, nullptr);
  return utf8;
}

std::wstring deriveSid(const std::string& material, int thirdSubauthority) {
  // The TS derivation hashes "temp\0" + material for the temp SID — node's
  // string update includes the NUL as data bytes, so build the material with
  // an explicit embedded NUL.
  const std::string utf8 = thirdSubauthority >= 0
      ? std::string("temp\0", 5) + material
      : material;
  const std::array<uint8_t, 32> digest = sha256(
      reinterpret_cast<const uint8_t*>(utf8.data()), utf8.size());
  const uint32_t first = subauthority(digest, 0);
  const uint32_t second = subauthority(digest, 4);
  if (thirdSubauthority >= 0) {
    return L"S-1-4-" + std::to_wstring(first) + L"-" + std::to_wstring(second)
        + L"-" + std::to_wstring(thirdSubauthority);
  }
  return L"S-1-4-" + std::to_wstring(first) + L"-" + std::to_wstring(second);
}

}  // namespace

std::wstring workspaceWriteSid(const std::wstring& workspaceRoot) {
  return deriveSid(wideToUtf8(workspaceRoot), -1);
}

std::wstring tempWriteSid(const std::wstring& tempDir) {
  return deriveSid(wideToUtf8(tempDir), 1);
}

PSID parseSid(const std::wstring& sddl) {
  PSID sid = nullptr;
  if (!ConvertStringSidToSidW(sddl.c_str(), &sid) || sid == nullptr) {
    throw std::runtime_error("ConvertStringSidToSidW failed for " + wideToUtf8(sddl));
  }
  return sid;  // LocalFree by the caller
}

}  // namespace winacl
