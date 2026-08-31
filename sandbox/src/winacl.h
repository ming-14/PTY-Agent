// winacl.h — shared definitions for the windows-acl-run confinement launcher.
//
// A C++ reimplementation of the @deepseek-ai/dsh-sandbox-windows-acl runner:
// wraps a caller's argv so it executes under a WRITE_RESTRICTED token whose
// restricting SIDs carry per-workspace and per-session-temp write capabilities,
// inside a kill-on-close job, with the caller's stdio passed straight through.
// Fail-closed: any Win32 failure prints `windows-acl-run: <detail>` to stderr
// and exits 127 without ever spawning the child unrestricted.
#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <array>
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <unordered_set>
#include <vector>

namespace winacl {

// SHA-256 of a byte range (BCrypt streaming API; implemented in sid.cpp).
std::array<uint8_t, 32> sha256(const uint8_t* data, size_t size);

// The sandbox grant: FILE_GENERIC_WRITE minus READ_CONTROL plus DELETE and
// FILE_DELETE_CHILD — "Modify" in Explorer/icacls. WRITE_DAC/WRITE_OWNER are
// deliberately excluded so a confined child can never take ownership or
// rewrite DACLs to escape the allowlist. Mirrors abi.GRANT_MASK (0x110156).
constexpr DWORD kGrantMask = (FILE_GENERIC_WRITE | DELETE | FILE_DELETE_CHILD) & ~STANDARD_RIGHTS_WRITE;

// Restricting-token flags: strip max privilege, synthesize the limited-user
// (filtered admin) effect, and intersect write accesses with restricting-SID
// grants — the core mechanism.
constexpr DWORD kRestrictFlags = DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED;
constexpr DWORD kLuaFlags = DISABLE_MAX_PRIVILEGE | LUA_TOKEN;

// Runner-failure contract: every runner-side failure prints this signature
// line and exits 127 so the harness's RUNNER_FAILURE_RULES match it.
inline constexpr const wchar_t* kRunnerSignature = L"windows-acl-run";
constexpr int kRunnerFailureExit = 127;

enum class Mode { ReadOnly, WorkspaceWrite };

// --- sid.cpp ----------------------------------------------------------------

// Deterministic S-1-4-x-y capability SID derived from the canonical workspace
// path (byte-compatible with the TS workspaceWriteSid, so standing ACEs from
// earlier TS-run sessions stay authoritative and reusable).
std::wstring workspaceWriteSid(const std::wstring& workspaceRoot);

// Deterministic S-1-4-x-y-1 capability SID derived from a private temp path
// (byte-compatible with tempWriteSid).
std::wstring tempWriteSid(const std::wstring& tempDir);

// Convert an SDDL SID string to a freshly LocalAlloc'd SID (caller frees).
PSID parseSid(const std::wstring& sddl);

// --- token.cpp --------------------------------------------------------------

// Build the write-restricted token for one mode. `writeSids` carries the
// capability SIDs for workspace-write (workspace SID + temp SID) and is empty
// for read-only. `outLogonSid`/`outWorldSid` receive the keep-alive SIDs the
// caller owns (LocalFree); the read-only default-DACL fallback names
// Everyone, matching the TS implementation.
HANDLE createRestrictedToken(Mode mode, const std::vector<PSID>& writeSids,
                             PSID& outLogonSid, PSID& outWorldSid,
                             bool writeRestricted = true);

// Merge a full-access ACE for `sid` into the token's default DACL so new
// objects (anonymous pipes etc.) the confined child creates pass the
// restricting-SID write check. Fails closed.
void setTokenDefaultDaclGrant(HANDLE token, PSID sid);

// --- acl.cpp ----------------------------------------------------------------

// Grant kGrantMask (OI|CI) to `sid` on `dir`, under a per-path lock. Skips the
// SetNamedSecurityInfoW apply when the exact ACE already stands (avoiding a
// repeat eager full-tree propagation). The directory must be caller-owned.
void grantWrite(const std::wstring& dir, PSID sid);

// Remove every ACE for `sid` from `dir`'s DACL (other entries preserved).
void revokeWrite(const std::wstring& dir, PSID sid);

// Harden the host process DACL: deny the sandbox's restricting SIDs (logon SID
// + Everyone) write-class process rights on the host — PROCESS_TERMINATE, VM
// write, thread creation, handle duplication, suspend/resume, etc. Deny ACEs
// precede the ambient Allows, so the child's WRITE_RESTRICTED pass-2 check
// fails for those rights. The host itself is unaffected (its pseudo-handle and
// own SID's full-control Allow ACE remain). Idempotent: when the exact Deny
// ACEs already stand, the apply is skipped. Fails closed: any Win32 failure
// throws before the child spawns.
void hardenHostProcessDacl(PSID logonSid, PSID worldSid);

// --- job.cpp ----------------------------------------------------------------

/** Resource limits for one confined run; 0/absent fields mean "unlimited". */
struct ResourceLimits {
  uint64_t memoryMb = 0;          // per-process commit limit
  uint64_t jobMemoryMb = 0;       // whole-job commit limit
  uint64_t cpuMs = 0;             // per-job CPU time (JOB_TIME)
  uint32_t cpuRatePercent = 0;    // CPU rate hard cap (Win8+; 0 = off)
  uint32_t maxProcesses = 0;      // active-process limit
  uint64_t wallClockMs = 0;       // wall-clock timeout, enforced by the launcher
  bool noUi = false;              // Job UI restrictions (handles/system/display/atoms)
  bool crashSilent = false;       // DIE_ON_UNHANDLED_EXCEPTION
  bool breakawayOk = false;       // allow children to break away from the job
};

/** Why a confined run ended, for the exit protocol. */
enum class LimitKind { None, Cpu, Memory, ProcessCount, Timeout, User };

/**
 * One confined run's job: created with the resource limits, associated with
 * an IOCP whose thread terminates the whole job on a hard-limit notification
 * (END_OF_JOB_TIME / PROCESS_MEMORY_LIMIT / JOB_MEMORY_LIMIT) — Windows does
 * NOT auto-kill on limit notifications, so the handling side must. Process
 * tree notifications (NEW_PROCESS / EXIT_PROCESS) are forwarded to the
 * installed callbacks (in-process consumers).
 */
class Job {
public:
  explicit Job(const ResourceLimits& limits);
  ~Job();
  Job(const Job&) = delete;
  Job& operator=(const Job&) = delete;

  HANDLE handle() const { return job_; }
  /** Assign a process to the job; fails closed (terminates the process). */
  void assign(HANDLE process);
  /** Terminate every process in the job. */
  void terminateAll(uint32_t exitCode);
  /** The limit that killed the job (thread-safe read). */
  LimitKind limitKind() const;
  /** True when the job was terminated by a hard limit. */
  bool limitHit() const;
  /** True when the CPU rate cap could not be applied (silent degradation). */
  bool cpuRateFailed() { return InterlockedCompareExchange(&cpuRateFailed_, 0, 0) != 0; }
  /** Stop the IOCP thread (join). */
  void stop();

  /** Invoked (IOCP thread) when a new process joins the job. */
  std::function<void(DWORD pid)> onNewProcess;
  /** Invoked (IOCP thread) when a process exits; abnormal = unhandled exception. */
  std::function<void(DWORD pid, DWORD exitCode, bool abnormal)> onExitProcess;

private:
  static DWORD WINAPI iocpThread(LPVOID param);
  void iocpLoop();
  void handleMessage(DWORD message, DWORD pid);

  HANDLE job_ = nullptr;
  HANDLE iocp_ = nullptr;
  HANDLE iocpThread_ = nullptr;
  volatile LONG limitKind_ = static_cast<LONG>(LimitKind::None);  // LimitKind
  mutable volatile LONG cpuRateFailed_ = 0;
  volatile LONG stopRequested_ = 0;
  std::unordered_set<DWORD> exitedPids_;  // IOCP thread only
};

// Create a plain kill-on-close job (no limits, no IOCP) — probe path.
HANDLE createKillOnCloseJob();

// --- spawn.cpp --------------------------------------------------------------

// Spawn `command` under `token` with the caller's stdio passed straight
// through: CREATE_SUSPENDED, assigned to `job`, then resumed. The child's
// environment inherits the runner's (which already carries the rewritten
// TMP/TEMP). Returns the child process handle (owned) and pid.
struct SpawnedChild {
  HANDLE process;
  DWORD pid;
};
SpawnedChild spawnSandboxedInherited(HANDLE token, HANDLE job, const std::wstring& command,
                                     const std::vector<std::wstring>& args, const std::wstring& cwd,
                                     bool newProcessGroup, const std::wstring* tempDir = nullptr,
                                  const std::map<std::wstring, std::wstring>* envOverrides = nullptr);

// Spawn under an external ConPTY handle (hpcon): the child's stdio is driven
// by the pseudo console, not by the launcher's handles. bInheritHandles=FALSE
// + EXTENDED_STARTUPINFO_PRESENT + the PSEUDOCONSOLE thread attribute; the
// child is assigned to the job immediately after the spawn (no SUSPENDED —
// a suspended primary thread fails DLL initialization under the pseudo
// console). `tempDir`, when non-null, redirects TMP/TEMP in the child's
// environment block.
SpawnedChild spawnSandboxedConPTY(HANDLE token, HANDLE job, HPCON hpcon,
                                  const std::wstring& command,
                                  const std::vector<std::wstring>& args,
                                  const std::wstring& cwd,
                                  const std::wstring* tempDir = nullptr,
                                  const std::map<std::wstring, std::wstring>* envOverrides = nullptr);

// --- util -------------------------------------------------------------------

// Quote one argv entry per CommandLineToArgvW rules (byte-compatible with the
// TS quoteArg).
std::wstring quoteArg(const std::wstring& argument);

}  // namespace winacl
