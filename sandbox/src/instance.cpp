// instance.cpp — in-process sandbox instance implementation.
#include "instance.h"

#include <shellapi.h>

#include <algorithm>
#include <cstdio>
#include <stdexcept>

#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "advapi32.lib")

namespace winacl {
namespace {

// Split a Windows command line via CommandLineToArgvW (exact CreateProcess
// semantics, including backslash-quote escapes and empty arguments).
std::vector<std::wstring> splitCommandLine(const std::wstring& commandLine) {
  int argc = 0;
  wchar_t** argv = CommandLineToArgvW(commandLine.c_str(), &argc);
  if (argv == nullptr) {
    throw std::runtime_error("CommandLineToArgvW failed");
  }
  std::vector<std::wstring> out;
  try {
    out.reserve(static_cast<size_t>(argc));
    for (int i = 0; i < argc; i++) out.emplace_back(argv[i]);
  } catch (...) {
    LocalFree(argv);
    throw;
  }
  LocalFree(argv);
  return out;
}

std::string reasonFor(bool userTerminated, bool timedOut, LimitKind kind) {
  if (userTerminated) return "user";
  if (timedOut) return "timeout";
  return limitKindString(kind);
}

}  // namespace

// ---------------------------------------------------------------------------
// SandboxedProcess
// ---------------------------------------------------------------------------

SandboxedProcess::SandboxedProcess(SpawnedChild child, std::unique_ptr<Job> job,
                                   HANDLE token, std::wstring tempDir,
                                   bool selfManagedTemp, uint64_t wallClockMs)
    : child_(child), job_(std::move(job)), token_(token),
      tempDir_(std::move(tempDir)), selfManagedTemp_(selfManagedTemp) {
  // Bridge the Job's IOCP notifications to this process's (locked) callbacks.
  job_->onNewProcess = [this](DWORD pid) { notifyStarted(pid); };
  job_->onExitProcess = [this](DWORD pid, DWORD code, bool abnormal) {
    notifyExited(pid, code, abnormal);
  };
  if (wallClockMs > 0) {
    wallClockEvent_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    wallClockThread_ = std::thread([this, wallClockMs] {
      const DWORD ms = static_cast<DWORD>(std::min<uint64_t>(wallClockMs, MAXDWORD));
      if (WaitForSingleObject(wallClockEvent_, ms) == WAIT_TIMEOUT) {
        if (InterlockedCompareExchange(&timedOut_, 1, 0) == 0) {
          job_->terminateAll(1);
        }
      }
    });
  }
}

SandboxedProcess::~SandboxedProcess() {
  dispose();
}

void SandboxedProcess::setCallbacks(std::function<void(DWORD)> started,
                                    std::function<void(DWORD, DWORD, bool)> exited) {
  std::lock_guard<std::mutex> lock(cbMutex_);
  onProcessStarted_ = std::move(started);
  onProcessExited_ = std::move(exited);
}

void SandboxedProcess::clearCallbacks() {
  std::lock_guard<std::mutex> lock(cbMutex_);
  onProcessStarted_ = nullptr;
  onProcessExited_ = nullptr;
}

void SandboxedProcess::notifyStarted(DWORD pid) {
  std::function<void(DWORD)> fn;
  {
    std::lock_guard<std::mutex> lock(cbMutex_);
    fn = onProcessStarted_;
  }
  if (fn) fn(pid);
}

void SandboxedProcess::notifyExited(DWORD pid, DWORD exitCode, bool abnormal) {
  std::function<void(DWORD, DWORD, bool)> fn;
  {
    std::lock_guard<std::mutex> lock(cbMutex_);
    fn = onProcessExited_;
  }
  if (fn) fn(pid, exitCode, abnormal);
}

std::pair<uint32_t, std::string> SandboxedProcess::wait() {
  if (disposed_) throw std::runtime_error("sandbox process already disposed");
  if (!exited_) {
    WaitForSingleObject(child_.process, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(child_.process, &code);
    exitCode_ = code;
    const bool user = InterlockedCompareExchange(&userTerminated_, 0, 0) != 0;
    const bool timedOut = InterlockedCompareExchange(&timedOut_, 0, 0) != 0;
    const LimitKind kind = job_->limitKind();
    // Priority: user termination > hard limit > watchdog timeout. A natural
    // exit wins over a watchdog that raced the exit (code is settled then).
    if (user) exitReason_ = "user";
    else if (kind != LimitKind::None) exitReason_ = limitKindString(kind);
    else if (timedOut) exitReason_ = "timeout";
    else exitReason_ = "normal";
    exited_ = true;
  }
  return {exitCode_, exitReason_};
}

std::optional<std::pair<uint32_t, std::string>> SandboxedProcess::pollExit() {
  if (disposed_) throw std::runtime_error("sandbox process already disposed");
  if (!exited_) {
    if (WaitForSingleObject(child_.process, 0) != WAIT_OBJECT_0) {
      return std::nullopt;
    }
    DWORD code = 0;
    GetExitCodeProcess(child_.process, &code);
    exitCode_ = code;
    const bool user = InterlockedCompareExchange(&userTerminated_, 0, 0) != 0;
    const bool timedOut = InterlockedCompareExchange(&timedOut_, 0, 0) != 0;
    const LimitKind kind = job_->limitKind();
    if (user) exitReason_ = "user";
    else if (kind != LimitKind::None) exitReason_ = limitKindString(kind);
    else if (timedOut) exitReason_ = "timeout";
    else exitReason_ = "normal";
    exited_ = true;
  }
  return std::pair<uint32_t, std::string>{exitCode_, exitReason_};
}

void SandboxedProcess::terminate(uint32_t exitCode) {
  if (disposed_) throw std::runtime_error("sandbox process already disposed");
  InterlockedExchange(&userTerminated_, 1);
  job_->terminateAll(exitCode);
}

bool SandboxedProcess::signalCtrlBreak() {
  if (disposed_) return false;
  return GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, child_.pid) != 0;
}

std::vector<DWORD> SandboxedProcess::queryProcessList() {
  if (disposed_) return {};
  std::vector<DWORD> out;
  JOBOBJECT_BASIC_PROCESS_ID_LIST probe{};
  DWORD needed = 0;
  QueryInformationJobObject(job_->handle(), JobObjectBasicProcessIdList, &probe,
                            sizeof(probe), &needed);
  if (probe.NumberOfProcessIdsInList == 0) return out;
  const DWORD count = probe.NumberOfAssignedProcesses;
  std::vector<uint8_t> buf(sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST)
                           + (count > 1 ? count - 1 : 0) * sizeof(ULONG_PTR));
  auto* list = reinterpret_cast<JOBOBJECT_BASIC_PROCESS_ID_LIST*>(buf.data());
  for (int attempt = 0; attempt < 8; attempt++) {
    if (!QueryInformationJobObject(job_->handle(), JobObjectBasicProcessIdList, list,
                                   static_cast<DWORD>(buf.size()), &needed)) {
      const DWORD err = GetLastError();
      if (err == ERROR_MORE_DATA && needed > buf.size()) {
        buf.resize(needed);
        continue;
      }
      break;
    }
    for (DWORD i = 0; i < list->NumberOfProcessIdsInList; i++) {
      out.push_back(static_cast<DWORD>(list->ProcessIdList[i]));
    }
    break;
  }
  return out;
}

std::pair<uint32_t, bool> SandboxedProcess::queryProcessExitCode(DWORD pid) {
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (process == nullptr) return {0, false};
  DWORD code = 0;
  const bool ok = GetExitCodeProcess(process, &code) != 0;
  CloseHandle(process);
  if (!ok) return {0, false};
  return {code, code == STILL_ACTIVE};
}

void SandboxedProcess::dispose() {
  if (disposed_.exchange(true)) return;  // idempotent
  // Wake and join the wall-clock watchdog before the object goes away.
  if (wallClockThread_.joinable()) {
    if (wallClockEvent_ != nullptr) SetEvent(wallClockEvent_);
    wallClockThread_.join();
  }
  if (wallClockEvent_ != nullptr) {
    CloseHandle(wallClockEvent_);
    wallClockEvent_ = nullptr;
  }
  clearCallbacks();  // no IOCP invocation may touch Python callbacks anymore
  if (job_ != nullptr) {
    // Kill the tree first, then let the primary process settle so its handle
    // is not closed while a concurrent wait() is blocked on it.
    job_->terminateAll(1);
    if (child_.process != nullptr) {
      WaitForSingleObject(child_.process, 3000);
    }
    job_->stop();  // join the IOCP thread before any callback target dies
    job_.reset();
  }
  if (token_ != nullptr) {
    CloseHandle(token_);
    token_ = nullptr;
  }
  if (child_.process != nullptr) {
    CloseHandle(child_.process);
    child_.process = nullptr;
  }
  if (selfManagedTemp_ && !tempDir_.empty()) {
    PSID sid = nullptr;
    try {
      const std::wstring tempSid = tempWriteSid(tempDir_);
      sid = parseSid(tempSid);
      revokeWrite(tempDir_, sid);
    } catch (...) {
      // Best-effort: an unreachable temp grant is inert residue.
    }
    if (sid != nullptr) LocalFree(sid);
    // std::filesystem::remove_all is unavailable here without extra includes;
    // use the Windows API directly.
    const std::wstring pattern = tempDir_ + L"\\*";
    WIN32_FIND_DATAW fd{};
    HANDLE find = FindFirstFileW(pattern.c_str(), &fd);
    if (find != INVALID_HANDLE_VALUE) {
      do {
        DeleteFileW((tempDir_ + L"\\" + fd.cFileName).c_str());
        RemoveDirectoryW((tempDir_ + L"\\" + fd.cFileName).c_str());
      } while (FindNextFileW(find, &fd));
      FindClose(find);
    }
    RemoveDirectoryW(tempDir_.c_str());
    tempDir_.clear();
  }
}

// ---------------------------------------------------------------------------
// SandboxInstance
// ---------------------------------------------------------------------------

std::shared_ptr<SandboxedProcess> SandboxInstance::startProcess(
    const std::wstring& commandLine, const std::wstring& workingDir,
    bool workspaceWrite, const ResourceLimits& limits, HPCON hpcon,
    const std::map<std::wstring, std::wstring>* envOverrides) {
  const std::vector<std::wstring> argv = splitCommandLine(commandLine);
  if (argv.empty()) throw std::runtime_error("empty command line");
  if (!workingDir.empty()) {
    const DWORD attrs = GetFileAttributesW(workingDir.c_str());
    if (attrs == INVALID_FILE_ATTRIBUTES || (attrs & FILE_ATTRIBUTE_DIRECTORY) == 0) {
      throw std::runtime_error("working directory does not exist (Win32 "
                               + std::to_string(GetLastError()) + ")");
    }
  }
  const Mode mode = workspaceWrite ? Mode::WorkspaceWrite : Mode::ReadOnly;

  // Materialize the workspace capability (standing) once per workspace.
  // The workspace path is normalized (no trailing separator, canonical case)
  // so the SID derivation matches the grant regardless of how the caller
  // spelled the directory.
  std::wstring wsPath = workingDir;
  while (wsPath.size() > 3 && (wsPath.back() == L'\\' || wsPath.back() == L'/')) {
    wsPath.pop_back();
  }
  const std::wstring workspaceSid = workspaceWriteSid(wsPath);
  if (workspaceWrite && grantedWorkspaces_.insert(wsPath).second) {
    PSID sid = parseSid(workspaceSid);
    try {
      grantWrite(wsPath, sid);
    } catch (...) {
      grantedWorkspaces_.erase(wsPath);  // failed materialization: retry next time
      LocalFree(sid);
      throw;
    }
    LocalFree(sid);
  }

  // Private temp directory + its revocable capability (workspace-write only).
  std::wstring tempDir;
  PSID tempSidPtr = nullptr;
  bool selfManagedTemp = false;
  if (workspaceWrite) {
    wchar_t tmpPath[MAX_PATH + 1] = {};
    if (GetTempPathW(MAX_PATH + 1, tmpPath) == 0) {
      throw std::runtime_error("GetTempPathW failed");
    }
    std::wstring root = tmpPath;
    for (int attempt = 0; attempt < 16; attempt++) {
      wchar_t name[16] = {};
      swprintf_s(name, L"dsh-%04x", GetTickCount() ^ (GetCurrentProcessId() << 8) ^ attempt);
      const std::wstring candidate = root + name;
      if (CreateDirectoryW(candidate.c_str(), nullptr) != 0 || GetLastError() == ERROR_ALREADY_EXISTS) {
        tempDir = candidate;
        break;
      }
    }
    if (tempDir.empty()) throw std::runtime_error("failed to create a private temp directory");
    const std::wstring tempSid = tempWriteSid(tempDir);
    tempSidPtr = parseSid(tempSid);
    try {
      grantWrite(tempDir, tempSidPtr);
    } catch (...) {
      LocalFree(tempSidPtr);
      RemoveDirectoryW(tempDir.c_str());
      throw;
    }
    selfManagedTemp = true;
  }

  // Restricted token (workspace-write carries both capability SIDs).
  std::vector<PSID> writeSids;
  PSID wsSidPtr = nullptr;
  PSID logonSid = nullptr;
  PSID worldSid = nullptr;
  HANDLE token = nullptr;
  std::unique_ptr<Job> job;
  try {
    if (workspaceWrite) {
      wsSidPtr = parseSid(workspaceSid);
      writeSids.push_back(wsSidPtr);
      if (tempSidPtr != nullptr) writeSids.push_back(tempSidPtr);
    }
    // ConPTY mode keeps WRITE_RESTRICTED when workspace-write: the previous
    // degradation (write whitelist silently off) was a security hole. The
    // DLL-init failures were caused by the ambient temp root being denied;
    // the explicit per-run TMP/TEMP override (env block below) fixes that
    // without weakening the mechanism.
    token = createRestrictedToken(mode, writeSids, logonSid, worldSid, true);
    setTokenDefaultDaclGrant(token, tempSidPtr != nullptr ? tempSidPtr
                                : wsSidPtr != nullptr ? wsSidPtr : worldSid);
    // 加固宿主进程 DACL：拒绝沙箱 restricting SIDs（logon SID + Everyone）对
    // 宿主获得进程写类权限（PROCESS_TERMINATE 等）。fail-closed：失败即抛错，
    // 子进程绝不带洞 spawn。
    hardenHostProcessDacl(logonSid, worldSid);
    LocalFree(logonSid);
    LocalFree(worldSid);
    logonSid = nullptr;
    worldSid = nullptr;
    job = std::make_unique<Job>(limits);
  } catch (...) {
    if (logonSid != nullptr) LocalFree(logonSid);
    if (worldSid != nullptr) LocalFree(worldSid);
    if (wsSidPtr != nullptr) LocalFree(wsSidPtr);
    if (tempSidPtr != nullptr) {
      try {
        revokeWrite(tempDir, tempSidPtr);
      } catch (...) {
      }
      LocalFree(tempSidPtr);
      tempSidPtr = nullptr;
    }
    if (token != nullptr) CloseHandle(token);
    if (!tempDir.empty()) RemoveDirectoryW(tempDir.c_str());
    if (workspaceWrite && grantedWorkspaces_.count(wsPath)) {
      grantedWorkspaces_.erase(wsPath);  // failed materialization: not granted
    }
    throw;
  }

  // Spawn (ConPTY path when an HPCON is supplied; the handle is valid in this
  // process by construction). The child environment is built explicitly with
  // TMP/TEMP pointing at the granted private temp directory: the child's DLL
  // initialization writes temporary files, and the WRITE_RESTRICTED
  // intersection would deny the ambient temp root. The host environment is
  // never modified (concurrent startProcess calls cannot race).
  SpawnedChild child;
  try {
    if (hpcon != nullptr) {
      child = spawnSandboxedConPTY(token, job->handle(), hpcon, argv[0],
                                   std::vector<std::wstring>(argv.begin() + 1, argv.end()),
                                   workingDir, tempDir.empty() ? nullptr : &tempDir, envOverrides);
    } else {
      child = spawnSandboxedInherited(token, job->handle(), argv[0],
                                      std::vector<std::wstring>(argv.begin() + 1, argv.end()),
                                      workingDir, true, tempDir.empty() ? nullptr : &tempDir, envOverrides);
    }
  } catch (...) {
    if (tempSidPtr != nullptr) {
      try {
        revokeWrite(tempDir, tempSidPtr);
      } catch (...) {
      }
      LocalFree(tempSidPtr);
      tempSidPtr = nullptr;
    }
    if (!tempDir.empty()) RemoveDirectoryW(tempDir.c_str());
    throw;
  }

  auto process = std::make_shared<SandboxedProcess>(child, std::move(job), token,
                                                    tempDir, selfManagedTemp,
                                                    limits.wallClockMs);
  if (tempSidPtr != nullptr) LocalFree(tempSidPtr);
  if (wsSidPtr != nullptr) LocalFree(wsSidPtr);
  processes_.push_back(process);
  return process;
}

void SandboxInstance::shutdown() {
  for (auto& process : processes_) {
    process->dispose();
  }
  processes_.clear();
}

}  // namespace winacl
