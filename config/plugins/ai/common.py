from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AICHAT_EXE = os.path.join(SCRIPT_DIR, "bin", "aichat.exe")

DEFAULT_CONFIG = os.path.normpath(
    os.path.join(SCRIPT_DIR, "config", "config.yaml")
)


def ensure_aichat() -> None:
    if not os.path.exists(AICHAT_EXE):
        raise RuntimeError(
            f"aichat.exe not found at {AICHAT_EXE}. "
            f"Run BUILD.ps1 to download it."
        )

def _strip_control(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "")
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

def _ensure_config(config_path: str) -> None:
    example_path = config_path + ".example"
    if not os.path.exists(config_path) and os.path.exists(example_path):
        import shutil
        shutil.copy2(example_path, config_path)


def _parse_yaml_value(content: str, key: str) -> str | None:
    """解析 yaml 顶层标量键（如 prompt）；缺失/空返回 None"""
    m = re.search(r'^' + re.escape(key) + r':[ \t]*(.*?)[ \t]*$', content, re.MULTILINE)
    if not m or not m.group(1):
        return None
    return m.group(1)


def _parse_yaml_int(content: str, key: str) -> int | None:
    """解析 yaml 顶层整数键（如 timeout）；缺失/非法返回 None"""
    m = re.search(r'^' + re.escape(key) + r':[ \t]*(\d+)[ \t]*$', content, re.MULTILINE)
    if not m:
        return None
    return int(m.group(1))


def load_settings(config_path: str | None = None) -> dict | None:
    """读取插件自身配置（prompt/timeout）

    单一来源为 config/ 目录：config.yaml 优先，键缺失时回退 .example
    （_ensure_config 自愈复制）；两者都缺失返回 None（调用方跳过分析）。
    """
    path = config_path or DEFAULT_CONFIG
    _ensure_config(path)
    settings: dict = {}
    for p in (path, path + ".example"):
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        # setdefault 不覆盖已存在的 key（即使值为 None），需要手动检查
        if "prompt" not in settings or settings.get("prompt") is None:
            settings["prompt"] = _parse_yaml_value(content, "prompt")
        if "timeout" not in settings or settings.get("timeout") is None:
            settings["timeout"] = _parse_yaml_int(content, "timeout")
    if "prompt" not in settings or "timeout" not in settings:
        return None
    return settings

def run_aichat(
    args: list[str],
    config: str | None = None,
    timeout: int = 40,
    no_think: bool = False,
) -> int:
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ensure_aichat()
    _ensure_config(DEFAULT_CONFIG)
    child_env = os.environ.copy()
    if config:
        child_env["AICHAT_CONFIG_FILE"] = config
    cmd = [AICHAT_EXE] + args
    buf = ""
    q: queue.Queue[bytes | None] = queue.Queue()

    def _reader(p: subprocess.Popen, qq: queue.Queue) -> None:
        for line in iter(lambda: p.stdout.readline(), b""):
            qq.put(line)
        qq.put(None)

    proc = subprocess.Popen(cmd, env=child_env, stdout=subprocess.PIPE)
    thread = threading.Thread(target=_reader, args=(proc, q), daemon=True)
    thread.start()

    try:
        while True:
            try:
                line = q.get(timeout=timeout)
            except queue.Empty:
                raise subprocess.TimeoutExpired(cmd, timeout)
            if line is None:
                break
            decoded = line.decode("utf-8", errors="replace")
            cleaned = _strip_control(decoded)
            if no_think:
                buf += cleaned
            else:
                sys.stdout.write(cleaned)
                sys.stdout.flush()
        proc.wait()
        if no_think:
            cleaned = strip_think(buf)
            if cleaned:
                sys.stdout.write(cleaned)
                sys.stdout.flush()
        return proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        sys.stderr.write(f"aichat timed out after {timeout} seconds\n")
        return 1
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        sys.stderr.write("\nInterrupted\n")
        return 130


def run_aichat_capture(
    args: list,
    config: str | None = None,
    timeout: int = 120,
) -> tuple:
    """调用 aichat 并捕获 stdout 文本（供程序内调用）

    与 run_aichat 的区别：不写 stdout，而是把清洗后的文本累积返回。
    自动剥离 ANSI 控制序列和  thinking 块。

    供 PTY-Agent 客户端的 --ai-analyse 功能调用（response/file 输出二次分析）。

    编码处理：Windows 下 aichat 可能输出系统 ANSI 编码（如 GBK），
    优先尝试 UTF-8 解码，失败时回退到系统编码。

    Args:
        args:    aichat 命令行参数（不含可执行文件路径）。
        config:  config.yaml 路径，None 用默认。
        timeout: 超时秒数。

    Returns:
        (returncode, output_text)。超时/异常时返回 (1, "")。
    """
    ensure_aichat()
    _ensure_config(DEFAULT_CONFIG)
    child_env = os.environ.copy()
    # 强制 aichat 输出 UTF-8（通过 LANG 环境变量）
    child_env["LANG"] = "en_US.UTF-8"
    child_env["PYTHONIOENCODING"] = "utf-8"
    if config:
        child_env["AICHAT_CONFIG_FILE"] = config
    cmd = [AICHAT_EXE] + args
    buf = ""
    q: queue.Queue = queue.Queue()

    def _reader(p: subprocess.Popen, qq: queue.Queue) -> None:
        for line in iter(lambda: p.stdout.readline(), b""):
            qq.put(line)
        qq.put(None)

    proc = subprocess.Popen(cmd, env=child_env, stdout=subprocess.PIPE)
    thread = threading.Thread(target=_reader, args=(proc, q), daemon=True)
    thread.start()

    try:
        while True:
            try:
                line = q.get(timeout=timeout)
            except queue.Empty:
                raise subprocess.TimeoutExpired(cmd, timeout)
            if line is None:
                break
            # 优先 UTF-8，失败时回退系统编码
            try:
                decoded = line.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    import locale
                    sys_enc = locale.getpreferredencoding(do_setlocale=False)
                    decoded = line.decode(sys_enc, errors="replace")
                except Exception:
                    decoded = line.decode("utf-8", errors="replace")
            buf += _strip_control(decoded)
        proc.wait()
        cleaned = strip_think(buf)
        return proc.returncode, cleaned
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        sys.stderr.write(f"aichat timed out after {timeout} seconds\n")
        return 1, ""


def strip_think(text: str) -> str:
    return re.sub(r'(?s)<think>.*?</think>\s*', '', text).strip()


def check_config(config_path: str) -> str | None:
    if not os.path.exists(config_path):
        _ensure_config(config_path)
    if not os.path.exists(config_path):
        return f"Config file not found: {config_path}"

    with open(config_path, encoding="utf-8") as f:
        config = f.read()

    fields = {
        "model": re.search(r'^model:[ \t]*(\S+)', config, re.MULTILINE),
        "type": re.search(r'^[ \t]*-[ \t]*type:[ \t]*(\S+)', config, re.MULTILINE),
        "name": re.search(r'^[ \t]+name:[ \t]*(\S+)', config, re.MULTILINE),
        "api_base": re.search(r'^[ \t]+api_base:[ \t]*(\S+)', config, re.MULTILINE),
        "api_key": re.search(r'^[ \t]+api_key:[ \t]*(\S+)', config, re.MULTILINE),
    }

    empty = [k for k, m in fields.items() if m is None]
    if empty:
        return f"Missing or empty config fields: {', '.join(empty)}"

    return None
