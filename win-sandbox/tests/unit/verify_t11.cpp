// 头文件编译验证：core 层实体与端口必须无 Win32 依赖
// 编译通过即通过；故意不包含 windows.h
#include "core/entities/JobAccountingInfo.hpp"
#include "core/entities/JobNotification.hpp"
#include "core/entities/ResourceQuota.hpp"
#include "core/entities/SandboxedProcess.hpp"
#include "core/ports/IJobNotificationSink.hpp"
#include "core/ports/IJobObject.hpp"
#include "core/ports/IProcessLauncher.hpp"

#include <cassert>

// 注意：不使用 using namespace winsandbox，避免与 std 名称查找冲突
// 头文件本身没问题，是验证程序写法导致的 MSVC 编译器查找路径异常

// 编译期语义检查：能构造、能传引用、能调虚函数签名
static void CheckCompileOnly() {
    winsandbox::ResourceQuota q;
    q.cpu_ms = 1000;
    q.cpu_rate_percent = 50;
    q.memory_mb = 256;
    q.job_memory_mb = 512;
    q.io_rate_bytes_per_sec = 1024 * 1024;
    q.io_rate_iops = 100;
    q.max_processes = 4;
    q.wall_clock_timeout_ms = 5000;
    q.cpu_timeout_ms = 3000;
    q.no_ui = true;
    q.breakaway_ok = false;
    (void)q;

    winsandbox::JobAccountingInfo a{};
    a.total_user_time_100ns = 1;
    a.read_transfer_count = 2;
    a.active_processes = 3;
    a.peak_process_memory = 4;
    (void)a;

    winsandbox::JobNotification n;
    n.type = winsandbox::JobNotificationType::ProcessExitAbnormal;
    n.pid = 1234;
    n.timestamp_ms = 9999;
    n.process_name = "cmd.exe";
    n.exit_code = 1;
    (void)n;

    winsandbox::SandboxedProcess p;
    p.pid = 100;
    p.command_line = "cmd /c echo hi";
    p.state = winsandbox::ProcessState::Running;
    p.exit_reason = winsandbox::ExitReason::NormalExit;
    (void)p;

    // 端口可被引用持有
    winsandbox::IJobObject* job = nullptr;
    winsandbox::IProcessLauncher* launcher = nullptr;
    winsandbox::IJobNotificationSink* sink = nullptr;
    (void)job; (void)launcher; (void)sink;
}

int main() {
    CheckCompileOnly();
    return 0;
}
