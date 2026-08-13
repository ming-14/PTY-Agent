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
#include "infra/globalquota/GlobalQuotaManagerImpl.hpp"
#include "core/entities/SandboxConfig.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <chrono>
#include <format>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

namespace py = pybind11;
namespace winsandbox::bindings {

// 前向声明（定义在 PySandboxInstance 之后）
static void ShutdownWithGilManagement(NativeSandboxInstance& instance);

// =============================================================================
// 配置加载失败抛 ProtocolError（Python 层 win_sandbox.exceptions.ProtocolError）
//
// 文档 §9 承诺配置类错误（未知字段/非法枚举/路径展开失败）抛 ProtocolError
// 且可被 except SandboxError 捕获；此处把原生 runtime_error 翻译为该类型。
// =============================================================================
[[noreturn]] void ThrowProtocolError(const std::string& message) {
    static py::object proto_cls =
        py::module_::import("win_sandbox.exceptions").attr("ProtocolError");
    PyErr_SetString(proto_cls.ptr(), message.c_str());
    throw py::error_already_set();
}

// =============================================================================
// PySandboxInstance - NativeSandboxInstance 的 Python 包装
// =============================================================================
class PySandboxInstance {
public:
    // 构造：config (dict / JSON 路径 / None) + log_level
    PySandboxInstance(py::object config, std::string log_level) {
        // 0. 严格校验 log_level（与配置 schema 一致的合法集合；非法值显式拒绝，
        //    杜绝"配了没生效"的静默级别回退）
        try {
            parse_log_level(log_level);
        } catch (const std::invalid_argument& e) {
            throw py::value_error(e.what());
        }

        // 1. 初始化日志系统
        logger_ = Logger::Init(log_level);
        if (!logger_) {
            throw std::runtime_error("Logger::Init failed");
        }

        // 2. 启动期残留兜底清理（会话目录/ETW 会话）
        //    每次 SandboxInstance 创建即一次沙箱会话启动。
        StartupCleanup::RunAll(logger_);

        // 3. 加载配置（失败 → ProtocolError，而非原生 RuntimeError）
        ConfigLoader loader(logger_);
        if (config.is_none()) {
            config_ = loader.Default();
        } else if (py::isinstance<py::str>(config)) {
            // JSON 路径
            std::string path = config.cast<std::string>();
            auto r = loader.Load(path);
            if (!r) {
                ThrowProtocolError(std::string("Config load failed: [") +
                                   std::to_string(static_cast<int>(r.Code())) +
                                   "] " + r.Message());
            }
            config_ = std::move(r.Value());
        } else if (py::isinstance<py::dict>(config)) {
            // dict → JSON 字符串 → LoadFromJsonString
            nlohmann::json j = py_to_json(config);
            auto r = loader.LoadFromJsonString(j.dump());
            if (!r) {
                ThrowProtocolError(std::string("Config parse failed: [") +
                                   std::to_string(static_cast<int>(r.Code())) +
                                   "] " + r.Message());
            }
            config_ = std::move(r.Value());
        } else {
            throw py::type_error("config must be None, str (path), or dict");
        }

        // 3. 检测能力
        capabilities_ = PermissionDetector::BuildReport();

        // 3a. 配置驱动的日志重配（logging.level/dir/retention_days 生效；
        //     早期日志走构造参数 level）
        logger_ = Logger::Configure(config_.logging.level, config_.logging.dir,
                                    config_.logging.retention_days);

        // 3b. 全局配额（config.global_quota.enabled 时创建并注册共享池）
        //     实现注入 NativeSandboxInstance（StartProcess 前 Acquire/退出后 Release）；
        //     注册失败不致命 → 降级为无全局配额（记 Warn）
        if (config_.global_quota.enabled) {
            global_quota_ = std::make_unique<GlobalQuotaManagerImpl>(logger_);
            auto gq_r = global_quota_->Register(config_.global_quota);
            if (!gq_r) {
                logger_->Log(LogLevel::Warn,
                             std::format("global quota register failed, degraded to "
                                         "no global quota: [{}] {}",
                                         static_cast<int>(gq_r.Code()), gq_r.Message()));
                global_quota_.reset();
            } else {
                logger_->Log(LogLevel::Info,
                             "global quota pool registered: " +
                                 config_.global_quota.pool_name);
            }
        }

        // 4. 创建 NativeSandboxInstance（全局配额 + monitoring 配置）
        instance_ = std::make_unique<NativeSandboxInstance>(
            logger_, nullptr, global_quota_.get(), config_.monitoring);
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
        const py::object& hpcon,
        const py::object& request_id) {

        // request_id：int | None → 字符串存储（"" = 未传）
        std::string request_id_str;
        if (!request_id.is_none()) {
            if (py::isinstance<py::int_>(request_id)) {
                request_id_str = std::to_string(request_id.cast<int64_t>());
            } else {
                throw py::type_error("request_id must be int or None");
            }
        }

        // 构造 StartProcessRequest（复用 BuildStartProcessRequest）
        auto req = BuildStartProcessRequest(
            command_line, working_dir, env_vars, inherit_env,
            quota, isolation_policy, interactive, stream_buffer_size, stdin_data, hpcon,
            config_.default_quota, config_.default_isolation_policy, request_id_str);

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

        // 墙钟超时：quota.wall_clock_timeout_ms 生效（Python 端不再手动管；
        // 后台线程到期 Terminate(KilledByTimeout)，进程已退出时自动 no-op）
        if (req.quota.wall_clock_timeout_ms.has_value() && *req.quota.wall_clock_timeout_ms > 0) {
            auto usecase = handle.usecase;
            uint64_t timeout_ms = *req.quota.wall_clock_timeout_ms;
            auto logger = logger_;
            std::thread([usecase, timeout_ms, logger]() {
                std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
                if (!usecase->IsFinished()) {
                    auto r = usecase->Terminate(1, ExitReason::KilledByTimeout);
                    if (!r) {
                        logger->Log(LogLevel::Warn,
                                    std::format("wall clock terminate failed: [{}] {}",
                                                static_cast<int>(r.Code()), r.Message()));
                    } else {
                        logger->Log(LogLevel::Info,
                                    std::format("wall clock timeout fired: pid={}",
                                                usecase->Process().pid));
                    }
                }
            }).detach();
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
    // shutdown 方法说明：三阶段 GIL 管理，防 py::function 无 GIL 析构崩溃 + ETW 线程死锁
    //   第 1 步：释放 GIL → StopEtwMonitor（join ETW 线程，线程可获 GIL 完成回调）
    //   第 2 步：持 GIL → ClearAllCallbacks（安全销毁 py::function 捕获）
    //   第 3 步：释放 GIL → ShutdownAll（usecase 无 py::function，安全析构）
    void shutdown() {
        ShutdownWithGilManagement(*instance_);
    }

    // ----- 上下文管理器（with SandboxInstance() as sb:）-----
    // __enter__ 返回自身；__exit__ 调 shutdown()（重复调用幂等）
    PySandboxInstance* enter() { return this; }
    void exit(const py::object&, const py::object&, const py::object&) {
        if (instance_) {
            ShutdownWithGilManagement(*instance_);
        }
    }

    // ----- process_count 属性 -----
    size_t process_count() const {
        return instance_->ProcessCount();
    }

    // ----- cleanup_finished 方法 -----
    // 清理已退出的 usecase（StartProcess 入口也会自动调用）
    void cleanup_finished() {
        py::gil_scoped_release gil;
        instance_->CleanupFinished();
    }

private:
    std::shared_ptr<ILogger> logger_;
    SandboxConfig config_;
    CapabilityReport capabilities_;
    std::unique_ptr<GlobalQuotaManagerImpl> global_quota_;  // 先声明（后析构），
                                                            // instance_ 析构时仍存活
    std::unique_ptr<NativeSandboxInstance> instance_;
};

// =============================================================================
// ShutdownWithGilManagement - 三阶段 GIL 管理的 shutdown 辅助
//
// py::function 析构需要 GIL，但 ShutdownAll 内 usecase.reset()
// 析构 py::function 时无 GIL → 崩溃。同时若持 GIL 调 Stop() → ETW 线程阻塞
// 在 gil_scoped_acquire → join 死锁。
//
// 三阶段：
//   1. 释放 GIL → StopEtwMonitor（join ETW 线程，线程可获 GIL 完成末次回调）
//   2. 持 GIL → ClearAllCallbacks（安全销毁 py::function 捕获）
//   3. 释放 GIL → ShutdownAll（usecase 已无 py::function，安全析构）
// =============================================================================
void ShutdownWithGilManagement(NativeSandboxInstance& instance) {
    // 第 1 步：停止 ETW monitor（释放 GIL，让 ETW 线程可获 GIL 完成）
    {
        py::gil_scoped_release gil;
        instance.StopEtwMonitor();
    }
    // 第 2 步：清空所有回调（持 GIL，安全销毁 py::function）
    instance.ClearAllCallbacks();
    // 第 3 步：终止 + 析构（释放 GIL，usecase 已无 py::function）
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
             py::arg("request_id") = py::none(),
             "启动隔离进程，返回 Process 对象。"
             "hpcon 传入外部创建的 ConPTY 句柄（HPCON int 值）时进入 ConPTY 模式："
             "子进程 stdio 由伪控制台驱动，stdin/stdout/stderr 句柄为 None，I/O 走外部 ConPTY；"
             "request_id (int|None) 用于关联请求，出现在 Process.request_id 中")

        .def("list_processes", &PySandboxInstance::list_processes,
             "列出所有进程状态")
        .def("shutdown", &PySandboxInstance::shutdown,
             "终止所有进程并清理")
        .def("cleanup_finished", &PySandboxInstance::cleanup_finished,
             "清理已退出的进程（释放 per-process 资源与全局配额；start_process 前自动调用）")

        // 上下文管理器：with SandboxInstance() as sb:
        .def("__enter__", &PySandboxInstance::enter,
             py::return_value_policy::reference,
             "进入上下文（返回自身）")
        .def("__exit__", &PySandboxInstance::exit,
             "退出上下文（自动 shutdown）")
    ;
}

} // namespace winsandbox::bindings
