// =============================================================================
// StartProcessRequest - 启动进程请求（core 层）
//
// 由 start_process 的 JSON payload 解析而来，作为 NativeSandboxedProcess::Execute 的输入。
// 携带命令行、工作目录、环境变量、资源配额、隔离策略。
//
// isolation_policy 收敛为 {net_policy, net_allowlist, clipboard_isolate}，
// 文件系统隔离为 Low IL token 固有语义（无配置项）
// =============================================================================
#pragma once

#include "core/entities/IsolationPolicy.hpp"
#include "core/entities/ResourceQuota.hpp"

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace winsandbox {

struct StartProcessRequest {
    std::string request_id;                     // 关联 start_process 请求的 request_id
    std::string command_line;                   // 完整命令行（含可执行路径），UTF-8
    std::string working_dir;                    // 工作目录，空表示继承父进程

    std::vector<std::pair<std::string, std::string>> env_vars;  // 额外环境变量
    bool inherit_env = true;                    // 是否继承父进程环境变量

    ResourceQuota quota;                        // 资源配额（仅 wall_clock 等 per-process 项生效；
                                                //  Job 级限制由沙箱实例创建 Job 时统一设置）

    // 隔离策略（无 fs 配置项，网络两态 + 剪贴板开关）
    IsolationPolicy isolation_policy;

    // 交互模式标志
    // false（默认）→ Execute 后立即关闭 stdin_write，子进程 ReadFile(stdin) 立即 EOF
    // true          → 保留 stdin_write，可后续通过 WriteStdin 命令写入数据
    //                 析构或进程退出时关闭 stdin_write
    // 适用场景：REPL（python -i）、shell（cmd /k）、长跑服务
    bool interactive = false;

    // 启动时一次性写入 stdin 的数据（在 Execute 内写入；interactive=true 时
    // 同样写入，随后保留 stdin_write 供后续 WriteStdin）
    // 留空时：interactive=false 的 stdin_write 由 Execute 立即关闭（子进程读 EOF），
    //         interactive=true 保留 stdin_write
    std::string stdin_data;

    // 沙箱内部进程 ID，由沙箱实例分配后填入
    // Execute 内会把它存到 SandboxedProcess.process_id，并出现在所有事件 payload 中
    // 调用方（沙箱实例）负责分配，NativeSandboxedProcess 只读取
    uint32_t process_id = 0;

    // 单次 ReadFile 缓冲大小（字节）
    // 0（默认）→ 用默认值（64KB）
    // >0       → 用指定大小（用于触发大块 stdout 输出）
    // 注意：此值会 commit 全部内存（vector 初始化时触摸所有字节），设大值会按进程×流数线性增加内存占用
    size_t stream_buffer_size = 0;

    // ConPTY 伪控制台句柄（HPCON），由外部创建并传入
    // 非空时 Execute 走 ConPTY 路径：子进程 stdio 由 ConPTY 驱动分配
    //   - 不创建匿名管道，LaunchResult.stdin_write/stdout_read/stderr_read 为 nullptr
    //   - 子进程 isatty() 返回 true，支持全屏 TUI / resize / VT 序列
    // 为空时走匿名管道路径（默认行为）
    void* hpcon = nullptr;
};

} // namespace winsandbox
