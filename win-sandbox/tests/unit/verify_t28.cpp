// =============================================================================
// Job 功能增强运行时验证
//
// 覆盖：
//   QueryProcessList：
//     1. 空 Job → Ok 且 pids 为空
//     2. 启动进程并 Assign → 列表包含该 pid 且 count >= 1
//     3. 进程退出后 QueryProcessList → 不再包含已退出的 pid
//   QueryProcessExitCode：
//     4. 运行中 ping → 259 (STILL_ACTIVE)
//     5. cmd /c exit 7 退出后 → 7
//   进程路径（NEW_PROCESS 通知携带 process_name/process_path）：
//     6. 启动 ping → NewProcess 通知带非空 process_path，process_name 含 ping/cmd
//   SetCrashSilent：
//     7. SetCrashSilent(true)/(false) 幂等
//     8. crash_dummy（空指针崩溃）在 crash_silent=true 下：10s 内死亡（无 WER 挂起）
//   退出分类（正常/异常）：
//     9.  cmd /c exit 0 → ProcessExitNormal，exit_code == 0
//    10.  cmd /c exit 7 → ProcessExitAbnormal，exit_code == 7
//    11. crash_dummy    → ProcessExitAbnormal，exit_code == 0xC0000005
//
// 依赖：crash_dummy.exe 与 verify_t28.exe 位于同一目录（构建后同在 build/bin）。
// 缺失时测试 8/11 跳过并提示（不算失败）。
//
// 不覆盖：
//   - IPC query_process_list / ProcessList 事件 → e2e test_job_enhancement.py
//   - Job 内子进程（cmd /c 派生）列表 → e2e
// =============================================================================

#include "core/entities/JobNotification.hpp"
#include "core/entities/Result.hpp"
#include "core/ports/IJobNotificationSink.hpp"
#include "core/ports/ILogger.hpp"
#include "infra/job/JobObjectImpl.hpp"
#include "infra/logging/Logger.hpp"
#include "infra/process/ProcessLauncherImpl.hpp"

#include <spdlog/spdlog.h>
#include <wil/resource.h>

#include <windows.h>

#include <cctype>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <format>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using namespace winsandbox;

// =============================================================================
// LaunchedProc - 启动的进程句柄集合（RAII 析构自动关闭）
// =============================================================================
struct LaunchedProc {
    wil::unique_handle process;
    wil::unique_handle thread;
    wil::unique_handle stdin_write;
    wil::unique_handle stdout_read;
    wil::unique_handle stderr_read;
};

// 启动进程并 Assign 到 Job
static Result<LaunchedProc> LaunchAndAssign(ProcessLauncherImpl& launcher,
                                            JobObjectImpl& job,
                                            const std::string& command,
                                            bool create_no_window = true) {
    LaunchRequest req;
    req.command_line = command;
    req.create_no_window = create_no_window;

    auto r = launcher.Launch(req);
    if (!r) {
        return Result<LaunchedProc>::Err(r.Code(), r.Message());
    }

    LaunchResult lr = std::move(r.Value());
    LaunchedProc p;
    p.process.reset(static_cast<HANDLE>(lr.process_handle));
    p.thread.reset(static_cast<HANDLE>(lr.thread_handle));
    p.stdin_write.reset(static_cast<HANDLE>(lr.stdin_write));
    p.stdout_read.reset(static_cast<HANDLE>(lr.stdout_read));
    p.stderr_read.reset(static_cast<HANDLE>(lr.stderr_read));

    auto assign_r = job.AssignProcess(p.process.get());
    if (!assign_r) {
        return Result<LaunchedProc>::Err(assign_r.Code(), assign_r.Message());
    }
    return Result<LaunchedProc>::Ok(std::move(p));
}

// =============================================================================
// NotificationCollector - 收集 Job 通知（IOCP 线程异步回调，轮询等待）
// =============================================================================
class NotificationCollector : public IJobNotificationSink {
public:
    void OnNotification(const JobNotification& n) override {
        std::lock_guard<std::mutex> lock(mutex_);
        notifications_.push_back(n);
        if (n.type == JobNotificationType::NewProcess) {
            ++new_process_count_;
        } else if (n.type == JobNotificationType::ProcessExitNormal ||
                   n.type == JobNotificationType::ProcessExitAbnormal ||
                   n.type == JobNotificationType::ProcessExit) {
            ++exit_count_;
            last_exit_ = n;
        }
    }

    // 等待 NewProcess 通知（最多 timeout_ms）
    bool WaitNewProcess(uint32_t timeout_ms) {
        auto deadline = std::chrono::steady_clock::now()
                        + std::chrono::milliseconds(timeout_ms);
        while (std::chrono::steady_clock::now() < deadline) {
            std::lock_guard<std::mutex> lock(mutex_);
            if (new_process_count_ > 0) {
                return true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        return false;
    }

    bool WaitForExit(uint32_t timeout_ms) {
        auto deadline = std::chrono::steady_clock::now()
                        + std::chrono::milliseconds(timeout_ms);
        while (std::chrono::steady_clock::now() < deadline) {
            std::lock_guard<std::mutex> lock(mutex_);
            if (exit_count_ > 0) {
                return true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        return false;
    }

    JobNotification LastExit() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return last_exit_;
    }

    std::vector<JobNotification> Snapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return notifications_;
    }

private:
    mutable std::mutex mutex_;
    std::vector<JobNotification> notifications_;
    uint32_t new_process_count_ = 0;
    uint32_t exit_count_ = 0;
    JobNotification last_exit_;
};

// =============================================================================
// 辅助：pid 是否在列表中
// =============================================================================
static bool ContainsPid(const std::vector<uint32_t>& pids, uint32_t pid) {
    for (uint32_t p : pids) {
        if (p == pid) {
            return true;
        }
    }
    return false;
}

// =============================================================================
// 辅助：大小写不敏感的子串匹配（避免 tolower(int) 的 C4244 告警）
// =============================================================================
static bool ContainsIgnoreCase(const std::string& haystack,
                               const std::string& needle) {
    if (needle.size() > haystack.size()) {
        return false;
    }
    for (size_t i = 0; i + needle.size() <= haystack.size(); ++i) {
        bool match = true;
        for (size_t j = 0; j < needle.size(); ++j) {
            char a = haystack[i + j];
            char b = needle[j];
            if (a >= 'A' && a <= 'Z') a = static_cast<char>(a - 'A' + 'a');
            if (b >= 'A' && b <= 'Z') b = static_cast<char>(b - 'A' + 'a');
            if (a != b) {
                match = false;
                break;
            }
        }
        if (match) {
            return true;
        }
    }
    return false;
}

// =============================================================================
// 辅助：crash_dummy.exe 定位（与 verify_t28 同目录，否则跳过）
// =============================================================================
struct CrashHelper {
    bool available = false;
    std::string path;
};

static CrashHelper FindCrashDummy() {
    CrashHelper h;
    // 以自身 exe 所在目录找 crash_dummy.exe（构建后同在 build/bin）
    wchar_t exe_path[MAX_PATH] = {};
    if (::GetModuleFileNameW(nullptr, exe_path, MAX_PATH) == 0) {
        h.path = "crash_dummy.exe";
        h.available = std::filesystem::exists(h.path);
        return h;
    }
    h.path = (std::filesystem::path(exe_path).parent_path() / "crash_dummy.exe")
                 .string();
    h.available = std::filesystem::exists(h.path);
    return h;
}

static int RunTests() {
    auto logger = Logger::Init("info");

    int passed = 0;
    int failed = 0;

    auto check = [&](bool cond, const std::string& name) {
        if (cond) {
            ++passed;
            spdlog::info("[PASS] {}", name);
        } else {
            ++failed;
            spdlog::error("[FAIL] {}", name);
        }
    };

    // -------------------------------------------------------------------------
    // 测试 1：空 Job 的 QueryProcessList
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 1: QueryProcessList on empty Job ----");

        JobObjectImpl job(logger);
        check(static_cast<bool>(job.Create()), "Job.Create");

        auto r = job.QueryProcessList();
        check(static_cast<bool>(r), "QueryProcessList on empty Job returns Ok");
        if (r) {
            spdlog::info("  pids.size() = {}", r.Value().size());
            check(r.Value().empty(), "empty Job has empty pid list");
        }
    }

    // -------------------------------------------------------------------------
    // 测试 2：Assign 后 QueryProcessList 包含该进程
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 2: QueryProcessList contains launched process ----");

        JobObjectImpl job(logger);
        job.Create();

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "cmd.exe /c ping -n 4 127.0.0.1");
        check(static_cast<bool>(lr), "launch ping -n 4");

        if (lr) {
            uint32_t pid =
                static_cast<uint32_t>(::GetProcessId(lr.Value().process.get()));
            spdlog::info("  ping pid = {}", pid);

            auto q = job.QueryProcessList();
            check(static_cast<bool>(q), "QueryProcessList returns Ok");
            if (q) {
                spdlog::info("  QueryList = {} pids", q.Value().size());
                check(ContainsPid(q.Value(), pid),
                      std::format("launched pid present in list (pid={})", pid));
            }

            launcher.WaitForExit(lr.Value().process.get(), 15000);
        }
    }

    // -------------------------------------------------------------------------
    // 测试 3：进程退出后 QueryProcessList 不再包含
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 3: exited process removed from list ----");

        JobObjectImpl job(logger);
        job.Create();

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job,
                                  "cmd.exe /c ping -n 1 -w 50 127.0.0.1");
        check(static_cast<bool>(lr), "launch quick ping");

        if (lr) {
            uint32_t pid =
                static_cast<uint32_t>(::GetProcessId(lr.Value().process.get()));
            launcher.WaitForExit(lr.Value().process.get(), 10000);

            auto r = job.QueryProcessList();
            check(static_cast<bool>(r), "QueryProcessList after exit returns Ok");
            if (r) {
                bool gone = !ContainsPid(r.Value(), pid);
                spdlog::info("  after exit: {} pids, pid {} gone = {}",
                             r.Value().size(), pid, gone);
                check(gone, "exited process removed from QueryProcessList");
            }
        }
    }

    // -------------------------------------------------------------------------
    // 测试 4：运行中 QueryProcessExitCode == STILL_ACTIVE(259)
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 4: running process exit code is 259 ----");

        JobObjectImpl job(logger);
        job.Create();

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "cmd.exe /c ping -n 4 127.0.0.1");
        check(static_cast<bool>(lr), "launch ping -n 4");

        if (lr) {
            uint32_t pid =
                static_cast<uint32_t>(::GetProcessId(lr.Value().process.get()));
            auto code_r = job.QueryProcessExitCode(pid);
            check(static_cast<bool>(code_r), "QueryProcessExitCode returns Ok");
            if (code_r) {
                spdlog::info("  running exit code = {}", code_r.Value());
                check(code_r.Value() == 259, "running exit code == 259");
            }
            launcher.WaitForExit(lr.Value().process.get(), 15000);
        }
    }

    // -------------------------------------------------------------------------
    // 测试 5：退出后 QueryProcessExitCode == 实际退出码
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 5: exit code after process exits ----");

        JobObjectImpl job(logger);
        job.Create();

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "cmd.exe /c exit 7");
        check(static_cast<bool>(lr), "launch cmd /c exit 7");

        if (lr) {
            auto wait_r = launcher.WaitForExit(lr.Value().process.get(), 10000);
            check(static_cast<bool>(wait_r), "cmd /c exit 7 exits");

            uint32_t pid =
                static_cast<uint32_t>(::GetProcessId(lr.Value().process.get()));
            auto code_r = job.QueryProcessExitCode(pid);
            check(static_cast<bool>(code_r), "QueryProcessExitCode returns Ok");
            if (code_r) {
                spdlog::info("  exited process exit code = {}", code_r.Value());
                check(code_r.Value() == 7,
                      std::format("exit code == 7 (got {})", code_r.Value()));
            }
        }
    }

    // -------------------------------------------------------------------------
    // 测试 6：NEW_PROCESS 通知携带 process_name/process_path
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 6: NewProcess notification carries path ----");

        JobObjectImpl job(logger);
        job.Create();

        NotificationCollector sink;
        auto reg = job.RegisterNotificationSink(sink);
        check(static_cast<bool>(reg), "RegisterNotificationSink");

        ProcessLauncherImpl launcher(logger);
        // 启动进程本体是 cmd.exe（ping 是 cmd 的子进程），NEW_PROCESS 对应 cmd.exe
        auto lr = LaunchAndAssign(launcher, job, "cmd.exe /c ping -n 3 127.0.0.1");
        check(static_cast<bool>(lr), "launch cmd /c ping");

        if (lr) {
            bool got = sink.WaitNewProcess(3000);
            check(got, "received NewProcess notification");
            if (got) {
                auto snapshot = sink.Snapshot();
                JobNotification nyc;
                bool found = false;
                for (auto& n : snapshot) {
                    if (n.type == JobNotificationType::NewProcess) {
                        nyc = n;
                        found = true;
                        break;
                    }
                }
                if (found) {
                    spdlog::info("  process_name = '{}', path = '{}'",
                                 nyc.process_name, nyc.process_path);
                    check(!nyc.process_path.empty(), "NewProcess.path non-empty");
                    check(ContainsIgnoreCase(nyc.process_path, "cmd.exe") ||
                          ContainsIgnoreCase(nyc.process_path, "ping.exe"),
                          std::format("path points to a launched exe (got {})",
                                      nyc.process_path));
                } else {
                    check(false, "NewProcess notification found in snapshot");
                }
            }
            launcher.WaitForExit(lr.Value().process.get(), 15000);
        }
    }

    // -------------------------------------------------------------------------
    // 测试 7：SetCrashSilent 幂等
    // -------------------------------------------------------------------------
    {
        spdlog::info("---- Test 7: SetCrashSilent idempotent ----");

        JobObjectImpl job(logger);
        job.Create();

        auto t1 = job.SetCrashSilent(true);
        check(static_cast<bool>(t1), "SetCrashSilent(true) Ok");
        auto t2 = job.SetCrashSilent(true);
        check(static_cast<bool>(t2), "SetCrashSilent(true) idempotent");
        auto f1 = job.SetCrashSilent(false);
        check(static_cast<bool>(f1), "SetCrashSilent(false) Ok");
    }

    // -------------------------------------------------------------------------
    // 测试 8~11：crash_dummy 场景（依赖同目录，否则跳过）
    // -------------------------------------------------------------------------
    CrashHelper crash = FindCrashDummy();
    if (!crash.available) {
        spdlog::warn("crash_dummy.exe 未与 verify_t28.exe 同目录，跳过测试 8/11");
    }

    // 测试 8：crash_silent=true 崩溃进程 10s 内死亡
    if (crash.available) {
        spdlog::info("---- Test 8: crash_dummy crash (crash_silent=true) ----");

        JobObjectImpl job(logger);
        job.Create();
        job.SetCrashSilent(true);

        NotificationCollector sink;
        job.RegisterNotificationSink(sink);

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "\"" + crash.path + "\"");
        check(static_cast<bool>(lr), "launch crash_dummy");

        if (lr) {
            auto t0 = std::chrono::steady_clock::now();
            auto wait_r = launcher.WaitForExit(lr.Value().process.get(), 10000);
            auto t1 = std::chrono::steady_clock::now();
            auto elapsed_ms =
                std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();

            check(static_cast<bool>(wait_r),
                  std::format("crash_dummy exits within 10s (took {} ms)", elapsed_ms));
            if (wait_r) {
                int32_t code = wait_r.Value();
                spdlog::info("  crash exit code = {}", code);
                check(code != 0, std::format("crash exit code != 0 (got {})", code));

                uint32_t pid =
                    static_cast<uint32_t>(::GetProcessId(lr.Value().process.get()));
                auto q = job.QueryProcessExitCode(pid);
                check(q && q.Value() == static_cast<uint32_t>(code),
                      std::format("QueryProcessExitCode matches WaitForExit (both {})",
                                  code));
            }
            check(sink.WaitForExit(5000), "received exit notification after crash");
        }
    }

    // 测试 9：cmd /c exit 0 → ProcessExitNormal（exit_code == 0）
    {
        spdlog::info("---- Test 9: normal exit classification ----");

        JobObjectImpl job(logger);
        job.Create();

        NotificationCollector sink;
        job.RegisterNotificationSink(sink);

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "cmd.exe /c exit 0");
        check(static_cast<bool>(lr), "launch cmd /c exit 0");

        if (lr) {
            launcher.WaitForExit(lr.Value().process.get(), 10000);
            bool got = sink.WaitForExit(5000);
            check(got, "received exit notification");
            if (got) {
                auto last = sink.LastExit();
                spdlog::info("  exit type = {} exit_code = {} pid = {}",
                             static_cast<int>(last.type),
                             last.exit_code.has_value() ? *last.exit_code : 0xFFFFFFFFu,
                             last.pid);
                check(last.type == JobNotificationType::ProcessExitNormal,
                      "exit 0 classified as ProcessExitNormal");
                if (last.type == JobNotificationType::ProcessExitNormal) {
                    check(last.exit_code.has_value() && *last.exit_code == 0,
                          "normal exit exit_code == 0");
                }
            }
        }
    }

    // 测试 10：cmd /c exit 7 → ProcessExitAbnormal（exit_code == 7）
    {
        spdlog::info("---- Test 10: exit 7 classification ----");

        JobObjectImpl job(logger);
        job.Create();

        NotificationCollector sink;
        job.RegisterNotificationSink(sink);

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "cmd.exe /c exit 7");
        check(static_cast<bool>(lr), "launch cmd /c exit 7");

        if (lr) {
            launcher.WaitForExit(lr.Value().process.get(), 10000);
            bool got = sink.WaitForExit(5000);
            check(got, "received exit notification");
            if (got) {
                auto last = sink.LastExit();
                spdlog::info("  exit type = {} exit_code = {} pid = {}",
                             static_cast<int>(last.type),
                             last.exit_code.has_value() ? *last.exit_code : 0xFFFFFFFFu,
                             last.pid);
                check(last.type == JobNotificationType::ProcessExitAbnormal,
                      "exit 7 classified as ProcessExitAbnormal");
                if (last.type == JobNotificationType::ProcessExitAbnormal) {
                    check(last.exit_code.has_value() && *last.exit_code == 7,
                          "exit 7 exit_code == 7");
                }
            }
        }
    }

    // 测试 11：crash_dummy → ProcessExitAbnormal（exit_code == 0xC0000005）
    if (crash.available) {
        spdlog::info("---- Test 11: crash classification ----");

        JobObjectImpl job(logger);
        job.Create();
        job.SetCrashSilent(true);

        NotificationCollector sink;
        job.RegisterNotificationSink(sink);

        ProcessLauncherImpl launcher(logger);
        auto lr = LaunchAndAssign(launcher, job, "\"" + crash.path + "\"");
        check(static_cast<bool>(lr), "launch crash_dummy");

        if (lr) {
            launcher.WaitForExit(lr.Value().process.get(), 10000);
            bool got = sink.WaitForExit(5000);
            check(got, "received crash exit notification");
            if (got) {
                auto last = sink.LastExit();
                const uint32_t expected = 0xC0000005U;
                spdlog::info("  crash type = {} exit_code = {} (expect 0xC0000005)",
                             static_cast<int>(last.type),
                             last.exit_code.has_value() ? *last.exit_code : 0);
                check(last.type == JobNotificationType::ProcessExitAbnormal,
                      "crash classified as ProcessExitAbnormal");
                check(last.exit_code.has_value() && *last.exit_code == expected,
                      std::format("crash exit_code == 0xC0000005 (got {})",
                                  last.exit_code.has_value() ? *last.exit_code : 0));
            }
        }
    }

    spdlog::info("==== Summary: {} passed, {} failed ====", passed, failed);

    Logger::Shutdown();
    return passed > 0 && failed == 0 ? 0 : 1;
}

int main() {
    try {
        return RunTests();
    } catch (const std::exception& e) {
        spdlog::error("exception: {}", e.what());
        return 2;
    }
}