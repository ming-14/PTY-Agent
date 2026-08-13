// =============================================================================
// SandboxInstanceBinding - pybind11 SandboxInstance 包装（绑定层）
//
// PySandboxInstance 包装 NativeSandboxInstance，暴露给 Python：
//   - 构造（config + log_level）
//   - capabilities 属性
//   - start_process 方法 → 返回 PyProcess
//   - list_processes / shutdown 方法
//
// 构造流程：
//   1. Logger::Init(log_level) 创建日志器
//   2. StartupCleanup::RunAll 启动期残留兜底清理（上次崩溃遗留的会话目录/ETW 会话）
//   3. ConfigLoader 加载配置（dict / JSON 路径 / None=默认）
//   4. PermissionDetector::BuildReport 检测能力
//   5. NativeSandboxInstance 构造（注入 logger）
// =============================================================================
#include "bindings/BindingCommon.hpp"
#include "bindings/ProcessBinding.hpp"
#include "bindings/ConfigBinding.hpp"  // BuildStartProcessRequest（inline）
#include "adapters/NativeSandboxInstance.hpp"
#include "adapters/ConfigLoader.hpp"
#include "adapters/PermissionDetector.hpp"
#include "infra/logging/Logger.hpp"
#include "infra/StartupCleanup.hpp"
#include "core/entities/SandboxConfig.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>
#include <stdexcept>
#include <string>

namespace py = pybind11;
namespace winsandbox::bindings {

// 前向声明（定义在 PySandboxInstance 之后）
static void ShutdownWithGilManagement(NativeSandboxInstance& instance);

// =============================================================================
// PySandboxInstance - NativeSandboxInstance 的 Python 包装
// =============================================================================
class PySandboxInstance {
public:
    // 构造：config (dict / JSON 路径 / None) + log_level
    PySandboxInstance(py::object config, std::string log_level) {
        // 1. 初始化日志系统
        logger_ = Logger::Init(log_level);
        if (!logger_) {
            throw std::runtime_error("Logger::Init failed");
        }

        // 2. 启动期残留兜底清理（Phase 16：会话目录/ETW 会话）
        //    原 sandbox.exe 主进程启动时执行；pybind11 直调形态下每次
        //    SandboxInstance 创建即一次沙箱会话启动，等价挂载点。
        StartupCleanup::RunAll(logger_);

        // 3. 加载配置
        ConfigLoader loader(logger_);
        if (config.is_none()) {
            config_ = loader.Default();
        } else if (py::isinstance<py::str>(config)) {
            // JSON 路径
            std::string path = config.cast<std::string>();
            auto r = loader.Load(path);
            if (!r) {
                throw std::runtime_error(std::string("Config load failed: [") +
                                        std::to_string(static_cast<int>(r.Code())) +
                                        "] " + r.Message());
            }
            config_ = std::move(r.Value());
        } else if (py::isinstance<py::dict>(config)) {
            // dict → JSON 字符串 → LoadFromJsonString
            nlohmann::json j = py_to_json(config);
            auto r = loader.LoadFromJsonString(j.dump());
            if (!r) {
                throw std::runtime_error(std::string("Config parse failed: [") +
                                        std::to_string(static_cast<int>(r.Code())) +
                                        "] " + r.Message());
            }
            config_ = std::move(r.Value());
        } else {
            throw py::type_error("config must be None, str (path), or dict");
        }

        // 3. 检测能力
        capabilities_ = PermissionDetector::BuildReport();

        // 4. 创建 NativeSandboxInstance（Phase 13：传入 monitoring 配置）
        instance_ = std::make_unique<NativeSandboxInstance>(
            logger_, nullptr, nullptr, config_.monitoring);
    }

    ~PySandboxInstance() {
        if (instance_) {
            ShutdownWithGilManagement(*instance_);
        }
    }

    // ----- capabilities 属性 -----
    py::dict capabilities() const {
        // CapabilityReport → dict
        py::dict d;
        d["mode"] = (capabilities_.mode == PermissionMode::Admin) ? "admin" : "standard_user";
        py::list caps;
        for (const auto& item : capabilities_.capabilities) {
            py::dict c;
            c["module"] = item.module;
            c["available"] = item.available;
            c["degraded_reason"] = item.degraded_reason;
            caps.append(c);
        }
        d["capabilities"] = caps;
        return d;
    }

    // ----- start_process 方法 -----
    py::object start_process(
        const std::string& command_line,
        const py::object& working_dir,
        const py::object& env_vars,
        bool inherit_env,
        const py::object& quota,
        const py::object& isolation_policy,
        bool interactive,
        size_t stream_buffer_size,
        const py::object& stdin_data,
        const py::object& hpcon) {

        // 构造 StartProcessRequest（复用 BuildStartProcessRequest）
        auto req = BuildStartProcessRequest(
            command_line, working_dir, env_vars, inherit_env,
            quota, isolation_policy, interactive, stream_buffer_size, stdin_data, hpcon,
            config_.default_quota, config_.default_isolation_policy);

        // 释放 GIL 调用 StartProcess（耗时操作：Launch + AssignProcess）
        NativeProcessHandle handle;
        {
            py::gil_scoped_release gil;
            auto r = instance_->StartProcess(req);
            if (!r) {
                throw std::runtime_error(std::string("[") +
                                        std::to_string(static_cast<int>(r.Code())) +
                                        "] " + r.Message());
            }
            handle = std::move(r.Value());
        }

        // 构造 PyProcess 返回
        return MakePyProcess(std::move(handle.usecase),
                             std::move(handle.exec_result),
                             handle.process_id);
    }

    // ----- list_processes 方法 -----
    py::list list_processes() const {
        std::vector<SandboxedProcess> procs;
        {
            py::gil_scoped_release gil;
            procs = instance_->ListProcesses();
        }
        py::list lst;
        for (const auto& p : procs) {
            lst.append(process_to_dict(p));
        }
        return lst;
    }

    // ----- shutdown 方法 -----
    // Phase 14 修复：三阶段 GIL 管理，防 py::function 无 GIL 析构崩溃 + ETW 线程死锁
    //   Phase 1：释放 GIL → StopEtwMonitor（join ETW 线程，线程可获 GIL 完成回调）
    //   Phase 2：持 GIL → ClearAllCallbacks（安全销毁 py::function 捕获）
    //   Phase 3：释放 GIL → ShutdownAll（usecase 无 py::function，安全析构）
    void shutdown() {
        ShutdownWithGilManagement(*instance_);
    }

    // ----- process_count 属性 -----
    size_t process_count() const {
        return instance_->ProcessCount();
    }

private:
    std::shared_ptr<ILogger> logger_;
    SandboxConfig config_;
    CapabilityReport capabilities_;
    std::unique_ptr<NativeSandboxInstance> instance_;
};

// =============================================================================
// ShutdownWithGilManagement - 三阶段 GIL 管理的 shutdown 辅助
//
// Phase 14 修复：py::function 析构需要 GIL，但 ShutdownAll 内 usecase.reset()
// 析构 py::function 时无 GIL → 崩溃。同时若持 GIL 调 Stop() → ETW 线程阻塞
// 在 gil_scoped_acquire → join 死锁。
//
// 三阶段：
//   1. 释放 GIL → StopEtwMonitor（join ETW 线程，线程可获 GIL 完成末次回调）
//   2. 持 GIL → ClearAllCallbacks（安全销毁 py::function 捕获）
//   3. 释放 GIL → ShutdownAll（usecase 已无 py::function，安全析构）
// =============================================================================
void ShutdownWithGilManagement(NativeSandboxInstance& instance) {
    // Phase 1：停止 ETW monitor（释放 GIL，让 ETW 线程可获 GIL 完成）
    {
        py::gil_scoped_release gil;
        instance.StopEtwMonitor();
    }
    // Phase 2：清空所有回调（持 GIL，安全销毁 py::function）
    instance.ClearAllCallbacks();
    // Phase 3：终止 + 析构（释放 GIL，usecase 已无 py::function）
    {
        py::gil_scoped_release gil;
        instance.ShutdownAll();
    }
}

// =============================================================================
// RegisterSandboxInstance - 注册 PySandboxInstance 类到模块
// =============================================================================
void RegisterSandboxInstance(py::module_& m) {
    py::class_<PySandboxInstance>(m, "SandboxInstance",
        R"doc(沙箱实例，管理多个隔离进程。

构造:
    SandboxInstance(config=None, log_level="info")
    config: None / dict / JSON 路径
    log_level: "trace" / "debug" / "info" / "warn" / "error"

属性:
    capabilities: dict - 当前环境能力报告

方法:
    start_process(command_line, ...) -> Process
    list_processes() -> list[dict]
    shutdown()
    process_count: int
)doc")
        .def(py::init<py::object, std::string>(),
             py::arg("config") = py::none(),
             py::arg("log_level") = "info")

        .def_property_readonly("capabilities", &PySandboxInstance::capabilities)
        .def_property_readonly("process_count", &PySandboxInstance::process_count)

        .def("start_process", &PySandboxInstance::start_process,
             py::arg("command_line"),
             py::arg("working_dir") = py::none(),
             py::arg("env_vars") = py::none(),
             py::arg("inherit_env") = true,
             py::arg("quota") = py::none(),
             py::arg("isolation_policy") = py::none(),
             py::arg("interactive") = false,
             py::arg("stream_buffer_size") = 0,
             py::arg("stdin_data") = py::none(),
             py::arg("hpcon") = py::none(),
             "启动隔离进程，返回 Process 对象。"
             "hpcon 传入外部创建的 ConPTY 句柄（HPCON int 值）时进入 ConPTY 模式："
             "子进程 stdio 由伪控制台驱动，stdin/stdout/stderr 句柄为 None，I/O 走外部 ConPTY")

        .def("list_processes", &PySandboxInstance::list_processes,
             "列出所有进程状态")
        .def("shutdown", &PySandboxInstance::shutdown,
             "终止所有进程并清理")
    ;
}

} // namespace winsandbox::bindings
