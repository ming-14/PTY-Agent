# PTY-Agent Win-Sandbox 黑盒安全测试报告

**测试日期**: 2026-08-30  
**沙箱类型**: win_sandbox_native（pybind11 in-process / WRITE_RESTRICTED 令牌 + Job Object）  
**测试方法**: 黑盒（通过公开 API 启动沙箱内进程，从外部独立验证结果）  
**测试套件**: 23 项主用例 + 7 项补充用例（配额 + 进程访问控制）  

---

## 测试结果总览

| 类别 | 通过 | 失败 | 说明 |
|------|------|------|------|
| 主测试套件（文件/注册表/网络/Job） | 23/23 | 0 | 全部通过 |
| 补充测试（配额/进程访问） | 6/7 | 1 | 1 个安全发现（见下） |
| 集成测试（现有） | 9/9 | 0 | 已修复导入 + 断言后全通 |

---

## 安全发现

### 1. [HIGH] 沙箱导入路径断裂 —— 沙箱默认不可用（已修复）

**严重性**: 高（完全阻断，已修复）  
**描述**: `src/sandbox/manager.py` 直接 `import win_sandbox_native`，期望 pyd 在 `bin/` 根目录。但 `BUILD.py` 现把编译产物放在 `bin/win_sandbox/_native/` 下（并主动删除根目录旧 pyd）。导入失败 → `_HAS_NATIVE=False` → 整个沙箱不可用（`SandboxError: win_sandbox_native 不可用`）。  
**影响**: 配置 `sandbox.enabled=true` 时沙箱无法启动（但默认 `enabled=false`，所以不是直接的安全绕过）。  
**修复**: 改为 `from win_sandbox import SandboxInstance`（受益于 `win_sandbox/__init__.py` 正确处理路径）。  
**测试**: 修复后 9/9 集成测试通过。

### 2. [INFO→已加固] 沙箱内进程可获宿主 PROCESS_TERMINATE 句柄（已修复）

**严重性**: 信息性 → 已实施宿主进程 DACL 加固  
**描述**: 沙箱受限令牌（WRITE_RESTRICTED）下，沙箱内进程可以用 `OpenProcess(PROCESS_TERMINATE)` 打开宿主进程（启动沙箱的 Python 进程）并获得终止句柄。实测使用 `TerminateProcess` 后宿主进程立即死亡（0.3s 内无痕退出，exit code 1，无 traceback）。  
**根因**: 受限令牌的 restricting SIDs = `[logon SID, Everyone, workspace SID, temp SID]`。宿主进程 DACL 授予了 logon SID `PROCESS_TERMINATE` 权限（ACE mask 0x00121411）→ WRITE_RESTRICTED 检查通过。  
**参考实现一致**: 参考实现 README（`docs/subsystems/sandbox.md`、`README.md line 114`）明确声明：**"Writes are restricted; reads, network, and process visibility are not"** — 进程可见性/进程操作**不受限**是经过验证的设计边界，不是 bug。参考实现本身未做宿主 DACL 加固（其 runner 为短命进程，风险有限）。  
**修复（已实施）**: `acl.cpp` 新增 `hardenHostProcessDacl(logonSid, worldSid)`，在 `SandboxInstance::startProcess` 的 `createRestrictedToken` 之后调用（fail-closed，失败即抛错）。给宿主进程 DACL 添加 Deny ACE（logon SID + Everyone 拒绝 `kDenyProcessMask`：PROCESS_TERMINATE / VM 写 / 线程创建 / 句柄复制 / 挂起恢复 / DELETE / WRITE_DAC / WRITE_OWNER 等），Deny 优先于 Allow。保留读类权限（查询/VM 读/同步），进程可见性边界不变。幂等：已存在的 Deny ACE 跳过重复应用。  
**验证**: 加固前 `terminate_external = CAN open host`（FAIL）；加固后 `terminate_external = denied open (err=5)`（PASS）。补充套件 7/7 全绿，主套件 23/23 全绿，单元+集成 66/66 全绿。  
**副作用检查**: 宿主自身不受影响（`GetCurrentProcess()` 伪句柄不经 DACL 检查；自身 SID 的 Allow ACE 仍在）；daemonctl 停止走 TCP 命令而非 TerminateProcess。

### 3. [LOW] 完整性级别文档注释过时（Medium IL 非 Low IL，已修复）

**严重性**: 低（文档对齐，已修复）  
**描述**: `config/daemon/sandbox.toml` 注释写明 "Low IL token"，但代码实际使用 `LUA_TOKEN`（`DISABLE_MAX_PRIVILEGE | LUA_TOKEN`），不降低完整性级别。  
**验证**: 沙箱内探针读取完整性级别为 **S-1-16-8192（Medium Integrity）**，而非 Low IL 的 S-1-16-4096。  
**参考实现一致**: 参考实现 `token.ts` 第 212 行同样使用 `DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED`，完整性级别也为 Medium。Low IL 不是设计目标——WRITE_RESTRICTED 提供正交的写保护，而 Low IL 会破坏工作区写模型（Low IL 无法写入 Medium IL 目录）。  
**修复**: 已统一 `sandbox.toml`、`tracker.py`、测试文件中的过时注释，改为准确描述（Medium IL + WRITE_RESTRICTED + 进程可见性边界）。

### 4. [LOW] 沙箱内进程枚举受限（信息性）

**描述**: `tasklist.exe` 在沙箱内返回 exit code 1（枚举失败）。受限令牌阻止了进程枚举，增强了进程可见性隔离。  
**影响**: 正面——沙箱内进程无法查看其他进程列表。

---

## 通过的测试项

### 文件系统写隔离（全部通过，主机侧独立验证）

| 用例 | 沙箱内结果 | 主机侧验证 |
|------|------------|------------|
| 写工作区 | 成功 | 文件存在 |
| 写私有 temp（TMP/TEMP 重定向） | 成功 | 文件存在 |
| 写主机 temp（真实路径） | Permission denied | 文件不存在 |
| 写用户目录（~） | Permission denied | 文件不存在 |
| 写 ProgramData | Permission denied | 文件不存在 |
| 写系统 temp（C:\Windows\Temp） | Permission denied | 文件不存在 |
| 删除外部文件 | Access denied | 文件不存在 |
| 写 HKCU 注册表 | Access denied | 键不存在 |
| 写 HKLM 注册表 | Access denied | 键不存在 |

### 文件系统读访问（全部通过）

| 用例 | 结果 |
|------|------|
| 读主机 temp 文件 | 成功 |
| 读用户目录文件 | 成功 |
| 读系统文件（win.ini） | 成功 |

### 进程/Job 隔离（全部通过）

| 用例 | 结果 |
|------|------|
| 沙箱内启动子进程 | 成功（cmd /c echo child-ok） |
| Job 进程列表包含子进程 | 通过（child pid 在 Job 列表内） |
| Job 逃逸（CREATE_BREAKAWAY_FROM_JOB） | 拒绝（WinError 5） |
| 打开宿主进程（PROCESS_ALL_ACCESS） | 拒绝（err=5） |
| 打开宿主进程（PROCESS_QUERY_LIMITED_INFO） | 成功（读权限允许） |
| 打开 System 进程（pid 4） | 拒绝（err=5，普通用户不可） |
| 进程枚举（tasklist） | 受限（rc=1） |

### 资源配额（全部通过）

| 用例 | 配置 | 结果 |
|------|------|------|
| max_processes=2 | 8 个子进程 | Job 拒绝 6 个（stderr: "Not enough quota" × 6），存活 2 个 |
| memory_mb=256 | 分配 600MB | 进程被 Job 杀死（alloc_started=True, exit=1） |

### 网络（符合配置）

| 用例 | 结果 |
|------|------|
| TCP 127.0.0.1 回环 | 连接成功（unrestricted 配置） |

---

## 代码修复记录

1. **`src/sandbox/manager.py`** (line 37-42): 导入从 `import win_sandbox_native` 改为 `from win_sandbox import SandboxInstance`，对齐 BUILD.py 构建布局。
2. **`src/sandbox/manager.py`** (line 93): `win_sandbox_native.SandboxInstance()` 改为 `SandboxInstance()`。
3. **`tests/integration/test_sandbox.py`** (test_start_and_ready): 移除不存在的 `process_count` 断言，改用 `start_process` + `get_process_list()` 验证。

---

## 结论

沙箱核心机制（WRITE_RESTRICTED 写保护）有效：
- 所有文件系统写隔离测试通过
- 注册表写隔离通过
- 资源配额（内存、进程数）通过
- 网络 unrestricted 符合配置
- 私有 temp 隔离（TMP/TEMP 重定向）通过

**修复完成**：
1. ✅ **沙箱导入路径断裂**（HIGH）—— 已修复，manager.py 改为 `from win_sandbox import SandboxInstance`
2. ✅ **宿主进程 PROCESS_TERMINATE 泄露**（HIGH）—— 已加固，`hardenHostProcessDacl()` 在宿主 DACL 添加 Deny ACE（logon SID + Everyone 拒绝进程写权限），重建 pyd 后验证通过
3. ✅ **完整性级别 Medium 文档注释过时**（LOW）—— 已统一所有 "Low IL" 注释为准确描述

**强化提升**：
- 参考实现（deepseek-harness）明确把"进程可见性不受限"列为设计边界，且未做宿主 DACL 加固。PTY-Agent 的 in-process 常驻 daemon 形态风险更大，本修复在参考实现基础上增加了**宿主进程 DACL 加固**作为防御增强，Deny 优先于 Allow，不影响宿主自身功能。