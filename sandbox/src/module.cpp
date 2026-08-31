// module.cpp — pybind11 extension entry for the in-process sandbox.
//
// Exposes SandboxInstance and SandboxedProcess to Python. The C++ core
// (WRITE_RESTRICTED token + capability-SID write allowlist + Job resource
// limits) loads into the Python interpreter process, so handles (HPCON
// included) are shared directly — no IPC, no pipes, no protocol lines.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <map>

#include "instance.h"

namespace py = pybind11;
namespace {

// Translate a std::runtime_error into a Python RuntimeError. Callers must
// hold the GIL (PyErr_SetString requires it).
[[noreturn]] void throwPyError(const std::exception& e) {
  PyErr_SetString(PyExc_RuntimeError, e.what());
  throw py::error_already_set();
}

// ---------------------------------------------------------------------------
// PyProcess — Python wrapper over SandboxedProcess.
// ---------------------------------------------------------------------------
class PyProcess {
public:
  explicit PyProcess(std::shared_ptr<winacl::SandboxedProcess> process)
      : process_(std::move(process)) {}

  ~PyProcess() {
    // Runs under the GIL (Python GC). Clear the C++ callbacks first so no
    // IOCP invocation can touch the py::function members after they die;
    // invoke() serializes with this through the GIL.
    process_->clearCallbacks();
  }

  uint32_t pid() const { return process_->pid(); }

  // Setters: install Python callables; the C++ side invokes them from the
  // job's IOCP thread through the locked process callbacks, and the bridge
  // acquires the GIL there.
  void set_on_process_started(py::object f) {
    started_ = py::cast<py::function>(f);
    process_->setCallbacks(
        [this](DWORD pid) { invoke(started_, pid); },
        [this](DWORD pid, DWORD code, bool abnormal) {
          invoke(exited_, pid, code, abnormal);
        });
  }
  void set_on_process_exited(py::object f) {
    exited_ = py::cast<py::function>(f);
    process_->setCallbacks(
        [this](DWORD pid) { invoke(started_, pid); },
        [this](DWORD pid, DWORD code, bool abnormal) {
          invoke(exited_, pid, code, abnormal);
        });
  }

  py::object started_obj() const { return started_ ? py::object(started_) : py::none(); }
  py::object exited_obj() const { return exited_ ? py::object(exited_) : py::none(); }

  py::tuple wait() {
    // Release the GIL while blocking; the C++ side is pure Win32. Reacquire
    // before translating exceptions / building the result.
    py::gil_scoped_release release;
    try {
      const auto [code, reason] = process_->wait();
      py::gil_scoped_acquire acquire;
      return py::make_tuple(code, reason);
    } catch (const std::exception& e) {
      py::gil_scoped_acquire acquire;
      throwPyError(e);
    }
  }

  py::object poll_exit() {
    py::gil_scoped_release release;
    try {
      const auto result = process_->pollExit();
      py::gil_scoped_acquire acquire;
      if (!result) return py::none();
      return py::make_tuple(result->first, result->second);
    } catch (const std::exception& e) {
      py::gil_scoped_acquire acquire;
      throwPyError(e);
    }
  }

  void terminate(uint32_t exit_code = 1) {
    py::gil_scoped_release release;
    process_->terminate(exit_code);
  }

  bool signal_ctrl_break() {
    py::gil_scoped_release release;
    return process_->signalCtrlBreak();
  }

  py::list query_process_list() {
    std::vector<DWORD> pids;
    {
      py::gil_scoped_release release;
      pids = process_->queryProcessList();
    }
    py::list out;
    for (const DWORD pid : pids) out.append(pid);
    return out;
  }

  py::tuple query_process_exit_code(uint32_t pid) {
    std::pair<uint32_t, bool> result;
    {
      py::gil_scoped_release release;
      result = process_->queryProcessExitCode(pid);
    }
    return py::make_tuple(result.first, result.second);
  }

private:
  template <typename... Args>
  void invoke(const py::function& fn, Args&&... args) {
    try {
      py::gil_scoped_acquire acquire;  // GIL BEFORE touching the py::function
      if (!fn) return;
      fn(std::forward<Args>(args)...);
    } catch (...) {
      // Callbacks must never propagate into the IOCP thread.
      PyErr_Clear();
    }
  }

  std::shared_ptr<winacl::SandboxedProcess> process_;
  py::function started_;
  py::function exited_;
};

// ---------------------------------------------------------------------------
// PySandboxInstance — Python wrapper over SandboxInstance.
// ---------------------------------------------------------------------------
class PySandboxInstance {
public:
  PySandboxInstance() = default;
  ~PySandboxInstance() {
    try {
      py::gil_scoped_release release;
      instance_.shutdown();
    } catch (...) {
    }
  }

  PyProcess start_process(const std::string& command_line,
                          const std::string& working_dir,
                          bool workspace_write,
                          const py::dict& quota,
                          py::object hpcon,
                          const py::dict& env) {
    // Parse quota BEFORE releasing the GIL — the py::dict must not be
    // touched from a thread that doesn't hold the GIL.
    winacl::ResourceLimits limits;
    parseQuota(quota, limits);
    HPCON conpty = nullptr;
    if (!hpcon.is_none()) {
      conpty = reinterpret_cast<HPCON>(hpcon.cast<uint64_t>());
    }
    // Parse env overrides into a C++ map BEFORE releasing the GIL.
    std::map<std::wstring, std::wstring> envOverrides;
    if (!env.is_none()) {
      for (const auto& item : env) {
        envOverrides[utf8ToWide(item.first.cast<std::string>())] =
            utf8ToWide(item.second.cast<std::string>());
      }
    }
    std::shared_ptr<winacl::SandboxedProcess> process;
    {
      py::gil_scoped_release release;
      try {
        process = instance_.startProcess(
            utf8ToWide(command_line), utf8ToWide(working_dir),
            workspace_write, limits, conpty,
            envOverrides.empty() ? nullptr : &envOverrides);
      } catch (const std::exception& e) {
        py::gil_scoped_acquire acquire;  // PyErr_SetString needs the GIL
        throwPyError(e);
      }
    }
    return PyProcess(std::move(process));
  }

  void shutdown() {
    py::gil_scoped_release release;
    instance_.shutdown();
  }

private:
  static void parseQuota(const py::dict& quota, winacl::ResourceLimits& limits) {
    auto num = [&](const char* key, uint64_t& target) {
      if (quota.contains(key)) {
        target = quota[key].cast<uint64_t>();
      }
    };
    num("memory_mb", limits.memoryMb);
    num("job_memory_mb", limits.jobMemoryMb);
    num("cpu_ms", limits.cpuMs);
    num("wall_clock_timeout_ms", limits.wallClockMs);
    if (quota.contains("cpu_rate_percent")) limits.cpuRatePercent = quota["cpu_rate_percent"].cast<uint32_t>();
    if (quota.contains("max_processes")) limits.maxProcesses = quota["max_processes"].cast<uint32_t>();
    if (quota.contains("no_ui")) limits.noUi = quota["no_ui"].cast<bool>();
    if (quota.contains("crash_silent")) limits.crashSilent = quota["crash_silent"].cast<bool>();
    if (quota.contains("breakaway_ok")) limits.breakawayOk = quota["breakaway_ok"].cast<bool>();
  }

  static std::wstring utf8ToWide(const std::string& utf8) {
    if (utf8.empty()) return {};
    const int len = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
    if (len <= 0) {
      throw std::runtime_error("MultiByteToWideChar failed: invalid UTF-8 input");
    }
    std::wstring wide(static_cast<size_t>(len) - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, wide.data(), len);
    return wide;
  }

  winacl::SandboxInstance instance_;
};

}  // namespace

PYBIND11_MODULE(win_sandbox_native, m) {
  m.doc() = "in-process sandbox native extension: WRITE_RESTRICTED token + "
            "capability-SID write allowlist + Job resource limits";

  py::class_<PyProcess>(m, "Process")
      .def(py::init<std::shared_ptr<winacl::SandboxedProcess>>())
      .def_property_readonly("pid", &PyProcess::pid)
      .def_property("on_job_process_started",
                    [](PyProcess& p) { return p.started_obj(); },
                    &PyProcess::set_on_process_started)
      .def_property("on_job_process_exited",
                    [](PyProcess& p) { return p.exited_obj(); },
                    &PyProcess::set_on_process_exited)
      .def("wait", &PyProcess::wait)
      .def("poll_exit", &PyProcess::poll_exit)
      .def("terminate", &PyProcess::terminate, py::arg("exit_code") = 1)
      .def("signal_ctrl_break", &PyProcess::signal_ctrl_break)
      .def("query_process_list", &PyProcess::query_process_list)
      .def("query_process_exit_code", &PyProcess::query_process_exit_code);

  py::class_<PySandboxInstance>(m, "SandboxInstance")
      .def(py::init<>())
      .def("start_process", &PySandboxInstance::start_process,
           py::arg("command_line"), py::arg("working_dir"),
           py::arg("workspace_write") = true, py::arg("quota") = py::dict(),
           py::arg("hpcon") = py::none(), py::arg("env") = py::dict())
      .def("shutdown", &PySandboxInstance::shutdown);
}
