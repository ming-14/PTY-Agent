// instance.h — in-process sandbox instance and per-process handle.
//
// The pybind11-facing core: SandboxInstance materializes the workspace/temp
// capability grants, builds the WRITE_RESTRICTED token, and spawns confined
// processes under a Job with optional resource limits. SandboxedProcess owns
// one confined run's handles (process/token/job/private temp) and answers
// wait/terminate/signal/process-tree queries in-process — no IPC, no pipes,
// no protocol lines. An external HPCON is usable directly because the pseudo
// console is created by the host process (the same process that calls
// CreateProcessAsUserW).
#pragma once

#include "winacl.h"

#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <thread>
#include <unordered_map>
#include <vector>

namespace winacl {

/** Why a confined run ended (string form for the Python layer). */
inline const char* limitKindString(LimitKind kind) {
  switch (kind) {
    case LimitKind::None: return "normal";
    case LimitKind::Cpu: return "cpu_limit";
    case LimitKind::Memory: return "memory_limit";
    case LimitKind::ProcessCount: return "process_count_limit";
    case LimitKind::Timeout: return "timeout";
    case LimitKind::User: return "user";
  }
  return "unknown";
}

/**
 * One confined run: the spawned primary process plus its job, token, private
 * temp directory, and settled exit facts. The job's IOCP thread delivers
 * process-tree notifications through the installed callbacks.
 *
 * Ownership: SandboxInstance keeps a shared_ptr per process; PyProcess holds
 * another. The object survives shutdown (disposed) so Python wrappers never
 * dangle. Callbacks are installed/cleared under cbMutex_ (the IOCP thread
 * reads them under the same lock); PyProcess clears them in its destructor
 * (which runs under the GIL), so a GC'd wrapper cannot be invoked after death.
 */
class SandboxedProcess {
public:
  SandboxedProcess(SpawnedChild child, std::unique_ptr<Job> job, HANDLE token,
                   std::wstring tempDir, bool selfManagedTemp, uint64_t wallClockMs);
  ~SandboxedProcess();
  SandboxedProcess(const SandboxedProcess&) = delete;
  SandboxedProcess& operator=(const SandboxedProcess&) = delete;

  DWORD pid() const { return child_.pid; }
  bool disposed() const { return disposed_.load(); }

  /** Install the process-tree callbacks (thread-safe vs IOCP thread). */
  void setCallbacks(std::function<void(DWORD)> started,
                    std::function<void(DWORD, DWORD, bool)> exited);
  /** Clear the callbacks (called from the PyProcess destructor). */
  void clearCallbacks();

  /** Block until the primary process exits; returns (exit code, reason). */
  std::pair<uint32_t, std::string> wait();
  /** Non-blocking probe: settled (exit code, reason) or nullopt while running. */
  std::optional<std::pair<uint32_t, std::string>> pollExit();
  /** Terminate the whole job (reason = user). */
  void terminate(uint32_t exitCode = 1);
  /** Send CTRL_BREAK to the child's process group (requires process-group head). */
  bool signalCtrlBreak();
  /** Live process ids in the job. */
  std::vector<DWORD> queryProcessList();
  /** (exit code, still active) for one job pid. */
  std::pair<uint32_t, bool> queryProcessExitCode(DWORD pid);
  /** Release handles, revoke the self-managed temp grant, remove the temp dir.
   *  Idempotent; after this the object answers no live queries. */
  void dispose();

private:
  void notifyStarted(DWORD pid);
  void notifyExited(DWORD pid, DWORD exitCode, bool abnormal);

  std::mutex cbMutex_;
  std::function<void(DWORD)> onProcessStarted_;
  std::function<void(DWORD, DWORD, bool)> onProcessExited_;
  std::atomic<bool> disposed_{false};
  bool exited_ = false;
  uint32_t exitCode_ = 0;
  std::string exitReason_ = "normal";
  volatile LONG userTerminated_ = 0;
  volatile LONG timedOut_ = 0;
  HANDLE wallClockEvent_ = nullptr;
  std::thread wallClockThread_;
  SpawnedChild child_;
  std::unique_ptr<Job> job_;
  HANDLE token_ = nullptr;
  std::wstring tempDir_;
  bool selfManagedTemp_ = false;
};

/**
 * One sandbox: owns the per-workspace standing grants, the live processes,
 * and their private-temp grants. startProcess materializes the capabilities,
 * builds the restricted token, and spawns; shutdown/dispose terminates every
 * live process, revokes the revocable temp grants, and removes the private
 * temp directories (workspace ACEs stand — the reuse cache).
 */
class SandboxInstance {
public:
  SandboxInstance() = default;
  ~SandboxInstance() { shutdown(); }
  SandboxInstance(const SandboxInstance&) = delete;
  SandboxInstance& operator=(const SandboxInstance&) = delete;

  /**
   * Spawn one confined process.
   * @param commandLine - full command line (CommandLineToArgvW split inside).
   * @param workingDir - workspace root (must exist; becomes the workspace).
   * @param workspaceWrite - true = workspace-write, false = read-only.
   * @param limits - resource limits (zeros = unlimited).
   * @param hpcon - external pseudo console handle, or nullptr for pipe stdio.
   * @returns the process object (shared ownership: instance + PyProcess).
   */
  std::shared_ptr<SandboxedProcess> startProcess(const std::wstring& commandLine,
                                                 const std::wstring& workingDir,
                                                 bool workspaceWrite,
                                                 const ResourceLimits& limits,
                                                 HPCON hpcon = nullptr,
                                                 const std::map<std::wstring, std::wstring>* envOverrides = nullptr);
  /** Dispose all processes, revoke temp grants, remove temp dirs. */
  void shutdown();

private:
  std::set<std::wstring> grantedWorkspaces_;
  std::vector<std::shared_ptr<SandboxedProcess>> processes_;
};

}  // namespace winacl
