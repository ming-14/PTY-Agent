import os
import platform
import queue
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AICHAT_EXE = os.path.join(SCRIPT_DIR, "bin", "aichat.exe")

DEFAULT_CONFIG = os.path.normpath(
    os.path.join(SCRIPT_DIR, "config", "config.yaml")
)

REPO = "sigoden/aichat"
BASE_URL = f"https://github.com/{REPO}/releases"


def detect_target() -> tuple[str, str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        os_part = "pc-windows-msvc"
        ext = ".zip"
        binary = "aichat.exe"
    elif system == "darwin":
        os_part = "apple-darwin"
        ext = ".tar.gz"
        binary = "aichat"
    elif system == "linux":
        os_part = "unknown-linux-musl"
        ext = ".tar.gz"
        binary = "aichat"
    else:
        sys.exit(f"Unsupported OS: {system}")

    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "i386": "i686",
        "i686": "i686",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "armv7l": "armv7",
        "armv6l": "arm",
        "arm": "arm",
    }
    arch = arch_map.get(machine)
    if arch is None:
        sys.exit(f"Unsupported architecture: {machine}")

    return f"{arch}-{os_part}", ext, binary

def get_latest_version() -> str:
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req) as resp:
        import json
        data = json.loads(resp.read())
        return data["tag_name"]

def download_and_extract(target: str, ext: str, version: str) -> None:
    filename = f"aichat-{version}-{target}{ext}"
    url = f"{BASE_URL}/download/{version}/{filename}"

    sys.stderr.write(f"Downloading {filename} ...\n")

    with tempfile.TemporaryFile(suffix=ext) as tmp:
        req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req) as resp:
                while chunk := resp.read(8192):
                    tmp.write(chunk)
        except KeyboardInterrupt:
            sys.stderr.write("\nDownload interrupted\n")
            raise
        tmp.seek(0)

        extracted_paths = []
        try:
            if ext == ".zip":
                with zipfile.ZipFile(tmp) as zf:
                    for member in zf.infolist():
                        if member.filename.endswith("/aichat.exe") or member.filename == "aichat.exe":
                            zf.extract(member, SCRIPT_DIR)
                            extracted_paths.append(os.path.join(SCRIPT_DIR, member.filename))
                            extracted = os.path.join(SCRIPT_DIR, member.filename)
                            if extracted != AICHAT_EXE:
                                os.rename(extracted, AICHAT_EXE)
                            break
                    else:
                        sys.exit("aichat.exe not found in zip archive")
            else:
                with tarfile.open(fileobj=tmp, mode="r:gz") as tf:
                    for member in tf.getmembers():
                        if member.name.endswith("/aichat") or member.name == "aichat":
                            tf.extract(member, SCRIPT_DIR, filter="data")
                            extracted_paths.append(os.path.join(SCRIPT_DIR, member.name))
                            extracted = os.path.join(SCRIPT_DIR, member.name)
                            if extracted != AICHAT_EXE:
                                os.rename(extracted, AICHAT_EXE)
                            break
                    else:
                        sys.exit("aichat binary not found in archive")
        except KeyboardInterrupt:
            for p in extracted_paths:
                if os.path.exists(p):
                    os.remove(p)
            sys.stderr.write("\nExtraction interrupted\n")
            raise

    os.chmod(AICHAT_EXE, 0o755)
    sys.stderr.write(f"Downloaded to {AICHAT_EXE}\n")

def ensure_aichat() -> None:
    if os.path.exists(AICHAT_EXE):
        return
    target, ext, _ = detect_target()
    version = get_latest_version()
    download_and_extract(target, ext, version)

def _strip_control(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "")
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

def _ensure_config(config_path: str) -> None:
    example_path = config_path + ".example"
    if not os.path.exists(config_path) and os.path.exists(example_path):
        import shutil
        shutil.copy2(example_path, config_path)

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
