// =============================================================================
// ProcessLauncherImpl 运行时验证
//
// 简单 assert + 控制台输出。
// 覆盖：
//   1. Launch `cmd.exe /c echo hello` → 进程启动成功，pid > 0
//   2. WaitForExit(5s) → 退出码 0
//   3. Terminate 已退出进程 → 返回 ProcessAlreadyExited 错误
//   4. Launch 不存在可执行文件 → 返回 ProcessLaunchFailed 错误
//   5. WaitForExit 超时 → 返回 ProcessStillRunning 错误
//      （用 cmd.exe /c "ping -n 10 127.0.0.1" + 100ms 超时触发）
//
// 不覆盖：
//   - stdout/stderr 内容读取（StreamReader 负责）
//   - Job 资源限制（StartProcessUseCase 集成）
// =============================================================================

#include "core/ports/ILogger.hpp"
#include "infra/logging/Logger.hpp"
#include "infra/process/ProcessLauncherImpl.hpp"

#include <spdlog/spdlog.h>

#include <windows.h>

#include <cassert>
#include <cstdio>
#include <format>
#include <string>

using namespace winsandbox;

static int RunTests() {
    auto logger = Logger::Init("info");

    ProcessLauncherImpl launcher(logger);

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

    // ----- 测试 1：Launch cmd.exe /c echo hello -----
    LaunchRequest req;
    req.command_line = "cmd.exe /c echo hello";
    req.working_dir = "";
    req.create_no_window = true;

    auto launch_r = launcher.Launch(req);
    check(static_cast<bool>(launch_r), "Launch cmd.exe /c echo hello");

    if (launch_r) {
        auto& result = launch_r.Value();

        // 验证 pid > 0
        check(result.process.pid > 0,
              std::format("pid > 0 (actual={})", result.process.pid));

        // 验证句柄非空
        check(result.process_handle != nullptr, "process_handle != null");
        check(result.stdout_read != nullptr, "stdout_read != null");
        check(result.stderr_read != nullptr, "stderr_read != null");
        check(result.stdin_write != nullptr, "stdin_write != null");

        // ----- 测试 2：WaitForExit(5s) → 退出码 0 -----
        auto wait_r = launcher.WaitForExit(result.process_handle, 5000);
        check(static_cast<bool>(wait_r),
              std::format("WaitForExit ok (code={})", wait_r ? wait_r.Value() : -1));
        check(wait_r && wait_r.Value() == 0,
              "exit code == 0");

        // ----- 测试 3：Terminate 已退出进程 → ProcessAlreadyExited -----
        auto term_r = launcher.Terminate(result.process_handle, 1);
        check(!term_r && term_r.Code() == ErrorCode::ProcessAlreadyExited,
              std::format("Terminate exited process → ProcessAlreadyExited (code={}, msg={})",
                          static_cast<int>(term_r.Code()), term_r.Message()));

        // 清理句柄（用 CloseHandle 关闭 void* 句柄）
        CloseHandle(result.process_handle);
        CloseHandle(result.thread_handle);
        CloseHandle(result.stdin_write);
        CloseHandle(result.stdout_read);
        CloseHandle(result.stderr_read);
    }

    // ----- 测试 4：Launch 不存在可执行文件 -----
    LaunchRequest bad_req;
    bad_req.command_line = "this_does_not_exist_12345.exe";
    bad_req.create_no_window = true;

    auto bad_launch_r = launcher.Launch(bad_req);
    check(!bad_launch_r && bad_launch_r.Code() == ErrorCode::ProcessLaunchFailed,
          std::format("Launch non-existent exe → ProcessLaunchFailed (code={}, msg={})",
                      static_cast<int>(bad_launch_r.Code()), bad_launch_r.Message()));

    // ----- 测试 5：WaitForExit 超时 → ProcessStillRunning -----
    LaunchRequest slow_req;
    slow_req.command_line = "cmd.exe /c ping -n 10 127.0.0.1";
    slow_req.create_no_window = true;

    auto slow_launch_r = launcher.Launch(slow_req);
    check(static_cast<bool>(slow_launch_r), "Launch slow process (ping -n 10)");

    if (slow_launch_r) {
        auto& slow_result = slow_launch_r.Value();

        // 100ms 超时，进程肯定还没退出
        auto slow_wait_r = launcher.WaitForExit(slow_result.process_handle, 100);
        check(!slow_wait_r && slow_wait_r.Code() == ErrorCode::ProcessStillRunning,
              std::format("WaitForExit 100ms timeout → ProcessStillRunning (code={})",
                          static_cast<int>(slow_wait_r.Code())));

        // 清理：终止进程
        launcher.Terminate(slow_result.process_handle, 1);
        launcher.WaitForExit(slow_result.process_handle, 3000);

        CloseHandle(slow_result.process_handle);
        CloseHandle(slow_result.thread_handle);
        CloseHandle(slow_result.stdin_write);
        CloseHandle(slow_result.stdout_read);
        CloseHandle(slow_result.stderr_read);
    }

    // 注意：必须先打印 summary 再 Shutdown。
    // Logger::Shutdown() 内部调用 spdlog::shutdown()，会销毁默认 logger；
    // 之后再调用 spdlog::info 会触发 access violation。
    spdlog::info("==== Summary: {} passed, {} failed ====", passed, failed);

    Logger::Shutdown();
    return failed == 0 ? 0 : 1;
}

int main() {
    try {
        return RunTests();
    } catch (const std::exception& e) {
        spdlog::error("exception: {}", e.what());
        return 2;
    }
}
