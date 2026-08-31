#!/usr/bin/env bash
# s6 用户级服务部署（无需 root）
# ==============================
# 用法：bash <(curl -sL https://...) 或手动执行
#
# 原理：s6 支持用户级服务目录，s6-svscan 以普通用户身份
# 扫描 ~/.s6/services/ 下的服务定义。daemon 前台运行
# （--foreground），s6 直接管理它（保活、重启、日志）。
#
# 使用 `python -m src.daemon --foreground` 而不走 CLI 双 fork，
# 因为双 fork 会使进程脱离监督器，在容器重启/stage 清理时
# 被 s6 杀掉。

set -e

# 1. 创建服务目录
S6_DIR="${HOME}/.s6/services/pty-agent"
mkdir -p "$S6_DIR"

# 2. 写 run 脚本
cat > "$S6_DIR/run" << 'RUNSCRIPT'
#!/usr/bin/env bash
exec 2>&1  # stderr 合并到 stdout，s6-log 统一捕获
cd /path/to/pty-agent  # 改成实际路径
exec python3 -m src.daemon --foreground
RUNSCRIPT
chmod +x "$S6_DIR/run"

# 3. 可选：写 finish 脚本（服务退出后清理）
cat > "$S6_DIR/finish" << 'FINISHSCRIPT'
#!/usr/bin/env bash
# 退出码 125 告诉 s6 不要自动重启（仅无监督器场景）
# 正常退出（0）或信号退出（>=256）时让 s6 按 policy 处理
# 不写 finish 时 s6 默认会重启 longrun 服务
rm -f "${HOME}/.pty-agent/daemon.lock"
exit 0
FINISHSCRIPT
chmod +x "$S6_DIR/finish"

# 4. 启动用户级 s6-svscan（放入 .bashrc 或容器 entrypoint）
# 如果已有系统级 s6-svscan（如容器 PID 1 是 s6），
# 此步跳过，只需把服务目录链接到扫描目录：
#   ln -s "$S6_DIR" /run/service/pty-agent   # 需要 root
# 或使用用户级扫描器：
#   s6-svscan ~/.s6/services &
# 验证：
#   s6-svstat ~/.s6/services/pty-agent

echo "s6 服务目录已创建: $S6_DIR"
echo "请修改 run 脚本中的 /path/to/pty-agent 为实际路径后运行："
echo "  s6-svscan ~/.s6/services &"
echo "或通过容器 s6-svscan 的链接目录注册（需 root）："
echo "  ln -s $S6_DIR /run/service/pty-agent"