// spawn.cpp — spawn the confined child under the restricted token.
//
// Both spawn paths build the child's environment block explicitly from the
// host environment with TMP/TEMP redirected to the granted private temp
// directory (when supplied) — the host environment is never modified, so
// concurrent spawns cannot race. The inherited path additionally whitelists
// exactly the three std handles via PROC_THREAD_ATTRIBUTE_HANDLE_LIST so no
// stray inheritable handle leaks into the confined child.
#include "winacl.h"

#include <wincon.h>
#include <cwchar>
#include <map>
#include <stdexcept>
#include <vector>

namespace winacl {
namespace {

// Copy the host environment block, replacing TMP/TEMP with the private temp
// directory when one is supplied. Returns a double-NUL-terminated block.
std::vector<wchar_t> buildEnvBlock(const std::wstring* tempDir,
                                       const std::map<std::wstring, std::wstring>* overrides) {
  struct EnvGuard {
    LPWCH env;
    ~EnvGuard() { FreeEnvironmentStringsW(env); }
  };
  LPWCH env = GetEnvironmentStringsW();
  if (env == nullptr) {
    throw std::runtime_error("GetEnvironmentStringsW failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }
  EnvGuard guard{env};
  std::vector<wchar_t> out;
  for (LPWCH p = env; *p != L'\0'; p += wcslen(p) + 1) {
    const std::wstring entry(p);
    const bool isTmp = entry.rfind(L"TMP=", 0) == 0 || entry.rfind(L"TEMP=", 0) == 0;
    if (tempDir != nullptr && isTmp) {
      continue;  // replaced below (both TMP and TEMP point at the same dir)
    }
    out.insert(out.end(), p, p + wcslen(p) + 1);
  }
  if (tempDir != nullptr) {
    const std::wstring tmp = L"TMP=" + *tempDir;
    const std::wstring temp = L"TEMP=" + *tempDir;
    out.insert(out.end(), tmp.begin(), tmp.end());
    out.push_back(L'\0');
    out.insert(out.end(), temp.begin(), temp.end());
    out.push_back(L'\0');
  }
  if (overrides != nullptr) {
    for (const auto& [key, value] : *overrides) {
      const std::wstring entry = key + L"=" + value;
      out.insert(out.end(), entry.begin(), entry.end());
      out.push_back(L'\0');
    }
  }
  out.push_back(L'\0');
  return out;
}

}  // namespace

std::wstring quoteArg(const std::wstring& argument) {
  if (argument.empty()) return L"\"\"";
  const bool needsQuotes = argument.find_first_of(L" \t\n\v\f\r\"") != std::wstring::npos;
  if (!needsQuotes) return argument;
  std::wstring quoted = L"\"";
  size_t i = 0;
  while (i < argument.size()) {
    size_t backslashes = 0;
    while (i < argument.size() && argument[i] == L'\\') {
      backslashes++;
      i++;
    }
    if (i == argument.size()) {
      quoted.append(backslashes * 2, L'\\');
    } else if (argument[i] == L'"') {
      quoted.append(backslashes * 2 + 1, L'\\');
      quoted.push_back(L'"');
      i++;
    } else {
      quoted.append(backslashes, L'\\');
      quoted.push_back(argument[i]);
      i++;
    }
  }
  quoted.push_back(L'"');
  return quoted;
}

static std::wstring buildCommandLine(const std::wstring& command,
                                     const std::vector<std::wstring>& args) {
  std::wstring commandLine = quoteArg(command);
  for (const std::wstring& arg : args) {
    commandLine += L" " + quoteArg(arg);
  }
  return commandLine;
}

SpawnedChild spawnSandboxedInherited(HANDLE token, HANDLE job, const std::wstring& command,
                                     const std::vector<std::wstring>& args, const std::wstring& cwd,
                                     bool newProcessGroup, const std::wstring* tempDir,
                                     const std::map<std::wstring, std::wstring>* envOverrides) {
  const std::wstring commandLine = buildCommandLine(command, args);

  // Re-enable inheritance on the std handles for the duration of the spawn
  // and pass them explicitly via STARTF_USESTDHANDLES; the HANDLE_LIST
  // attribute restricts what the child inherits to exactly these three.
  HANDLE stdIn = GetStdHandle(STD_INPUT_HANDLE);
  HANDLE stdOut = GetStdHandle(STD_OUTPUT_HANDLE);
  HANDLE stdErr = GetStdHandle(STD_ERROR_HANDLE);
  if (stdIn == nullptr || stdIn == INVALID_HANDLE_VALUE || stdOut == nullptr
      || stdOut == INVALID_HANDLE_VALUE || stdErr == nullptr || stdErr == INVALID_HANDLE_VALUE) {
    throw std::runtime_error("GetStdHandle returned an invalid handle");
  }
  struct InheritGuard {
    HANDLE in, out, err;
    ~InheritGuard() {
      SetHandleInformation(in, HANDLE_FLAG_INHERIT, 0);
      SetHandleInformation(out, HANDLE_FLAG_INHERIT, 0);
      SetHandleInformation(err, HANDLE_FLAG_INHERIT, 0);
    }
  };
  if (!SetHandleInformation(stdIn, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
      || !SetHandleInformation(stdOut, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
      || !SetHandleInformation(stdErr, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)) {
    throw std::runtime_error("SetHandleInformation(inherit) failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }
  InheritGuard inheritGuard{stdIn, stdOut, stdErr};

  // Attribute list: HANDLE_LIST whitelist (std handles only). The attribute
  // list must be allocated before STARTUPINFOEXW is filled.
  SIZE_T attrSize = 0;
  InitializeProcThreadAttributeList(nullptr, 1, 0, &attrSize);  // expected ERROR_INSUFFICIENT_BUFFER
  std::vector<uint8_t> attrBuf(attrSize);
  LPPROC_THREAD_ATTRIBUTE_LIST attrList =
      reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attrBuf.data());
  if (!InitializeProcThreadAttributeList(attrList, 1, 0, &attrSize)) {
    throw std::runtime_error("InitializeProcThreadAttributeList failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }
  struct AttrGuard {
    LPPROC_THREAD_ATTRIBUTE_LIST list;
    ~AttrGuard() { DeleteProcThreadAttributeList(list); }
  } attrGuard{attrList};
  HANDLE handles[] = {stdIn, stdOut, stdErr};
  if (!UpdateProcThreadAttribute(attrList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                 handles, sizeof(handles), nullptr, nullptr)) {
    throw std::runtime_error("UpdateProcThreadAttribute(HANDLE_LIST) failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }

  STARTUPINFOEXW siex{};
  siex.StartupInfo.cb = sizeof(siex);
  siex.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
  siex.StartupInfo.hStdInput = stdIn;
  siex.StartupInfo.hStdOutput = stdOut;
  siex.StartupInfo.hStdError = stdErr;
  siex.lpAttributeList = attrList;

  const std::vector<wchar_t> envBlock = buildEnvBlock(tempDir, envOverrides);

  PROCESS_INFORMATION pi{};
  // CREATE_NEW_PROCESS_GROUP: makes the child the head of its own process
  // group so control-channel ctrl_break can be directed at it (a confined
  // child shares the host console).
  const DWORD creationFlags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
                              | EXTENDED_STARTUPINFO_PRESENT
                              | (newProcessGroup ? CREATE_NEW_PROCESS_GROUP : 0);
  const BOOL created = CreateProcessAsUserW(
      token, nullptr, const_cast<LPWSTR>(commandLine.c_str()), nullptr, nullptr, TRUE,
      creationFlags, const_cast<wchar_t*>(envBlock.data()), cwd.empty() ? nullptr : cwd.c_str(),
      &siex.StartupInfo, &pi);

  if (!created) {
    throw std::runtime_error("CreateProcessAsUserW failed (Win32 " + std::to_string(GetLastError())
                             + ") for command: " + std::string(commandLine.begin(), commandLine.end()));
  }

  // The child is suspended and NOT yet in the kill-on-close job: any failure
  // from here must terminate it so it cannot hang or run unconfined.
  if (!AssignProcessToJobObject(job, pi.hProcess)) {
    const DWORD err = GetLastError();
    TerminateProcess(pi.hProcess, 1);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    throw std::runtime_error("AssignProcessToJobObject failed (Win32 " + std::to_string(err) + ")");
  }
  if (ResumeThread(pi.hThread) == static_cast<DWORD>(-1)) {
    const DWORD err = GetLastError();
    TerminateProcess(pi.hProcess, 1);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    throw std::runtime_error("ResumeThread failed (Win32 " + std::to_string(err) + ")");
  }
  CloseHandle(pi.hThread);
  return SpawnedChild{pi.hProcess, pi.dwProcessId};
}

SpawnedChild spawnSandboxedConPTY(HANDLE token, HANDLE job, HPCON hpcon,
                                  const std::wstring& command,
                                  const std::vector<std::wstring>& args,
                                  const std::wstring& cwd, const std::wstring* tempDir,
                                  const std::map<std::wstring, std::wstring>* envOverrides) {
  const std::wstring commandLine = buildCommandLine(command, args);

  // Attribute list with exactly the PSEUDOCONSOLE attribute (lpValue is the
  // HPCON value itself, not its address). The child's stdio is allocated by
  // the ConPTY host; STARTF_USESTDHANDLES must still be set with NULL
  // handles or the stdio copy path is not activated and the pseudo console
  // is ignored under CreateProcessAsUserW (win-sandbox's empirical finding).
  SIZE_T attrSize = 0;
  InitializeProcThreadAttributeList(nullptr, 1, 0, &attrSize);  // expected ERROR_INSUFFICIENT_BUFFER
  std::vector<uint8_t> attrBuf(attrSize);
  LPPROC_THREAD_ATTRIBUTE_LIST attrList =
      reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attrBuf.data());
  if (!InitializeProcThreadAttributeList(attrList, 1, 0, &attrSize)) {
    throw std::runtime_error("InitializeProcThreadAttributeList failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }
  struct AttrGuard {
    LPPROC_THREAD_ATTRIBUTE_LIST list;
    ~AttrGuard() { DeleteProcThreadAttributeList(list); }
  } guard{attrList};
  if (!UpdateProcThreadAttribute(attrList, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                 hpcon, sizeof(HPCON), nullptr, nullptr)) {
    throw std::runtime_error("UpdateProcThreadAttribute(PSEUDOCONSOLE) failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }

  STARTUPINFOEXW siex{};
  siex.StartupInfo.cb = sizeof(siex);
  siex.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
  siex.lpAttributeList = attrList;

  // Explicit environment block with the private temp override (see
  // buildEnvBlock); lpEnvironment=NULL is unreliable for DLL initialization
  // under the restricted token + pseudo console.
  const std::vector<wchar_t> envBlock = buildEnvBlock(tempDir, envOverrides);

  PROCESS_INFORMATION pi{};
  // CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT; no
  // CREATE_NO_WINDOW (ConPTY hosts conhost headless) and no
  // CREATE_NEW_PROCESS_GROUP (the pseudo console forwards Ctrl+C itself).
  // NO CREATE_SUSPENDED: a suspended primary thread fails DLL initialization
  // under the pseudo console (STATUS_DLL_INIT_FAILED, 0xC0000142) — the job
  // assignment follows immediately after the spawn instead (win-sandbox's
  // ConPTY branch has the same shape).
  const BOOL created = CreateProcessAsUserW(
      token, nullptr, const_cast<LPWSTR>(commandLine.c_str()), nullptr, nullptr, FALSE,
      CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
      const_cast<wchar_t*>(envBlock.data()), cwd.empty() ? nullptr : cwd.c_str(), &siex.StartupInfo, &pi);
  if (!created) {
    throw std::runtime_error("CreateProcessAsUserW(ConPTY) failed (Win32 "
                             + std::to_string(GetLastError()) + ")");
  }

  // Assign immediately after the spawn — the child may briefly run unconfined
  // before the assignment (the OJ-acceptable window win-sandbox documents).
  if (!AssignProcessToJobObject(job, pi.hProcess)) {
    const DWORD err = GetLastError();
    TerminateProcess(pi.hProcess, 1);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    throw std::runtime_error("AssignProcessToJobObject failed (Win32 " + std::to_string(err) + ")");
  }
  CloseHandle(pi.hThread);
  return SpawnedChild{pi.hProcess, pi.dwProcessId};
}

}  // namespace winacl
