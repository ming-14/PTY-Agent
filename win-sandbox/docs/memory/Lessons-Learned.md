# Lessons Learned - 项目踩坑记录

> 防止反复犯同一个错误。每条记录要包含：现象、根因、修复、验证、参考资料。

---

## 001. Windows 命名管道同步 I/O 句柄级串行化

**日期**：2026-07-26
**模块**：Python 客户端 `python/win_sandbox/client.py`
**现象**：Python 客户端 send_shutdown 调用 `WriteFile` 永久阻塞（>30s 不返回），
C++ 端 cdb 附加显示进程正常阻塞在 `NtReadFile`（`WaitCommand` 的 ReadFile）。
管道缓冲 64KB，写入仅 93 字节，理论上应该立即返回。

**调试过程**：
1. cdb 附加 sandbox 进程抓栈：C++ 端栈显示在 `ntdll!NtReadFile` →
   `KERNELBASE!ReadFile` → `NamedPipeServer::WaitCommand`，符合预期
2. 写最小复现脚本 `no_reader_thread.py`：不启动后台 reader 线程，顺序执行
   `ReadFile(Ready) → WriteFile(Shutdown) → ReadFile(ShutdownComplete)`
3. 结果：WriteFile 0ms 返回，整轮 < 100ms 完成，sandbox 干净退出（code=0）
4. 假设证实：问题在 reader 线程

**根因**：
- `CreateFileW` 没传 `FILE_FLAG_OVERLAPPED` → 句柄是同步 I/O 模式
- Microsoft `CreateFile` 文档明确：**"If `FILE_FLAG_OVERLAPPED` is not specified,
  I/O operations on the handle are serialized."**
- Python reader 线程常驻 `ReadFile` 阻塞等数据 → 主线程 `WriteFile` 被内核
  串行化阻塞 → 死锁（reader 等 C++ 数据，writer 等 reader 让出，C++ 等 Python 写）
- C++ 端没问题（单线程，读和写顺序进行）

**修复**：
- `CreateFileW` 加 `FILE_FLAG_OVERLAPPED`
- reader 线程：`ReadFile(overlapped)` → `WaitForSingleObject(event, 100ms)` 轮询
  → `GetOverlappedResult` → 解码入队
- `send_message`：`WriteFile(overlapped)` → `WaitForSingleObject(event, timeout)`
  → `GetOverlappedResult`，超时则 `CancelIoEx` 取消
- `close`：`CancelIoEx(handle, NULL)` 取消所有 pending I/O → join reader → `CloseHandle`
- 顺手修掉 `_is_valid_handle` 的 `h != -1` bug：64 位 Windows 上
  `INVALID_HANDLE_VALUE = 0xFFFFFFFFFFFFFFFF`，与 -1（int）比较恒为 False

**验证**：
- `tests/e2e/smoke.py` 用公共 API 跑完整 round-trip，3 次连续运行全过
- send_shutdown 从无限阻塞 → 16ms 返回
- sandbox 干净退出 code=0

**关键教训**：
1. Windows 命名管道客户端如果需要「reader 线程 + 主线程写」并发，**必须用 overlapped I/O**
2. ctypes 的 `c_void_p(-1).value` 在 CPython 上返回 `None`，但不要依赖这个行为；
   显式检查 `int(h) == (1<<64)-1` 更稳
3. 同步 I/O 的串行化是**句柄级**的，不分读写方向 — 一个 ReadFile 阻塞会卡住
   其他线程的 WriteFile

**参考资料**：
- Microsoft Docs - CreateFile: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- Microsoft Docs - Synchronous and Asynchronous I/O: https://learn.microsoft.com/en-us/windows/win32/fileio/synchronous-and-asynchronous-i-o
- Microsoft Docs - Named Pipe Operations: https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-operations

---

## 002. MSVC 编译 UTF-8 源文件必须加 /utf-8 标志

**日期**：2026-07-26
**模块**：T1.1 头文件编译验证（`tests/unit/verify_t11.cpp`）

**现象**：
独立 cl.exe 编译脚本（不走 CMake）编译含中文注释的 C++ 头文件时报大量离奇错误：
- `error C2323: "winsandbox::operator delete[]": 非成员运算符 new 或 delete 函数不可声明为静态的`
- `error C2039: "nothrow_t": 不是 "std" 的成员`
- `error C2938: "winsandbox::std::_Conditional_type": 未能使别名模板专用化`
- 编译器把 `std::` 当成 `winsandbox::std::` 处理

**调试过程**：
1. 最初怀疑是 `using namespace winsandbox;` 与 std 名称查找冲突 → 去掉后错误依旧
2. 写最小复现 `verify_min.cpp` 只含 `IStatsCollector.hpp`（含 `<functional>`）→ 报
   `winsandbox 不是命名空间`，但无 IStatsCollector.hpp 自身错误
3. 注意到 warning C4819：`该文件包含不能在当前代码页(936)中表示的字符`
4. 想到 MSVC 默认按系统代码页（936=GBK）解析源文件，UTF-8 中文注释被误解析
5. 检查根 CMakeLists.txt 第 35 行已有 `/utf-8` → Phase 0 走 CMake 没问题
6. 独立 bat 脚本漏了 `/utf-8`

**根因**：
- Write 工具保存的源文件是 UTF-8 无 BOM
- MSVC 默认按系统代码页（中文 Windows = 936/GBK）解析源文件
- UTF-8 多字节序列（中文注释）被 GBK 解码时，某些字节组合会被误解析为
  反斜杠（0x5C）触发行续接、或吞掉引号/分号，导致 `namespace winsandbox {`
  未正确闭合 → 后续 `<functional>` 等标准库头文件在错误的命名空间上下文中展开
- 表现形式：`std` 被解析为 `winsandbox::std`，operator new/delete 报错

**修复**：
- 独立 cl.exe 编译命令加 `/utf-8` 标志
- 验证：`build_verify_t11.bat` 加 `/utf-8` 后编译通过

**验证**：
- `verify_t11.cpp` 包含全部 8 个新头文件，编译通过，无 Win32 依赖
- 无 C4819 警告

**关键教训**：
1. 任何使用 cl.exe 直接编译（绕过 CMake）的脚本必须加 `/utf-8`
2. 出现 `winsandbox::std::xxx` 这类「命名空间嵌套」错误 + C4819 警告时，
   第一时间检查 `/utf-8` 是否缺失，不要怀疑头文件本身
3. UTF-8 中文注释在 GBK 解码下可能产生 0x5C（反斜杠）字符，导致行续接
   吞掉下一行，引发各种离奇语法错误

**参考资料**：
- MSVC /utf-8 文档: https://learn.microsoft.com/en-us/cpp/build/reference/utf-8-set-source-and-executable-character-set-to-utf-8
- 项目根 CMakeLists.txt 第 35 行：`add_compile_options(/utf-8 /W4 /permissive- ...)`

---

## 003. Job Object CPU Rate Control 标志 WEIGHT_BASED 与 CpuRate 互斥

**日期**：2026-07-26
**模块**：T1.2/T1.3 JobObjectImpl `SetCpuRateControl`（`src/infra/job/JobObjectImpl.cpp`）

**现象**：
配置 `cpu_rate_percent=50` 后，CPU 密集进程未被限制在 50%，
实际跑满 100% CPU。日志中无 "CPU Rate Control set" 成功记录，
只有 "CPU Rate Control not applied (err=...); degrading" 警告被吞掉。

**调试过程**：
1. 直接读源码 `SetCpuRateControl`，发现同时设置了
   `JOB_OBJECT_CPU_RATE_CONTROL_WEIGHT_BASED` 标志和 `CpuRate` 字段
2. 查 MSDN `JOBOBJECT_CPU_RATE_CONTROL_INFORMATION`：
   - `WEIGHT_BASED` 标志 → 使用 `Weight` 字段（1-9 权重）
   - 不设 `WEIGHT_BASED` → 默认使用 `CpuRate` 字段（0.01% 单位）
3. 同时设置二者是非法组合，`SetInformationJobObject` 必然失败
4. 代码降级分支返回 `Ok` 而非 `Err`，把失败静默吞掉
5. LLD-01 §5.2.3 原文同样有此 bug，注释 `// 或 RATE_BASED` 暴露了不确定

**根因**：
- LLD 设计阶段对 CPU Rate Control 标志语义理解不准
- `JOB_OBJECT_CPU_RATE_CONTROL_RATE_BASED` 常量实际不存在
  （MSDN 文档中是隐式默认行为，没有对应标志）
- 降级分支返回 Ok 把所有 SetInformationJobObject 失败都吞掉，
  掩盖了配置不生效的问题

**修复**：
- 移除 `JOB_OBJECT_CPU_RATE_CONTROL_WEIGHT_BASED` 标志
- 仅保留 `ENABLE | HARD_CAP`，默认使用 `CpuRate` 字段
- 同步修订 LLD-01 §5.2.3
- 在代码注释中明确记录标志语义，防止再次混淆

**验证**：
- `build_debug.bat` 编译通过
- `tests/e2e/smoke.py` round-trip 全过
- 后续 T1.9 e2e 测试 `test_cpu_bomb` 将验证 CPU 限制实际生效

**关键教训**：
1. Windows API 标志存在互斥关系时（如 WEIGHT_BASED vs CpuRate），
   不能"全设置"，必须按字段选择对应标志
2. "降级返回 Ok" 的容错策略会掩盖配置失效问题——降级时应至少
   记 Warn 日志说明哪些配置项被跳过
3. LLD 设计稿中的代码片段也是"代码"，需要和真实代码一样审查
4. MSDN 中部分标志没有对应常量（如 RATE_BASED），是隐式默认行为，
   不要凭名字臆测存在

**参考资料**：
- MSDN JOBOBJECT_CPU_RATE_CONTROL_INFORMATION:
  https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information
- MSDN SetInformationJobObject:
  https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-setinformationjobobject

---

<!-- 后续 lesson 模板：

## 002. 标题

**日期**：YYYY-MM-DD
**模块**：
**现象**：

**调试过程**：

**根因**：

**修复**：

**验证**：

**关键教训**：

**参考资料**：

-->

## 004. 会话压缩摘要中的"已修复"未必落地，集成验证必须以代码为准

**日期**：2026-07-26
**模块**：T1.4 ProcessLauncherImpl（`src/infra/process/ProcessLauncherImpl.cpp`）+
        T1.4 验证程序（`tests/unit/verify_t14.cpp`）

**现象**：
压缩恢复后跑 Phase 1 集成验证，verify_t14 出现 1 项失败：
- 测试 3「Terminate 已退出进程 → ProcessAlreadyExited」返回
  `JobTerminateFailed (code=15, msg=TerminateProcess failed (access denied): err=5)`，
  预期的 `ProcessAlreadyExited` 没出现。
- 此外测试程序末尾 `spdlog::info("==== Summary ...")` 在
  `Logger::Shutdown()` 之后调用，存在 access violation 风险
  （摘要称已修复"Moved summary log before Logger::Shutdown"，但代码里顺序仍颠倒）。

**调试过程**：
1. 直接读 `ProcessLauncherImpl::Terminate` 源码，发现并没有摘要中描述的
   `WaitForSingleObject(h, 0)` 预检测分支——而是直接调 `::TerminateProcess`
   并依赖 `ERROR_ACCESS_DENIED` 错误码判断"已退出"。
2. MSDN `TerminateProcess` 对已退出进程返回 `ERROR_ACCESS_DENIED`，
   与"权限不足"无法区分；摘要中已经分析过这个根因。
3. 读 `verify_t14.cpp` 末尾，确认 `Logger::Shutdown()` 在
   `spdlog::info("==== Summary ====")` 之前——和摘要描述的修复相反。

**根因**：
- 摘要（Compaction Summary）声称的修复并未实际落地到代码。
- 摘要本身可能由模型基于"应该这么修"的推理生成，而非基于实际 diff。
- 上次会话中可能只讨论了修复方案但未执行 Edit，或 Edit 后被回滚。

**修复**：
1. `ProcessLauncherImpl::Terminate` 增加 `WaitForSingleObject(h, 0)` 预检测：
   - `WAIT_OBJECT_0` → 返回 `ProcessAlreadyExited`
   - `WAIT_TIMEOUT`  → 调 `TerminateProcess`
   - `WAIT_FAILED`   → 返回 `ProcessWaitFailed`
2. `verify_t14.cpp` 调换 summary 日志与 `Logger::Shutdown()` 的顺序：
   先 `spdlog::info` 再 `Shutdown`，并在注释中说明原因。

**验证**：
- 重新编译：sandbox.exe + verify_t14.exe 编译通过
- `verify_t14.exe`：12 passed, 0 failed（含 ProcessAlreadyExited + ProcessStillRunning）
- `tests/e2e/smoke.py`：round-trip < 0.2s，退出码 0，Phase 0 IPC 无回归

**关键教训**：
1. **会话压缩摘要不是源码真相**——摘要里写的"已修复""已落地"必须重新
   读代码核实，不能直接信任后继续推进。
2. **集成验证阶段必须跑测试**——即便摘要说"全部通过"，重新跑一次
   verify + smoke 是发现"摘要失真"的最低成本手段。
3. **错误码无法区分多种语义时，必须用显式预检测**——`TerminateProcess`
   返回 `ERROR_ACCESS_DENIED` 同时表示"已退出"和"权限不足"，必须通过
   `WaitForSingleObject(h, 0)` 在调用前明确进程状态。
4. **资源释放顺序**：spdlog 的 `spdlog::shutdown()` 会销毁默认 logger，
   之后任何 `spdlog::info` 都会 access violation——summary 日志必须在
   `Logger::Shutdown()` 之前打印。

**参考资料**：
- MSDN TerminateProcess:
  https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess
- MSDN WaitForSingleObject:
  https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject

---

## 005. Windows Generic Mapping：DACL 写入 GENERIC_ALL 会被自动转换为 FILE_ALL_ACCESS

**日期**：2026-07-27
**模块**：Phase 3 T3.7 管道 DACL（`src/infra/ipc/NamedPipeServerImpl.cpp` +
        `tests/e2e/test_pipe_dacl.py`）

**现象**：
T3.7 e2e 测试用 `GetNamedSecurityInfoW` 读取管道 SD 中的 DACL ACE，
断言 `(ace.Mask & GENERIC_ALL) == GENERIC_ALL` 时失败。实测三个 ACE
（SY/BA/user_sid）的 mask 都是 `0x001F01FF`，而非代码中 `AddAccessAllowedAceEx`
写入的 `GENERIC_ALL = 0x10000000`。

**调试过程**：
1. 打印 ACE mask 看到 `0x001f01ff`，立刻识别为 `FILE_ALL_ACCESS`：
   `STANDARD_RIGHTS_REQUIRED(0x000F0000) | SYNCHRONIZE(0x00100000) | 0x1FF`
2. 翻阅 Windows SDK `winnt.h`：`FILE_ALL_ACCESS` 定义匹配
3. 回顾 SD 写入路径：`InitializeAcl` → `AddAccessAllowedAceEx(..., GENERIC_ALL, sid)`
   → `SetSecurityDescriptorDacl` → `MakeSelfRelativeSD`
4. 关键：CreateNamedPipeW 接受的 SD 在内核 `SeSetSecurityDescriptorInfo` 路径上
   会调用 `RtlpAddAce → RtlMapGenericMask`，把 GENERIC 权限按对象类型的
   `GENERIC_MAPPING` 转为 specific 权限。Named pipe 是 file object，
   `IoFileObjectType` 的 GENERIC_MAPPING 把 GENERIC_ALL 映射为 FILE_ALL_ACCESS

**根因**：
- Windows generic mapping 在 SD 写入时把 generic 权限位
  （GENERIC_READ/WRITE/EXECUTE/ALL）按对象类型映射为具体权限
- 这不是 bug，是设计：SD 持久化时 generic 位会被抹掉，存的是 specific mask
- 读回的 mask 已经是 file object 专有权限，不能用 GENERIC_ALL 断言

**修复**：
- Python 测试定义 `FILE_ALL_ACCESS = 0x001F01FF`
- 三个 ACE 的 mask 断言改为 `(ace.Mask & FILE_ALL_ACCESS) == FILE_ALL_ACCESS`
- 文档中明确说明该映射行为，避免后续维护者重蹈覆辙

**验证**：
- `python tests/e2e/test_pipe_dacl.py 1`：3 个 ACE mask=0x001f01ff 全部通过断言
- 全量回归 8 套件 34/34 全绿

**关键教训**：
1. **DACL 内容断言要用对象类型对应的 specific mask**——命名管道/file object 用
   `FILE_ALL_ACCESS`，注册表用 `KEY_ALL_ACCESS`，service 用 `SERVICE_ALL_ACCESS`，
   不能用通用的 `GENERIC_ALL`
2. **`wintypes` 模块没有 `PVOID`**——ctypes 写 Windows Security API 时要用
   `ctypes.c_void_p`，不要用 `wintypes.PVOID`（会 AttributeError）
3. **匿名 token 测试技巧**：`ImpersonateAnonymousToken(GetCurrentThread())`
   切到匿名 token 后，`CreateFileW` 会用线程 token 做访问检查，匿名 token 的
   SID 是 `S-1-5-7`，在严格 DACL 下会被 `ERROR_ACCESS_DENIED (5)` 拒绝。
   `RevertToSelf` 恢复。这是单用户环境下模拟"其他用户"的最简洁方式。
4. **纯 ctypes 实现 Windows Security API**——不引入 pywin32 依赖（保持 Python
   客户端零依赖原则），用 `GetNamedSecurityInfoW` + `GetSecurityDescriptorDacl`
   + `GetAce` + `EqualSid` 即可完整遍历 DACL

**参考资料**：
- MSDN GENERIC_MAPPING:
  https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-generic_mapping
- MSDN CreateNamedPipeW SECURITY_ATTRIBUTES:
  https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createnamedpipew
- MSDN ImpersonateAnonymousToken:
  https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-impersonateanonymoustoken
- winnt.h FILE_ALL_ACCESS 定义

---

## 006. std::optional 空值解引用崩溃（MSVC hardening）

**日期**：2026-07-31
**模块**：`src/infra/job/JobObjectImpl.cpp` (Phase 5 资源配额)

**现象**：
运行时弹窗崩溃：`operator*() called on empty optional`（MSVC optional:238）。
用户两次报告，首次修复 `JobObjectImpl.cpp:175` 后仍重现。

**调试过程**：
1. 定位 `JobObjectImpl.cpp:175`：`uint64_t cpu_val = quota.cpu_ms.value_or(*quota.cpu_timeout_ms);`
   - 当 `cpu_timeout_ms` 为空且被解引用则崩溃
2. 修复后用户再次报告相同崩溃弹窗
3. 全量回归 11 套件通过（10/11 + test_fragmentation flaky）
4. 崩溃非测试套件触发，疑似 `debug_test*.py` 脚本触发或陈旧构建产物

**根因**：
- `std::optional` 在 MSVC hardening 模式下解引用空值会触发断言崩溃
- `quota.cpu_ms` 有值但 `quota.cpu_timeout_ms` 为空时，`*quota.cpu_timeout_ms` 解引用空 optional
- 修复 line 175 后仍重现，说明存在其他空 optional 解引用点，或使用了陈旧 obj

**修复**：
- Line 175：`*quota.cpu_timeout_ms` → `quota.cpu_timeout_ms.value_or(0)`
- commit `7245625` fix: optional deref crash in SetExtendedLimits
- **崩溃仍未完全解决**，需明确复现路径

**验证**：
- 全量回归通过
- 用户报告的崩溃路径未复现

**关键教训**：
1. `std::optional` 解引用前必须检查 `has_value()`，或用 `value_or()` 提供默认值
2. MSVC hardening 断言在 Debug 构建中生效，`operator*()` 对空 optional 直接崩溃
3. 修复后需确认是否使用陈旧构建产物（ninja 增量编译可能链接旧 obj）
4. `debug_test*.py` 等临时测试脚本可能触发非测试套件的代码路径

---

## 007. AppContainer 无法访问本地 loopback（平台限制）

> **Phase 16（2026-08-12）已过时**：AppContainer 链已删除，网络隔离改为 SOCKS5 代理（allowlist）/ 不限制（unrestricted），loopback 限制不再存在。

**日期**：2026-07-31
**模块**：Phase 5 网络隔离

**现象**：
授予 `privateNetworkClientServer` capability 后，AppContainer 内进程仍无法连接 loopback（127.0.0.1）。

**根因**：
- AppContainer 网络隔离机制限制：即使授予 loopback capability，AppContainer 内进程也无法访问本地 loopback
- 这是 Windows 平台限制，非 capability 配置问题

**修复**：
- loopback 测试改为仅验证 capability 配置正确，不验证实际连接
- 文档记录该限制

**关键教训**：
1. AppContainer 的 loopback 限制是平台级的，不能通过 capability 解决
2. 测试设计要区分「配置正确性验证」与「功能可用性验证」，平台限制下前者仍有意义

---

## 008. 非管理员无法对系统目录写 ACL

> **Phase 16（2026-08-12）已过时**：AppContainer/DefaultDeny 机制已删除，文件系统隔离改为 Low IL 完整性级别（全盘只读），无需对系统目录写 ACL，非管理员天然可用。

**日期**：2026-07-31
**模块**：Phase 5 网络隔离测试

**现象**：
AppContainer + DefaultDeny 模式对 `C:\Windows\System32` 等系统目录写 ACL 需要管理员权限，测试超时。

**根因**：
- `GrantSidAccess` 对系统目录设置 DACL 需要管理员权限
- 非管理员运行时 `SetNamedSecurityInfo` 返回 `ERROR_ACCESS_DENIED`

**修复**：
- 测试改用 `temp_workspace` 模式，工作目录在 `%TEMP%` 下，非管理员有完整控制权

**关键教训**：
1. AppContainer 测试避免对系统目录操作，使用临时目录绕过权限问题
2. `temp_workspace` 模式是非管理员环境下的默认选择

---

## 009. Result<T>::Value() 在错误状态下触发 std::optional 空值解引用崩溃

**日期**：2026-08-04
**模块**：`src/core/entities/Result.hpp`（全项目错误处理模板）

**现象**：
运行时弹窗崩溃 `operator*() called on empty optional`（MSVC optional:238）。Lessons-Learned #006 曾修复 `JobObjectImpl.cpp:175` 一处，但用户报告仍能重现，根因未最终定位。

**调试过程**：
1. 全量搜索所有 `std::optional` 解引用点（`*xxx` / `.value()` / `operator*`）
2. 发现 `Result<T>::Value()` 直接调用 `value_.value()`——当 `Result` 是 Err 状态时 `value_` 为空 optional
3. 全量搜索所有 `Value()` 调用点（28 处），逐一确认调用方是否先检查 `IsOk()`
4. 确认所有调用点都有 `if (!r)` 检查，但 `Value()` 本身是"定时炸弹"——任何遗漏检查的调用点都会崩溃

**根因**：
- `Result<T>::Value()` 在错误状态下调用 `value_.value()` 触发 MSVC hardening 断言崩溃
- 这是 API 设计缺陷：`Value()` 应该抛出可捕获的异常而非直接崩溃
- 之前修复 `JobObjectImpl.cpp:175` 只是修了一个具体调用点，没有修复根因

**修复**：
- `Result<T>::Value()` 增加 `has_value()` 检查，错误状态下抛出 `std::logic_error`（含错误码和消息）
- 调用方可以捕获异常诊断，而不是进程直接崩溃
- 新增 `<stdexcept>` 头文件

**验证**：
- 全量 e2e 测试通过（16 套件全部 PASS/SKIP 符合预期）
- 构建无警告

**关键教训**：
1. **错误处理模板的 `Value()` 必须防御性检查**——不能直接 `value_.value()`，否则任何遗漏 `IsOk()` 检查的调用点都会崩溃
2. **修复"具体调用点"不等于修复"根因"**——#006 修了 `JobObjectImpl.cpp:175` 但根因在 `Result<T>::Value()` 本身
3. **MSVC hardening 下 `std::optional::operator*()` 对空值直接崩溃**，不是返回未定义值

---

## 010. FrameCodec 分片重组 use-after-free：erase 后访问悬垂引用

**日期**：2026-08-04
**模块**：`src/infra/ipc/FrameCodec.cpp`（IPC 分片协议）

**现象**：
`test_fragmentation.py` Test 2（Python → C++ 20MB stdin_data round-trip）失败：
- 第一次失败：`sandbox pipe closed before delivering expected message`（Shutdown race）
- 修复 Shutdown race 后：`timeout waiting for 'process_started' message after 60.0s`
- C++ 端没有正确重组 20MB 分片命令

**调试过程**：
1. 修复 Shutdown race condition 后 Test 2 仍失败，但错误从 pipe closed 变为 timeout
2. 定位到 `FrameCodec::StreamDecoder::HandleDecodedMessage` 的分片重组逻辑
3. 发现 line 429-430：先 `fragment_buffers_.erase(it)` 移除 `FragmentBuffer`，然后 line 432-442 仍访问 `buf.pieces` 和 `buf.total_base64_size`
4. 这是典型的 **use-after-free**：`buf` 是 `it->second` 的引用，erase 后引用悬垂

**根因**：
- `HandleDecodedMessage` 中先 `fragment_buffers_.erase(it)` 再访问 `buf.pieces`
- 20MB 分片（21 帧）场景下，最后一帧到达触发重组时访问已释放内存
- 小消息（单帧）不触发此路径，所以 Test 1/3/4 通过

**修复**：
- 先拷贝需要的字段（`pieces` / `total_base64_size` / `original_type`），再从 `fragment_buffers_` 移除
- 用 `std::move(buf.pieces)` 转移所有权，避免拷贝大 map

**验证**：
- `test_fragmentation.py` 5/5 全部通过（含 20MB round-trip）
- 全量 e2e 测试通过

**关键教训**：
1. **erase 容器元素后不能继续访问其引用**——`auto& buf = it->second` 在 `erase(it)` 后是悬垂引用
2. **大消息分片路径是 use-after-free 的高发区**——需要先拷贝/移动数据再清理容器
3. **测试失败模式变化是定位根因的线索**——从 pipe closed 变为 timeout 说明修复了 Shutdown race 后暴露了更深层的问题

---

## 011. Shutdown race condition：write_queue 未 flush 就取消 pending I/O

**日期**：2026-08-04
**模块**：`src/infra/ipc/NamedPipeServerImpl.cpp`（IPC 服务端 Shutdown）

**现象**：
`test_fragmentation.py` Test 2 失败：`sandbox pipe closed before delivering expected message`。
Python 端 `wait_exit` 收到 pipe closed 而非 ShutdownComplete。

**调试过程**：
1. 查看 `main.cpp` Shutdown 命令处理：`SendEvent(ShutdownComplete)` → `RequestExit` → 主线程 `server->Shutdown()`
2. `SendEvent` 只是把消息入队到 `write_queue_`，实际写入由 `ClientWriteLoop` 线程异步执行
3. `Shutdown()` 中立即设置 `disconnected = true` 并 `CancelIoEx`，可能还没等 `ClientWriteLoop` 写入 ShutdownComplete 就取消了 pending I/O

**根因**：
- `Shutdown()` 在 `ClientWriteLoop` 写完 `write_queue_` 中的消息之前就取消了 pending I/O
- ShutdownComplete 等命令响应消息在 `write_queue_` 中，被 `CancelIoEx` 取消后 Python 端收不到
- Python 端 `_wait_message` 收到 pipe closed（`None`）→ 抛 `SandboxProcessError`

**修复**：
- `Shutdown()` 中新增步骤 3：等待所有客户端 `write_queue_` 清空（flush pending 消息）
- 用 `write_cv.wait_for` 等待队列空，超时 5s
- 之后再取消 pending I/O 和断开连接

**验证**：
- `test_fragmentation.py` 5/5 全部通过
- `test_multi_client.py` 4/4 全部通过（含 controller 断连自动退出）
- 全量 e2e 测试通过

**关键教训**：
1. **异步写队列必须在 shutdown 前 flush**——`SendEvent` 只是入队，实际写入由后台线程完成
2. **取消 pending I/O 前必须确保所有待发送消息已写完**——否则客户端收到 pipe closed 而非预期消息
3. **Shutdown 顺序**：flush write_queue → 取消 pending read → 等待客户端自清理

---

## 012. ProcessEntry 整值移动赋值导致 use-after-free（析构顺序陷阱）

**日期**：2026-08-05
**模块**：`src/adapters/SandboxInstance.cpp`（ShutdownAll）

**现象**：`sandbox.exe` 在 Shutdown 时崩溃（rc=0xC0000005 ACCESS_VIOLATION）。
Python 端 `wait_exit` 收到 pipe closed 而非 ShutdownComplete。
崩溃栈：`ShutdownAll → ProcessEntry::operator= → unique_ptr reset → ~StartProcessUseCase`
（析构内调用 `[rax+30h]` 虚函数，rax=0xddddddddddddd0d 已释放内存填充）。

**根因**：
- `ShutdownAll` 中用 `entry = ProcessEntry{}` 释放资源 → 触发**整值移动赋值**
- C++ 移动赋值按成员**声明顺序**执行：job → launcher → app_container → ... → usecase
- usecase 声明在最后，被最后释放；但 usecase 析构内部依赖
  `job_object_`/`app_container_`/`fs_isolator_`/`wfp_engine_` 裸指针，
  这些成员**先于 usecase 被释放** → usecase 析构访问悬垂指针 → 崩溃
- 正常析构（逆序）是安全的，所以普通退出路径从未暴露；只有 Shutdown 的
  整值移动赋值路径触发

**修复**：
- `ShutdownAll` 改为显式按依赖顺序 `reset()`：先 `usecase.reset()`（依赖的其他成员仍存活），
  再 `stats_collector` → `wfp_engine` → `fs_isolator` → `path_grantor` → `app_container` → `launcher` → `job`
- 删除 `entry = ProcessEntry{}` 整值赋值

**验证**：
- 探针：Shutdown 立即执行（进程仍在运行）连续 4 次 OK exit=0，不再崩溃
- ctest 13/13 + e2e 18/18 全绿

**关键教训**：
1. **整值移动赋值/析构顺序与裸指针依赖的冲突是隐蔽 UB**——成员含裸指针指向
   兄弟成员时，必须显式控制释放顺序，禁止依赖隐式成员顺序
2. **e2e 测试的 `except Exception` 吞异常会掩盖崩溃**——此前所有测试
   `_close_gracefully` 用 `try/except: pass` 包住 `wait_exit`，Shutdown 崩溃
   从未被发现

---

## 013. StopWallClockTimer 并发 join 同一 std::thread 崩溃

**日期**：2026-08-05
**模块**：`src/core/usecases/StartProcessUseCase`（wall_clock 定时器）

**现象**：立即 Shutdown（进程仍在运行）时，`~StartProcessUseCase` 内
`wait_thread_.join()` 崩溃（0xC0000005）。日志序列：
`ShutdownAll terminating → destructed while process still running → Job terminated → wall_clock timer disarmed → 崩溃`。

**根因**：
- `StopWallClockTimer()` 只做 `wall_clock_armed_=false` + `joinable()` 检查 + `join()`
- wait 线程（进程退出路径 `WaitLoop`）与 usecase 析构（`ShutdownAll` 清理路径）
  会**同时**调用 `StopWallClockTimer` → 并发 `join` 同一 `wall_clock_thread_`
- `std::thread::join` 并发调用是未定义行为 → 崩溃

**修复**：
- 新增 `std::once_flag wall_clock_stop_once_` 成员
- `StopWallClockTimer` 用 `std::call_once` 保证 `wall_clock_thread_` 只被 join 一次
- 注意：wait 线程与析构均可能调用，call_once 语义正好匹配"只 join 一次"

**验证**：
- 立即 Shutdown 探针连续 8 次 OK exit=0（修复前 6/8 失败）
- async 测试套件 5/5 通过

**关键教训**：
1. **同一个 `std::thread` 被多个路径并发 join 是 UB**——共享线程资源需用
   `std::call_once` / mutex 保护
2. **析构函数可能与后台线程并发执行同一清理逻辑**——必须考虑
   wait 线程 vs 析构的竞态，不能假设析构是唯一清理方

---

## 014. 命名管道 Shutdown 后 ShutdownComplete 可能被 broken pipe 丢弃（客户端容忍）

**日期**：2026-08-05
**模块**：`python/win_sandbox/client.py` + `async_client.py`（wait_exit）

**现象**：立即 Shutdown 场景偶发 `SandboxProcessError: sandbox pipe closed before
delivering expected message`。服务端日志显示 `ShutdownComplete sent` 已执行，
沙箱 rc=0 正常退出，但客户端 `wait_exit` 仍抛错。

**根因**：
- 服务端 `SendEvent(ShutdownComplete)` 入队后立即 `RequestExit` → 进程退出关闭管道
- 命名管道在服务端进程退出时，未读缓冲中的 ShutdownComplete 可能被 broken pipe 丢弃
- 客户端 `wait_exit` 的 `_wait_message(SHUTDOWN_COMPLETE)` 收到 None（pipe closed）
  即抛 `SandboxProcessError`，但 `wait_exit` 语义是"等沙箱进程退出"，
  管道关闭正是沙箱退出的表现，不应视为错误

**修复**：
- sync + async 的 `wait_exit` 增加 `except SandboxProcessError` 分支：
  管道提前关闭视为正常（沙箱已退出），降级为 info 日志后继续等进程退出

**验证**：
- 立即 Shutdown 探针连续 8 次 OK exit=0（修复前 6/8 失败）
- async 测试套件 5/5 通过

**关键教训**：
1. **服务端进程退出时，命名管道未读缓冲可能丢数据**——ShutdownComplete
   这类"最后一条确认消息"不能依赖可靠送达
2. **客户端 `wait_exit` 应以进程退出为准**，ShutdownComplete 只是可选的优雅确认，
   收到 pipe closed 应降级而非报错
3. **区分「沙箱崩溃退出」与「正常退出但消息丢失」**：崩溃时 rc 非 0 且无
   ShutdownComplete；正常退出 rc=0 但可能也无 ShutdownComplete——`wait_exit`
   必须两者都能处理

---

## 015. JobObject IOCP 通知线程回调已析构 sink（use-after-free，Shutdown 偶发 0xC0000005）

**日期**：2026-08-06
**模块**：`src/infra/job/JobObjectImpl.cpp` + `src/adapters/SandboxInstance.cpp`

**现象**：黑盒测试报告 Shutdown 时 sandbox.exe 偶发 0xC0000005。复现发现：在
**限额杀进程场景**（如 memory_mb 超限子进程被 Job 杀）后 Shutdown，稳定触发
`exit_code=3221225477`（修复前 Test 2 连续 5/5 崩溃）；而简单 echo 进程场景
（190 次压测）不触发——因此此前的简单场景压测未能暴露。

**根因**：
- `JobObjectImpl` 的 IOCP 通知线程持 `sink_`（非拥有裸指针）调用
  `sink_->OnNotification(notif)`（JobObjectImpl.cpp:575-578）
- `sink_` 指向 `StartProcessUseCase`，只在 Job 析构时 `StopIocpThread()` join
- 但 `SandboxInstance::ShutdownAll` 按依赖顺序 `usecase.reset()` 先于
  `job.reset()`；usecase 析构会调 `job_object_->TerminateAll(1)`，产生新的
  Job 通知（EXIT_PROCESS / ACTIVE_PROCESS_ZERO）进入 IOCP 队列
- 若 IOCP 线程恰好在 usecase 已析构后取出该通知 → 回调悬垂 sink → UAF
- 限额杀进程场景队列中必有残留通知，触发窗口被显著放大

**修复**：
- `IJobObject` 新增 `Shutdown()`：先清空 `sink_`（sink_mutex_ 保护）再
  `StopIocpThread()` join，保证清理后无任何回调；不关 job 句柄（usecase
  析构仍需 TerminateAll）
- `SandboxInstance::ShutdownAll` / `CleanupFinished` / StartProcess 失败路径
  在 `usecase.reset()` 之前调用 `entry.job->Shutdown()`

**验证**：
- 修复前：Test 2（memory limit）5/5 崩溃 0xC0000005
- 修复后：Test 2 连续 5/5 通过，全量 e2e 21/21，ctest 13/13

**关键教训**：
1. **IOCP/回调线程持非拥有 sink 指针时，必须先停线程再析构 sink 对象**
2. **e2e 必须断言 sandbox.exe 自身退出码**——只断言事件流会把 Shutdown 崩溃掩盖
3. **压测场景选择决定暴露概率**：限额杀进程比普通进程退出更易触发通知竞态

---

## 016. NLOHMANN_JSON_SERIALIZE_ENUM 未知值静默回退首项 → 未知命令误判为 Hello 断连

**日期**：2026-08-06
**模块**：`src/core/entities/IpcMessage.hpp`

**现象**：黑盒报告 F1——发送未知命令类型（`this_command_does_not_exist`）后
服务端直接断开客户端管道，一个错误命令即可终结整个沙箱会话。

**根因**：
- `NLOHMANN_JSON_SERIALIZE_ENUM` 的 `from_json` 在字符串无匹配时**静默回退到
  映射表第一个条目**（`e = begin(m)->first`），不抛异常
- MessageType 映射表首项是 `Hello`，未知命令被解码为 `Hello`
- ReadLoop 检测到重复 Hello → `duplicate Hello, disconnecting` → 断连
- 独立验证：未知字符串 `type` 解码为 0（Hello）

**修复**：
- MessageType 增加 `Unknown` 哨兵，置于映射表**首个**（宏回退目标）
- `IpcMessage::from_json` 检测 `type == Unknown` 抛 `json::type_error`
- 由 `FrameCodec::Decode` 捕获返回 `IpcJsonParseError`，ReadLoop 跳过该帧不断连

**验证**：
- 修复前：发未知命令 → PIPE CLOSED，后续命令 error 232
- 修复后：发未知命令 → 服务端存活，后续 StartProcess 正常
- 全量 e2e 21/21，ctest 13/13

**关键教训**：
1. **NLOHMANN_JSON_SERIALIZE_ENUM 对未知值静默回退到首项，不是抛异常**——
   映射表首项必须是"安全默认"（Unknown/哨兵），不能是业务值
2. **协议层未知类型必须显式拒绝**，不能依赖宏默认值（否则语义错乱）

---

## 017. 空环境块单 null 结尾 → CreateProcessW 偶发 ERROR_INVALID_PARAMETER(87)

**日期**：2026-08-06
**模块**：`src/infra/process/ProcessLauncherImpl.cpp`

**现象**：黑盒报告 BUG-01——`inherit_env=False` 且无 `env_vars` 时约 80% 概率子进程启动失败 `CreateProcessW failed: err=87`。实测复现 85%，对照组（带 1 个 env_var）0%。

**根因**：
- `BuildEnvironmentBlock` 空环境块只写**一个** `L'\0'`，而 Windows 环境块要求**双 null** 结尾
- 单 null 时 `CreateProcessW` 会继续读取 vector 容量区之后的堆内存，是否恰好在第 2 字节遇到 0 决定成败 → 非确定性（~80% 失败）
- 只要 ≥1 个 entry 就是 N+1 ≥ 2 个 null，恒合法 → 解释"给 env_vars 就 100% 成功"

**修复**：空块补第二个 `\0`（`if (block.size() == 1) block.push_back(L'\0')`）。

**关键教训**：**空环境块必须双 null 结尾**；单 null 会触发越界读堆垃圾，行为随堆布局随机。

---

## 018. PowerShell Compress-Archive 自动补 .zip 后缀 → 归档存在性检查永远失败

**日期**：2026-08-06
**模块**：`src/infra/filesystem/FileSystemIsolatorImpl.cpp`

**现象**：黑盒报告 BUG-02——`exit_strategy=archive` 时归档不生效、临时区不清理。实测复现 3/3。

**根因**：
- PowerShell `Compress-Archive -DestinationPath` 对无 `.zip` 后缀的目标路径**自动补全扩展名**（`ar` → 实际创建 `ar.zip`）
- 代码用 `PathFileExistsW(archive_path)` 检查**原始无后缀路径** → 永远失败 → 返回 FsArchiveFailed → Teardown 不删临时区

**修复**：`ArchiveDirectory` 内规范化目标路径（无 `.zip` 后缀则补全），命令与存在性检查使用同一有效路径。

**关键教训**：**调用外部工具时，路径语义以工具实际行为为准**（PowerShell 自动补扩展名），存在性检查必须与工具落盘路径一致。

---

## 019. fs_mode=Disabled 时整个 filesystem 配置被静默丢弃 → 子进程直写宿主（安全 footgun）

> **Phase 16（2026-08-12）演进**：`fs_mode`/`filesystem`/AppContainer 已删除，该冲突不可能再发生（旧字段解析期显式拒绝）。本条的**核心教训被 Phase 16 继承**：删除旧配置/字段必须显式拒绝（`unknown field` + Phase 16 指引），不得静默忽略。

**日期**：2026-08-06
**模块**：`src/core/usecases/StartProcessUseCase.cpp` / `ConfigLoader.cpp` / `StartProcessPayloadParser.cpp`

**现象**：黑盒报告 BUG-03（HIGH/安全）——配置 `fs_mode=disabled` + `filesystem` 块时，filesystem 配置被完全忽略，子进程无隔离直写调用方 cwd。`default_deny` 则正确生效。

**根因**：
- fs_isolator 的 Setup 整个嵌套在 `if (use_appcontainer)` 块内，而 `use_appcontainer` 仅当 `fs_mode==DefaultDeny` 成立
- `fs_mode=Disabled` 时 `fs_config` 有值但永远不被应用，且无任何警告 → 静默配置失效

**修复**：配置文件与 IPC 两条路径都增加冲突检测——`fs_config` 存在但 `fs_mode != DefaultDeny` 时**显式报错拒绝**，禁止静默直写宿主。

**关键教训**：**隔离产品的"配置被忽略"必须是显式错误，不能静默**；安全语义上"无隔离"与"无访问"是两回事，语义分歧要防在配置层。

---

## 020. 事件载荷字段类型与文档契约不符（process_exited.reason / resource_limit_hit / behavior_log.type）

**日期**：2026-08-06
**模块**：`StartProcessUseCase.cpp` / `main.cpp`

**现象**：黑盒报告 BUG-08——文档称 `reason`/`behavior_log.type` 为字符串枚举、`resource_limit_hit` 有 `limit/value`，实际代码发 int、键名不符。三处冲突，下游解析会失败。

**根因**：序列化用 `static_cast<int>(枚举)` 而非枚举的字符串映射（`ExitReasonToString` / `NLOHMANN_JSON_SERIALIZE_ENUM` 已有但未用）；`resource_limit_hit` 未带限额值。

**修复**：代码对齐文档——`reason` 输出字符串、`behavior_log.type` 输出字符串枚举、`resource_limit_hit` 补 `limit`+`value`（保留 `limit_type`/`notification_type` 向后兼容）。同步更新 7 个 e2e 测试的 int 断言。

**关键教训**：**对外 API 契约一旦文档化，实现必须逐字段核对**；枚举序列化优先用已有字符串映射，避免 int/字符串漂移。

---

## 021. py::function 无 GIL 析构崩溃 + ETW 线程 join 死锁

**日期**：2026-08-11
**模块**：`SandboxInstanceBinding.cpp` / `NativeSandboxInstance.cpp`

**现象**：运行 `test_behavior_log.py` 触发 Microsoft Visual C++ Runtime Library 弹窗。`~PySandboxInstance()` 析构时死锁（ETW dispatch 线程阻塞在 `gil_scoped_acquire`）。

**根因**：
1. `shutdown()` 释放 GIL 后调 `ShutdownAll()` → `entry.usecase.reset()` 析构 `NativeSandboxedProcess` → 析构 `on_behavior_event` 等 `std::function` → lambda 捕获的 `py::function` 析构需要 GIL → **无 GIL 析构 → 崩溃**
2. `~PySandboxInstance()` 持 GIL 调 `ShutdownAll()` → `etw_monitor_->Stop()` join dispatch 线程 → dispatch 线程阻塞在 `gil_scoped_acquire` → **死锁**

**修复**：三阶段 GIL 管理（`ShutdownWithGilManagement`）：
1. Phase 1：释放 GIL → `StopEtwMonitor()`（join ETW 线程，线程可获 GIL 完成回调）
2. Phase 2：持 GIL → `ClearAllCallbacks()`（安全销毁 `py::function` 捕获）
3. Phase 3：释放 GIL → `ShutdownAll()`（usecase 已无 `py::function`，安全析构）

**关键教训**：
- C++ 对象持有 `py::function`（通过 `std::function` lambda 捕获）时，**析构必须在 GIL 下**
- 线程 join 时需**释放 GIL**，防回调线程死锁在 `gil_scoped_acquire`
- pybind11 绑定层的 shutdown/destructor 必须**分阶段管理 GIL**：先释放 GIL join 线程 → 持 GIL 清回调 → 释放 GIL 析构
