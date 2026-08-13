---
name: windows-debugging
description: Use the local Windows Debugging Tools (cdb / kd / adplus / umdh / dumpchk) bundled under windows-debugging\10.0.19041.5609 to analyze crash dumps (BSOD / .dmp), attach debuggers, capture process crashes with ADPlus, and hunt memory leaks with UMDH. Trigger when the user mentions WinDbg, analyzing a dump, blue screen, BugCheck, symbol path, ADPlus, or memory leak on Windows.
---

# Windows Debugging Tools (本地免安装版)

本技能封装位于 `windows-debugging\10.0.19041.5609\` 的 Windows 调试工具集（Debugging Tools for Windows，版本 10.0.19041.5609）。所有 `.exe` 均可直接运行，**无需安装、无需管理员**（内核调试除外）。

如果`windows-debugging\10.0.19041.5609\`什么都没有，请通知用户下载

## 关键路径

- 工具根目录（下文用 `$TOOLS` 指代）：
  `windows-debugging\10.0.19041.5609\`
- 命令行用户态调试器：`$TOOLS\cdb.exe`
- 内核调试器：`$TOOLS\kd.exe` / `$TOOLS\ntkd.exe`
- 崩溃抓取：`$TOOLS\adplus.exe`
- 堆/内存泄漏：`$TOOLS\umdh.exe`
- dump 校验：`$TOOLS\dumpchk.exe` / `$TOOLS\dumpexam.exe`
- 符号校验：`$TOOLS\symchk.exe`
- 进程列表/终止：`$TOOLS\tlist.exe` / `$TOOLS\list.exe` / `$TOOLS\kill.exe`

## 符号服务器（最重要）

分析 dump 前必须配符号，否则堆栈全是 `???`。微软公共符号服务器：

```
srv*C:\Symbols*https://msdl.microsoft.com/download/symbols
```

- 在 WinDbg 图形界面里：`File > Symbol File Path` 填入上面字符串；或命令窗口执行 `.sympath srv*C:\Symbols*https://msdl.microsoft.com/download/symbols` 然后 `.reload`。
- 用 `cdb`/命令行时通过 `-y` 参数传入：`-y "srv*C:\Symbols*https://msdl.microsoft.com/download/symbols"`。
- 公司私有符号服务器把上面的 URL 换成内网 `srv*` 地址即可。

## 场景 1：分析蓝屏 / 崩溃 dump（`.dmp`）

最常用、最自动化的流程：

1. 用 WinDbg 打开 dump：
   ```
   & "$TOOLS\windbg.exe" -z "C:\path\to\memory.dmp"
   ```
2. 打开后自动执行自动分析命令：
   ```
   !analyze -v
   ```
   重点看输出里的 `BUGCHECK_CODE`、`BUGCHECK_STRING`、大概率 culprit（`Probably caused by`）、以及 `STACK_TEXT` 调用栈。
3. 若符号未加载，先设符号路径再 `.reload`，重跑 `!analyze -v`。
4. 常用辅助命令：
   - `k` / `kP` — 当前线程调用栈（含参数）
   - `lm` — 列出已加载模块及符号状态
   - `!thread` / `!process` — 线程/进程上下文（内核 dump）
   - `!irp` — 查 IRP（驱动相关 BSOD 常用）
   - `dt nt!_EPROCESS` — 查看结构体

**非交互（命令行）一次性分析**：用 `cdb` 把命令串通过 `-c` 传入，适合脚本批量：
```
& "$TOOLS\cdb.exe" -y "srv*C:\Symbols*https://msdl.microsoft.com/download/symbols" -z "C:\path\to\memory.dmp" -c "!analyze -v; q"
```
（末尾 `q` 表示分析完退出。）

## 场景 2：用 ADPlus 抓取进程崩溃 / 挂起

当目标程序**还没崩溃**、需要它在崩溃瞬间自动生成 dump 时：

- 崩溃（Crash）模式：
  ```
  & "$TOOLS\adplus.exe" -crash -pn <进程名.exe> -o "C:\dumps"
  ```
  或按 PID：`-p <PID>`。ADPlus 会挂一个调试器在进程上，进程一崩就写 mini/full dump 到 `-o` 目录。
- 挂起（Hang）模式（进程卡死无响应）：
  ```
  & "$TOOLS\adplus.exe" -hang -pn <进程名.exe> -o "C:\dumps"
  ```
- 常见附加参数：`-fullonfirst` 第一次异常就抓 full dump；`-quiet` 静默（适合脚本）。
- 注意：ADPlus 运行期间会拖慢目标进程；抓完用 `kill`/结束调试器即可释放。

## 场景 3：用 UMDH 查用户态内存泄漏

UMDH 比较两次堆快照的差值，定位泄漏点（需 PDB 符号）：

1. 用 `gflags.exe` 为目标进程开启用户态栈回溯（Umdh 依赖）：
   ```
   & "$TOOLS\gflags.exe" -i <进程名.exe> +ust
   ```
2. 启动目标进程，抓第一份快照：
   ```
   & "$TOOLS\umdh.exe" -p:<PID> -f:"C:\dumps\snap1.txt"
   ```
3. 让程序跑一会儿（制造泄漏），抓第二份：
   ```
   & "$TOOLS\umdh.exe" -p:<PID> -f:"C:\dumps\snap2.txt"
   ```
4. 比较两次快照（需符号路径）：
   ```
   & "$TOOLS\umdh.exe" -d:"C:\dumps\snap1.txt" -d:"C:\dumps\snap2.txt" -f:"C:\dumps\diff.txt" -y:"srv*C:\Symbols*https://msdl.microsoft.com/download/symbols"
   ```
   `diff.txt` 里按分配字节数排序，最上面的调用栈就是泄漏来源。
5. 排查完用 `gflags.exe -i <进程名.exe> -ust` 关闭。

## 场景 4：校验 dump / 查符号

- 校验 dump 文件是否完整：
  ```
  & "$TOOLS\dumpchk.exe" "C:\path\to\memory.dmp"
  ```
- 校验某模块符号是否匹配：
  ```
  & "$TOOLS\symchk.exe" /v "C:\path\to\module.dll" /s "srv*C:\Symbols*https://msdl.microsoft.com/download/symbols"
  ```

## 场景 5：进程/USB 辅助排查

- 列出进程：`& "$TOOLS\tlist.exe"` 或 `& "$TOOLS\list.exe"`
- 终止进程：`& "$TOOLS\kill.exe" <PID>`
- 查看 USB 设备树：`& "$TOOLS\usbview.exe"`
- API/操作记录与回放：`& "$TOOLS\logger.exe"` + `& "$TOOLS\logviewer.exe"`

## 注意事项

- 符号缓存目录（如 `C:\Symbols`）首次会下载很多文件，体积较大，属正常现象。
- 内核调试（`kd.exe`）需要目标机开启调试模式并用网络/USB/1394/`kdnet` 连接，普通 dump 分析用不到，非必要时不要碰。
