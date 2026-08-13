// =============================================================================
// ProcessBinding - pybind11 Process 包装（绑定层）
//
// PyProcess 包装 NativeSandboxedProcess + NativeExecuteResult，暴露给 Python：
//   - 句柄属性（process_handle/stdin_handle/stdout_handle/stderr_handle，int 值）
//   - 回调 setter（on_resource_limit/on_job_process_started/on_job_process_exited）
//   - 方法（wait/terminate/signal/close_stdin/query_*/close）
//
// 回调桥接（GIL 管理）：
//   - Python callable (py::function) 存为 PyProcess 成员（引用计数保持）
//   - setter 同时设置 usecase 的 std::function（IOCP 线程回调时持 GIL 调 Python callable）
//   - 回调契约：回调内禁止调 C++ 方法（防死锁）
//
// GIL 管理策略：
//   - wait/terminate/signal/query_*：释放 GIL（让其他 Python 线程跑）
//   - 构造/属性访问/回调 setter：持有 GIL（快速操作）
//   - IOCP 回调 Python：获取 GIL（py::gil_scoped_acquire）
// =============================================================================
#include "bindings/BindingCommon.hpp"
#include "bindings/ProcessBinding.hpp"
#include "core/usecases/NativeSandboxedProcess.hpp"
#include "core/entities/SandboxedProcess.hpp"
#include "core/entities/ErrorCode.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <windows.h>

#include <memory>
#include <stdexcept>
#include <string>

namespace py = pybind11;
namespace winsandbox::bindings {

// =============================================================================
// PyProcess - NativeSandboxedProcess 的 Python 包装
// =============================================================================
class PyProcess {
public:
    PyProcess(std::shared_ptr<NativeSandboxedProcess> usecase,
              NativeExecuteResult exec_result,
              uint32_t process_id)
        : usecase_(std::move(usecase))
        , exec_result_(std::move(exec_result))
        , process_id_(process_id) {
    }

    ~PyProcess() {
        // 兜底清理（Python __del__ 或 GC 触发）
        if (!usecase_) {
            return;
        }
        // 1) 先清回调：std::function 捕获的 py::function 析构需要 GIL（当前持有）
        //    （清空后 IOCP/ETW 线程即使在执行回调也不触及 Python 对象）
        usecase_->ClearAllCallbacks();
        // 2) 释放 GIL 后 Close()：Close 会 join IOCP 通知线程，
        //    若持 GIL join，而 IOCP 线程正等在 gil_scoped_acquire → 死锁
        try {
            py::gil_scoped_release gil;
            usecase_->Close();
        } catch (...) {
            // 析构不抛异常
        }
    }

    // ----- 句柄属性 -----
    uint32_t process_id() const { return process_id_; }
    uint32_t pid() const { return exec_result_.process.pid; }

    // request_id：int | None（"" → None，数字字符串 → int）
    py::object request_id() const {
        const std::string& s = exec_result_.process.request_id;
        if (s.empty()) {
            return py::none();
        }
        try {
            return py::int_(std::stoull(s));
        } catch (const std::exception&) {
            return py::none();
        }
    }

    // HANDLE 值转 int（Python 端用 ctypes 操作）
    int64_t process_handle() const {
        return reinterpret_cast<int64_t>(exec_result_.process_handle);
    }

    // ConPTY 模式标记（hpcon 启动）：true 时 stdio 句柄全为 None，I/O 走外部 ConPTY
    bool is_pty() const { return exec_result_.is_pty; }

    py::object stdin_handle() const {
        // ConPTY 模式：无匿名管道，stdin 由外部 ConPTY 提供
        if (exec_result_.is_pty || exec_result_.stdin_write == nullptr) {
            return py::none();
        }
        return py::int_(reinterpret_cast<int64_t>(exec_result_.stdin_write));
    }

    py::object stdout_handle() const {
        // ConPTY 模式：无匿名管道，stdout 由外部 ConPTY 提供
        if (exec_result_.is_pty || exec_result_.stdout_read == nullptr) {
            return py::none();
        }
        return py::int_(reinterpret_cast<int64_t>(exec_result_.stdout_read));
    }

    py::object stderr_handle() const {
        // ConPTY 模式：无匿名管道，stderr 合并到 ConPTY 输出流
        if (exec_result_.is_pty || exec_result_.stderr_read == nullptr) {
            return py::none();
        }
        return py::int_(reinterpret_cast<int64_t>(exec_result_.stderr_read));
    }

    // ----- 回调 setter -----
    // 设置回调：存 py::function + 包装为 std::function（持 GIL）注入 usecase
    void set_on_resource_limit(py::function f) {
        on_resource_limit_ = std::move(f);
        if (on_resource_limit_) {
            auto cb = on_resource_limit_;
            usecase_->SetOnResourceLimit([cb](const ResourceLimitInfo& info) {
                py::gil_scoped_acquire gil;
                cb(resource_limit_info_to_dict(info));
            });
        } else {
            usecase_->SetOnResourceLimit(nullptr);
        }
    }

    void set_on_job_process_started(py::function f) {
        on_job_process_started_ = std::move(f);
        if (on_job_process_started_) {
            auto cb = on_job_process_started_;
            usecase_->SetOnJobProcessStarted([cb](const JobProcessStartedInfo& info) {
                py::gil_scoped_acquire gil;
                cb(job_process_started_info_to_dict(info));
            });
        } else {
            usecase_->SetOnJobProcessStarted(nullptr);
        }
    }

    void set_on_job_process_exited(py::function f) {
        on_job_process_exited_ = std::move(f);
        if (on_job_process_exited_) {
            auto cb = on_job_process_exited_;
            usecase_->SetOnJobProcessExited([cb](const JobProcessExitedInfo& info) {
                py::gil_scoped_acquire gil;
                cb(job_process_exited_info_to_dict(info));
            });
        } else {
            usecase_->SetOnJobProcessExited(nullptr);
        }
    }

    // ETW 行为事件回调
    void set_on_behavior_event(py::function f) {
        on_behavior_event_ = std::move(f);
        if (on_behavior_event_) {
            auto cb = on_behavior_event_;
            usecase_->SetOnBehaviorEvent([cb](const BehaviorEventInfo& info) {
                py::gil_scoped_acquire gil;
                cb(behavior_event_info_to_dict(info));
            });
        } else {
            usecase_->SetOnBehaviorEvent(nullptr);
        }
    }

    // AccessDenied 专项回调
    void set_on_access_denied(py::function f) {
        on_access_denied_ = std::move(f);
        if (on_access_denied_) {
            auto cb = on_access_denied_;
            usecase_->SetOnAccessDenied([cb](const AccessDeniedInfo& info) {
                py::gil_scoped_acquire gil;
                cb(access_denied_info_to_dict(info));
            });
        } else {
            usecase_->SetOnAccessDenied(nullptr);
        }
    }

    // ----- 方法 -----

    // wait(timeout_ms) → (exit_code, exit_reason, resource_usage)
    // timeout_ms < 0 或省略 = INFINITE
    // 超时抛 SandboxTimeoutError，其他错误抛 SandboxProcessError
    py::tuple wait(int64_t timeout_ms = -1) {
        uint64_t tm = (timeout_ms < 0) ? UINT64_MAX : static_cast<uint64_t>(timeout_ms);

        NativeWaitResult wr;
        ErrorCode err_code = ErrorCode::Ok;
        std::string err_msg;
        {
            // 释放 GIL 让其他 Python 线程跑
            py::gil_scoped_release gil;
            auto r = usecase_->Wait(tm);
            if (!r) {
                err_code = r.Code();
                err_msg = std::string("[") +
                          std::to_string(static_cast<int>(r.Code())) +
                          "] " + r.Message();
            } else {
                wr = std::move(r.Value());
            }
        }
        // GIL 已重获：抛 Python 异常
        if (err_code != ErrorCode::Ok) {
            if (err_code == ErrorCode::ProcessStillRunning) {
                py::object exc = py::module_::import("win_sandbox.exceptions")
                                     .attr("SandboxTimeoutError");
                PyErr_SetString(exc.ptr(), err_msg.c_str());
            } else {
                py::object exc = py::module_::import("win_sandbox.exceptions")
                                     .attr("SandboxProcessError");
                PyErr_SetString(exc.ptr(), err_msg.c_str());
            }
            throw py::error_already_set();
        }

        // 返回 (exit_code, exit_reason, resource_usage)
        py::dict usage = accounting_to_dict(wr.resource_usage);
        return py::make_tuple(
            static_cast<int32_t>(wr.exit_code),
            ExitReasonToString(wr.exit_reason),
            usage);
    }

    // terminate(exit_code=1)
    void terminate(uint32_t exit_code = 1) {
        py::gil_scoped_release gil;
        unwrap_result(usecase_->Terminate(exit_code));
    }

    // signal(sig="ctrl_break")
    void signal(std::string sig = "ctrl_break") {
        ProcessSignal ps;
        if (sig == "ctrl_break") {
            ps = ProcessSignal::CtrlBreak;
        } else if (sig == "kill") {
            ps = ProcessSignal::Kill;
        } else {
            throw py::value_error("invalid signal: " + sig + " (expected: ctrl_break/kill)");
        }
        py::gil_scoped_release gil;
        unwrap_result(usecase_->SignalProcess(ps));
    }

    // close_stdin()
    void close_stdin() {
        py::gil_scoped_release gil;
        usecase_->CloseStdinWrite();
    }

    // query_accounting() → dict
    py::dict query_accounting() {
        JobAccountingInfo info;
        {
            py::gil_scoped_release gil;
            auto r = usecase_->QueryAccounting();
            if (!r) {
                throw std::runtime_error(std::string("[") +
                                        std::to_string(static_cast<int>(r.Code())) +
                                        "] " + r.Message());
            }
            info = std::move(r.Value());
        }
        return accounting_to_dict(info);
    }

    // query_peak_memory() → int
    uint64_t query_peak_memory() {
        py::gil_scoped_release gil;
        auto r = usecase_->QueryPeakMemory();
        if (!r) {
            throw std::runtime_error(std::string("[") +
                                    std::to_string(static_cast<int>(r.Code())) +
                                    "] " + r.Message());
        }
        return r.Value();
    }

    // query_process_list() → list[int]
    std::vector<uint32_t> query_process_list() {
        py::gil_scoped_release gil;
        auto r = usecase_->QueryProcessList();
        if (!r) {
            throw std::runtime_error(std::string("[") +
                                    std::to_string(static_cast<int>(r.Code())) +
                                    "] " + r.Message());
        }
        return r.Value();
    }

    // query_process_exit_code(pid) → (exit_code, is_active)
    // 文档 §4.3/§6.10：运行中 → (259, True)；已退出 → (真实退出码, False)
    // is_active = (code == STILL_ACTIVE)：Win32 GetExitCodeProcess 的固有约定
    py::tuple query_process_exit_code(uint32_t pid) {
        uint32_t code = 0;
        {
            // 释放 GIL 调用 C++ 查询，返回后恢复 GIL 再构造 Python 元组
            py::gil_scoped_release gil;
            auto r = usecase_->QueryProcessExitCode(pid);
            if (!r) {
                throw std::runtime_error(std::string("[") +
                                        std::to_string(static_cast<int>(r.Code())) +
                                        "] " + r.Message());
            }
            code = r.Value();
        }
        return py::make_tuple(code, code == 259u /* STILL_ACTIVE */);
    }

    // close()
    void close() {
        py::gil_scoped_release gil;
        if (usecase_) {
            usecase_->Close();
        }
    }

    // 是否已关闭（供 SandboxInstance 检测）
    bool is_finished() const {
        return usecase_ ? usecase_->IsFinished() : true;
    }

private:
    std::shared_ptr<NativeSandboxedProcess> usecase_;
    NativeExecuteResult exec_result_;
    uint32_t process_id_;

    // 回调（py::function 持有引用，防 GC）
    py::function on_resource_limit_;
    py::function on_job_process_started_;
    py::function on_job_process_exited_;
    py::function on_behavior_event_;    // ETW 行为事件
    py::function on_access_denied_;     // AccessDenied 专项
};

// =============================================================================
// MakePyProcess - 工厂函数（供 SandboxInstanceBinding::start_process 调用）
// =============================================================================
py::object MakePyProcess(std::shared_ptr<NativeSandboxedProcess> usecase,
                         NativeExecuteResult exec_result,
                         uint32_t process_id) {
    // 创建 PyProcess Python 实例（通过 pybind11 类型对象）
    // PyProcess 类型在 RegisterProcess 里注册，此处用 py::cast 构造
    return py::cast(std::make_shared<PyProcess>(
        std::move(usecase), std::move(exec_result), process_id));
}

// =============================================================================
// RegisterProcess - 注册 PyProcess 类到模块
// =============================================================================
void RegisterProcess(py::module_& m) {
    py::class_<PyProcess, std::shared_ptr<PyProcess>>(m, "Process",
        R"doc(隔离进程句柄，由 SandboxInstance.start_process 返回。

属性:
            process_id: int  - 沙箱内部进程 ID
            pid: int          - OS 进程 PID
            request_id: str   - start_process 传入的请求关联 ID（可能为空）
            process_handle: int  - 进程句柄（HANDLE 值，ctypes WaitForSingleObject 用；禁止自行关闭）
    is_pty: bool      - ConPTY 模式标记（hpcon 启动，stdio 走外部 ConPTY）
    stdin_handle: int | None  - stdin 写端句柄（interactive=True 且非 ConPTY 时非空）
    stdout_handle: int | None  - stdout 读端句柄（ctypes ReadFile 用；ConPTY 模式为 None）
    stderr_handle: int | None  - stderr 读端句柄（ctypes ReadFile 用；ConPTY 模式为 None）

回调（setter）:
    on_resource_limit: callable(dict) - Job 资源限制命中
    on_job_process_started: callable(dict) - Job 内子进程创建
    on_job_process_exited: callable(dict) - Job 内子进程退出

方法:
    wait(timeout_ms=-1) -> (exit_code, exit_reason, resource_usage)
    terminate(exit_code=1)
    signal(sig="ctrl_break")  # "ctrl_break" | "kill"
    close_stdin()
    query_accounting() -> dict
    query_peak_memory() -> int
    query_process_list() -> list[int]
    query_process_exit_code(pid) -> (exit_code, is_active)
    close()

回调契约:
    回调内禁止调 C++ 方法（如 proc.terminate），否则可能死锁。
    回调内只做：记录日志、设标志位、入队列。
)doc")
        .def_property_readonly("process_id", &PyProcess::process_id)
        .def_property_readonly("pid", &PyProcess::pid)
        .def_property_readonly("request_id", &PyProcess::request_id)
        .def_property_readonly("process_handle", &PyProcess::process_handle)
        .def_property_readonly("is_pty", &PyProcess::is_pty)
        .def_property_readonly("stdin_handle", &PyProcess::stdin_handle)
        .def_property_readonly("stdout_handle", &PyProcess::stdout_handle)
        .def_property_readonly("stderr_handle", &PyProcess::stderr_handle)

        // 回调 setter
        .def_property("on_resource_limit",
            nullptr,  // getter（不暴露 py::function）
            [](PyProcess& self, py::function f) { self.set_on_resource_limit(std::move(f)); },
            "Job 资源限制命中回调")
        .def_property("on_job_process_started",
            nullptr,
            [](PyProcess& self, py::function f) { self.set_on_job_process_started(std::move(f)); },
            "Job 内子进程创建回调")
        .def_property("on_job_process_exited",
            nullptr,
            [](PyProcess& self, py::function f) { self.set_on_job_process_exited(std::move(f)); },
            "Job 内子进程退出回调")
        .def_property("on_behavior_event",
            nullptr,
            [](PyProcess& self, py::function f) { self.set_on_behavior_event(std::move(f)); },
            "ETW 行为事件回调（文件/注册表/进程/网络访问）")
        .def_property("on_access_denied",
            nullptr,
            [](PyProcess& self, py::function f) { self.set_on_access_denied(std::move(f)); },
            "AccessDenied 专项回调（ETW 或 stderr 关键字扫描）")

        // 方法
        .def("wait", &PyProcess::wait,
             py::arg("timeout_ms") = -1,
             "等待进程退出，返回 (exit_code, exit_reason, resource_usage)")
        .def("terminate", &PyProcess::terminate,
             py::arg("exit_code") = 1,
             "主动终止进程")
        .def("signal", &PyProcess::signal,
             py::arg("sig") = "ctrl_break",
             "发送信号 (ctrl_break/kill)")
        .def("close_stdin", &PyProcess::close_stdin,
             "关闭 stdin 写端（让子进程 ReadFile 返回 EOF）")
        .def("query_accounting", &PyProcess::query_accounting,
             "查询 Job 会计信息")
        .def("query_peak_memory", &PyProcess::query_peak_memory,
             "查询峰值内存（字节）")
        .def("query_process_list", &PyProcess::query_process_list,
             "查询 Job 内所有进程 PID 列表")
        .def("query_process_exit_code", &PyProcess::query_process_exit_code,
             py::arg("pid"),
             "查询指定 PID 的退出码，返回 (exit_code, is_active) 元组")
        .def("close", &PyProcess::close,
             "显式清理 C++ 端资源（Job/隔离 token/句柄）")

        // 内部状态
        .def("__bool__", [](PyProcess& self) { return !self.is_finished(); },
             "进程是否仍在运行")
    ;
}

} // namespace winsandbox::bindings
