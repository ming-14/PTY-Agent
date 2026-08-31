// job.cpp — one confined run's job: resource limits + kill-on-close + IOCP
// notification thread that terminates the whole job on a hard-limit hit.
//
// Windows does NOT auto-terminate on Job limit notifications — an associated
// completion port only delivers the message — so the handling side must call
// TerminateJobObject. That is this thread's whole job; the launcher's main
// loop only waits on the primary process handle (wall-clock timeout included).
// The worker-thread model and message mapping follow the win-sandbox
// JobObjectImpl design, trimmed to the argv-wrapper shape: no per-pid handle
// caches, no process-tree reporting.
#include "winacl.h"

#include <stdexcept>

namespace winacl {
namespace {

constexpr ULONG_PTR kJobCompletionKey = 0x01;

void check(bool ok, const char* what) {
  if (!ok) {
    throw std::runtime_error(std::string(what) + " failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
}

}  // namespace

Job::Job(const ResourceLimits& limits) {
  job_ = CreateJobObjectW(nullptr, nullptr);
  check(job_ != nullptr, "CreateJobObjectW");
  try {
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION ext{};
  ext.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  if (limits.cpuMs > 0) {
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_TIME;
    // FILETIME 100ns units.
    ext.BasicLimitInformation.PerJobUserTimeLimit.QuadPart =
        static_cast<LONGLONG>(limits.cpuMs) * 10'000;
  }
  if (limits.memoryMb > 0) {
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY;
    ext.ProcessMemoryLimit = static_cast<SIZE_T>(limits.memoryMb) * 1024 * 1024;
  }
  if (limits.jobMemoryMb > 0) {
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY;
    ext.JobMemoryLimit = static_cast<SIZE_T>(limits.jobMemoryMb) * 1024 * 1024;
  }
  if (limits.maxProcesses > 0) {
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    ext.BasicLimitInformation.ActiveProcessLimit = limits.maxProcesses;
  }
  if (limits.breakawayOk) {
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK;
  }
  if (limits.crashSilent) {
    ext.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
  }
  check(SetInformationJobObject(job_, JobObjectExtendedLimitInformation, &ext, sizeof(ext)) != 0,
        "SetInformationJobObject(ExtendedLimitInformation)");

  if (limits.cpuRatePercent > 0) {
    // 0.01% units; HARD_CAP throttles instead of notifying. Win8+; a failure
    // degrades to no rate cap — recorded so the caller can tell (fail-open
    // would otherwise be silent).
    JOBOBJECT_CPU_RATE_CONTROL_INFORMATION cpu{};
    cpu.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP;
    cpu.CpuRate = limits.cpuRatePercent * 100;
    if (!SetInformationJobObject(job_, JobObjectCpuRateControlInformation, &cpu, sizeof(cpu))) {
      InterlockedExchange(&cpuRateFailed_, 1);
    }
  }

  if (limits.noUi) {
    JOBOBJECT_BASIC_UI_RESTRICTIONS ui{};
    ui.UIRestrictionsClass = JOB_OBJECT_UILIMIT_HANDLES | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS
        | JOB_OBJECT_UILIMIT_DISPLAYSETTINGS | JOB_OBJECT_UILIMIT_GLOBALATOMS;
    check(SetInformationJobObject(job_, JobObjectBasicUIRestrictions, &ui, sizeof(ui)) != 0,
          "SetInformationJobObject(BasicUIRestrictions)");
  }

  // Associate the completion port BEFORE any process enters the job.
  iocp_ = CreateIoCompletionPort(INVALID_HANDLE_VALUE, nullptr, 0, 0);
  check(iocp_ != nullptr, "CreateIoCompletionPort");
  JOBOBJECT_ASSOCIATE_COMPLETION_PORT associate{};
  associate.CompletionKey = reinterpret_cast<void*>(kJobCompletionKey);
  associate.CompletionPort = iocp_;
  check(SetInformationJobObject(job_, JobObjectAssociateCompletionPortInformation, &associate,
                                sizeof(associate)) != 0,
        "SetInformationJobObject(AssociateCompletionPort)");

  iocpThread_ = CreateThread(nullptr, 0, &Job::iocpThread, this, 0, nullptr);
  check(iocpThread_ != nullptr, "CreateThread(IOCP)");
  } catch (...) {
    // Partial construction: release what was created so far. The thread (if
    // started) is stopped via the sentinel; job/iocp handles close here.
    stop();
    if (job_ != nullptr) CloseHandle(job_);
    job_ = nullptr;
    throw;
  }
}

Job::~Job() {
  stop();
  if (job_ != nullptr) CloseHandle(job_);
}

void Job::assign(HANDLE process) {
  if (!AssignProcessToJobObject(job_, process)) {
    const DWORD err = GetLastError();
    TerminateProcess(process, 1);
    throw std::runtime_error("AssignProcessToJobObject failed (Win32 " + std::to_string(err) + ")");
  }
}

void Job::terminateAll(uint32_t exitCode) {
  if (job_ != nullptr) TerminateJobObject(job_, exitCode);
}

LimitKind Job::limitKind() const {
  return static_cast<LimitKind>(InterlockedCompareExchange(
      const_cast<volatile LONG*>(&limitKind_), 0, 0));
}

bool Job::limitHit() const {
  return limitKind() != LimitKind::None;
}

void Job::stop() {
  if (InterlockedExchange(&stopRequested_, 1) == 0 && iocp_ != nullptr) {
    PostQueuedCompletionStatus(iocp_, 0, 0, nullptr);  // wake the loop
  }
  if (iocpThread_ != nullptr) {
    WaitForSingleObject(iocpThread_, INFINITE);
    CloseHandle(iocpThread_);
    iocpThread_ = nullptr;
  }
  if (iocp_ != nullptr) {
    CloseHandle(iocp_);
    iocp_ = nullptr;
  }
}

DWORD WINAPI Job::iocpThread(LPVOID param) {
  static_cast<Job*>(param)->iocpLoop();
  return 0;
}

void Job::iocpLoop() {
  for (;;) {
    DWORD bytes = 0;
    ULONG_PTR key = 0;
    OVERLAPPED* overlapped = nullptr;
    if (!GetQueuedCompletionStatus(iocp_, &bytes, &key, &overlapped, INFINITE)) {
      continue;  // a failed wait is not a reason to die; the stop sentinel wakes us
    }
    if (key != kJobCompletionKey) {
      // The stop sentinel (key 0) — leave the loop when shutdown was asked.
      if (InterlockedCompareExchange(&stopRequested_, 0, 0) != 0) break;
      continue;
    }
    const DWORD message = bytes;
    const DWORD pid = static_cast<DWORD>(reinterpret_cast<uintptr_t>(overlapped));
    handleMessage(message, pid);
    if (InterlockedCompareExchange(&stopRequested_, 0, 0) != 0) break;
  }
}

void Job::handleMessage(DWORD message, DWORD pid) {
  switch (message) {
    case JOB_OBJECT_MSG_END_OF_JOB_TIME:
    case JOB_OBJECT_MSG_END_OF_PROCESS_TIME:
      if (InterlockedCompareExchange(&limitKind_, static_cast<LONG>(LimitKind::Cpu),
                                     static_cast<LONG>(LimitKind::None)) == static_cast<LONG>(LimitKind::None)) {
        terminateAll(1);
      }
      break;
    case JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT:
    case JOB_OBJECT_MSG_JOB_MEMORY_LIMIT:
      if (InterlockedCompareExchange(&limitKind_, static_cast<LONG>(LimitKind::Memory),
                                     static_cast<LONG>(LimitKind::None)) == static_cast<LONG>(LimitKind::None)) {
        terminateAll(1);
      }
      break;
    case JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT:
      // Creation-time rejection semantics: existing processes are not at
      // fault, so nothing is terminated; record the fact for the exit report.
      InterlockedCompareExchange(&limitKind_, static_cast<LONG>(LimitKind::ProcessCount),
                                 static_cast<LONG>(LimitKind::None));
      break;
    case JOB_OBJECT_MSG_NEW_PROCESS:
      
      if (onNewProcess) onNewProcess(pid);
      break;
    case JOB_OBJECT_MSG_EXIT_PROCESS:
    case JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS:
      // Dedup only guards the notification window; cap the set so a long-lived
      // job spawning many short children cannot grow it without bound.
      if (exitedPids_.size() > 8192) exitedPids_.clear();
      if (exitedPids_.insert(pid).second) {
        // Read the settled exit code (the process object may still be live for
        // a brief window after the exit notification).
        DWORD exitCode = 0;
        HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
        if (process != nullptr) {
          GetExitCodeProcess(process, &exitCode);
          CloseHandle(process);
        }
        if (onExitProcess) {
          
          onExitProcess(pid, exitCode, message == JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS);
        } else {
          
        }
      }
      break;
    default:
      break;
  }
}

HANDLE createKillOnCloseJob() {
  HANDLE job = CreateJobObjectW(nullptr, nullptr);
  if (job == nullptr) {
    throw std::runtime_error("CreateJobObjectW failed (Win32 " + std::to_string(GetLastError()) + ")");
  }
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION info{};
  info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info, sizeof(info))) {
    const DWORD err = GetLastError();
    CloseHandle(job);
    throw std::runtime_error("SetInformationJobObject(KILL_ON_JOB_CLOSE) failed (Win32 " + std::to_string(err) + ")");
  }
  return job;
}

}  // namespace winacl
