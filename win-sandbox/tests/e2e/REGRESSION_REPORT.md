# 回归测试报告 — win-sandbox e2e

- 运行命令：`python tests/e2e/run_all_regression.py`
- 运行环境：Windows 10（Git Bash 宿主，PATH 含 `C:\Program Files\Git\usr\bin`）
- 结果：**19 / 21 PASS（exit=1）**

> 说明：本仓库 HEAD 提交为 `feat: win-sandbox 并入 PTY-Agent 并修复全量测试`。
> 当前工作树存在大量未提交改动，本次跑出的 2 个失败项尚未处理。

## 结果汇总

| 测试 | 结果 |
|------|------|
| test_behavior_log | PASS |
| test_cleanup | PASS |
| test_degraded_monitor | PASS |
| test_global_quota | PASS |
| test_helpers | PASS |
| test_hpcon_conpty | **FAIL** |
| test_job_enhancement | PASS |
| test_lowil_isolation | **FAIL** |
| test_multiprocess | PASS |
| test_native_etw | PASS |
| test_native_smoke | PASS |
| test_network_allowlist | PASS |
| test_oj_scenario | PASS |
| test_permission_matrix | PASS |
| test_process_tree | PASS |
| test_resource_quota | PASS |
| test_scenario_c_sample | PASS |
| test_scenario_d_ci | PASS |
| test_signal | PASS |
| test_silo | PASS |
| test_write_stdin | PASS |

## 失败项分析

### 1. test_lowil_isolation.py —— 环境问题（非产品缺陷）

- 失败点：仅子用例 1 `whoami /groups`（进程完整性级别 = Low）失败。
- 报错：`whoami /groups failed: exit=-1073741502 ... NtCreateDirectoryObject(\BaseNamedObjects\msys-2.0...): 0xC0000022`
- 根因：本工具运行在 Git Bash 下，PATH 中 `C:\Program Files\Git\usr\bin\whoami.exe`（msys 版）排在 `C:\Windows\System32\whoami.exe` 之前。沙箱子进程继承了该 PATH，`cmd /c whoami /groups` 实际执行的是 msys 版 whoami；msys 运行时会尝试创建 `BaseNamedObjects` 命名对象，而 Low IL 下该操作返回 `STATUS_ACCESS_DENIED (0xC0000022)`，故进程直接崩溃。
- 其余 5 个子用例全部 PASS（%TEMP% 重定向、桌面写被拒、父 %TEMP% 私有文件读被拒、System32 可读、会话目录清理），证明 **Low IL 隔离语义本身正确**，仅完整性级别“探针命令”在 Git Bash 环境下失效。
- 修复建议（测试侧，低风险）：探针改用绝对路径 `C:\Windows\System32\whoami.exe`，或在沙箱启动前清理 PATH 中的 msys/cygwin 目录。

### 2. test_hpcon_conpty.py —— 稳定复现（含两个独立问题）

- 失败点：第 8 步 Ctrl+C 中断验证。
- 报错：`FAIL: ctrl+c not effective: interrupt=False stop=True ttl=False`（ping 持续输出 `PING：传输失败。常见故障。`）
- 问题 A（`ttl=False`）：`ping -n 60 127.0.0.1` 打印“传输失败”，说明沙箱网络隔离阻断了环回 ICMP，测试预设（“ping 返回 TTL= 行”）在当前网络策略下不成立。（注：宿主侧环回 ping 正常返回 `TTL=128`，故非宿主问题。）
- 问题 B（`interrupt=False`）：Ctrl+C（`\x03`）未终止 ping，输出中也未见 `^C` / `Control-C` 文本。
  - 已核对启动代码 `src/infra/process/ProcessLauncherImpl.cpp:380-384`：ConPTY 路径**正确未设置** `CREATE_NEW_PROCESS_GROUP`（该标志正是导致 `\x03` 失效的已知原因），启动标志无误。
  - 因此该问题可能是真实 Ctrl+C 回归，也可能是中文语言环境 / 网络被阻断组合下的测试检测缺陷，需进一步排查（建议：改用不依赖环回网络、且Ctrl+C 行为干净的长运行命令如 `cmd /c "for /l %i in () do @(echo tick & timeout /t 1 >nul)"`，并辅以“中断后无新输出”旁证）。

## 待定

- 这 2 个失败项是“环境问题/已知 flaky”还是需要进一步修复（产品侧或测试侧），需确认处理策略后再动手。
- 当前工作树改动较大，动手前建议先明确范围，避免与未提交改动相互干扰。
