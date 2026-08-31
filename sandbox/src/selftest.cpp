// selftest.cpp — standalone C++ check of SandboxInstance/SandboxedProcess
// (no pybind11 involved), to isolate binding-layer crashes from core bugs.
#include "instance.h"

#include <cstdio>
#include <windows.h>

int wmain() {
  using namespace winacl;
  try {
    SandboxInstance sb;
    wchar_t tmpPath[MAX_PATH + 1] = {};
    GetTempPathW(MAX_PATH, tmpPath);
    std::wstring workdir = tmpPath;
    workdir += L"sandbox-inprocess-smoke";

    auto* proc = sb.startProcess(
        L"cmd /d /c \"ping -n 3 127.0.0.1 >nul\"", workdir, true, {}, nullptr);
    printf("pid=%lu\n", proc->pid());

    Sleep(3000);
    auto result = proc->pollExit();
    if (result) {
      printf("pollExit: code=%lu reason=%s\n", result->first, result->second.c_str());
    } else {
      printf("pollExit: still running\n");
    }
    sb.shutdown();
    printf("SELFTEST OK\n");
    return 0;
  } catch (const std::exception& e) {
    printf("SELFTEST EXCEPTION: %s\n", e.what());
    return 2;
  }
}
