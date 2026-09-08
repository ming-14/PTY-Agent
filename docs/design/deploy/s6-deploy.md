# s6 容器部署守护进程（免 root）

> PTY-Agent 守护进程被 s6（服务监督器）杀掉的根因：CLI 自动拉起时 Unix 双 fork
> 守护化，进程脱离监督器；s6 重启父服务/清理 stage 时信号扫到孤儿 daemon。
> 修复：daemon 支持**前台运行模式**，由 s6 以 longrun 方式直接管理（保活/重启/日志）。

## 前台模式

```bash
python -m src.daemon --foreground      # 参数
PTY_AGENT_FOREGROUND=1 python -m src.daemon   # 或环境变量
python app.py start --foreground       # 或经 CLI 入口（exec 链保持同一 PID）
```

- 不双 fork、不脱离终端 → s6 能正确持有并监控
- 日志同时输出到 stderr（s6-log 可捕获）+ 原文件日志
- SIGTERM 优雅退出（已有）

## 生存模式（可选）

如需 daemon 忽略所有结束进程的信号与 stop 消息（仅 SIGKILL 可终止）：

```bash
python app.py start --survive --foreground    # s6 容器：不可终止 + 前台
python -m src.daemon --survive                # 或直接入口
PTY_AGENT_SURVIVE=1 python -m src.daemon      # 或环境变量
```

- 忽略 SIGTERM/SIGHUP/SIGINT/SIGQUIT（含启动窗口期），daemon 不再因信号退出
- `stop` 协议消息被拒绝；`stop --force`（SIGKILL）仍可终止
- 注意：s6 监督下建议配合 `--foreground`，且此模式下 s6 无法用 SIGTERM 优雅停止 daemon

## 免 root 部署（用户级 s6）

s6 支持用户级服务目录，`/run/service` 只是系统级默认路径，不需要 root：

```bash
# 1. 建服务目录
mkdir -p ~/.s6/services/pty-agent

# 2. run 脚本（前台跑 daemon）
cat > ~/.s6/services/pty-agent/run <<'EOF'
#!/usr/bin/env bash
cd /path/to/pty-agent          # 改成实际路径
exec python3 -m src.daemon --foreground
EOF
chmod +x ~/.s6/services/pty-agent/run

# 3. 启动用户级扫描器（放 ~/.bashrc / 容器 entrypoint）
s6-svscan ~/.s6/services &

# 4. 验证
s6-svstat ~/.s6/services/pty-agent
```

容器里若 PID 1 已是 s6（s6-overlay）且有 root 写 /run/service：
```bash
ln -s ~/.s6/services/pty-agent /run/service/pty-agent
```
无需 root 时直接用上面的用户级 `s6-svscan` 即可。

## CLI 仍可正常连接

daemon 由 s6 前台管理后，CLI 的 `exec`/`send`/`read` 会通过单实例锁/端口发现
已有 daemon，直接连接，不会重复双 fork 启动（原有逻辑）。

## 日志

- stderr → s6-log 捕获（容器统一日志）
- 文件 → `~/.pty-agent/logs/*.log`（原有）

## 相关修复

- daemon 入口支持 `--foreground` / `PTY_AGENT_FOREGROUND`
- `start_daemon()` Unix 分支补就绪轮询（不再误报 failed）
- 就绪探测端口按 CONNECT_MODE 取实际端口（basic/tls 不再硬编码 token 端口）
