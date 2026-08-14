"""e2e 测试 —— keygen 子命令的端到端验证

覆盖场景（区别于单元测试，e2e 走真实 CLI 子进程链路）：
- 真实调用 ``python -m src keygen`` 子进程
- 验证生成的私钥/公钥文件存在且格式正确
- ssh-keygen 互操作：``ssh-keygen -lf`` 读项目生成的公钥指纹与项目输出一致
- ssh-keygen 互操作：``ssh-keygen -y -f`` 从项目私钥导出公钥，与项目公钥一致
- Windows 下默认 ~/.pty-agent/keys 路径（用 USERPROFILE 隔离 HOME 避免污染用户家目录）
- --force 覆盖已存在文件

ssh-keygen 工具不存在时，相关测试自动 skip（pytest.skip）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# 将项目根目录加入 sys.path，便于 import src.* 与以子进程方式运行 ``python -m src``
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.auth.keys import PrivateKey, load_authorized_keys


# ssh-keygen 可执行文件路径（不存在则为 None，相关测试 skip）
_SSH_KEYGEN = shutil.which("ssh-keygen")


def _parse_json_block(text: str) -> dict:
    """从可能混有非 JSON 文本中提取首个完整 JSON 对象

    _cmd_keygen 用 ``json.dumps(..., indent=2)`` 输出多行 JSON，
    故通过花括号配平定位 JSON 块而非按行解析。

    Args:
        text: 包含 JSON 块的文本

    Returns:
        解析后的 dict

    Raises:
        ValueError: 未找到 JSON 块或块未闭合
        json.JSONDecodeError: JSON 格式错误
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("未找到 JSON 起始 '{'")
    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        raise ValueError("JSON 块未闭合")
    return json.loads(text[start:end])


def _run_keygen_cli(*extra_args: str, env: dict = None) -> subprocess.CompletedProcess:
    """以子进程方式调用 ``python -m src keygen``

    Args:
        *extra_args: 透传给 keygen 的额外命令行参数（如 "--force"、"--key-dir"）
        env: 子进程环境变量（None 表示继承当前进程）

    Returns:
        subprocess.CompletedProcess（含 returncode/stdout/stderr）
    """
    cmd = [sys.executable, "-m", "src", "keygen", *extra_args]
    # cwd 设为项目根目录，确保 ``python -m src`` 能找到 src 包
    return subprocess.run(
        cmd,
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ssh-keygen -lf 输出格式："256 SHA256:xxxxx comment (ED25519)"
_SSH_KEYGEN_FP_RE = re.compile(r"SHA256:\S+")


def _ssh_keygen_fingerprint(public_key_path: str) -> str:
    """用 ssh-keygen -lf 读取公钥指纹

    Args:
        public_key_path: 公钥文件路径

    Returns:
        指纹字符串（"SHA256:..."）

    Raises:
        RuntimeError: ssh-keygen 不可用或输出无法解析
    """
    if _SSH_KEYGEN is None:
        raise RuntimeError("ssh-keygen 不可用")
    result = subprocess.run(
        [_SSH_KEYGEN, "-lf", public_key_path],
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh-keygen -lf 失败: {result.stderr}")
    match = _SSH_KEYGEN_FP_RE.search(result.stdout)
    if not match:
        raise RuntimeError(f"无法从 ssh-keygen 输出解析指纹: {result.stdout!r}")
    return match.group(0)


def _ssh_keygen_derive_public(private_key_path: str) -> str:
    """用 ssh-keygen -y -f 从私钥导出公钥行

    Args:
        private_key_path: 私钥文件路径

    Returns:
        公钥行（"ssh-ed25519 AAAA... comment"）

    Raises:
        RuntimeError: ssh-keygen 不可用或导出失败
    """
    if _SSH_KEYGEN is None:
        raise RuntimeError("ssh-keygen 不可用")
    result = subprocess.run(
        [_SSH_KEYGEN, "-yf", private_key_path],
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh-keygen -yf 失败: {result.stderr}")
    return result.stdout.strip()


class TestKeygenCliE2E:
    """真实 CLI 子进程链路的 keygen 端到端测试"""

    def test_cli_generates_keys_in_custom_dir(self, tmp_path):
        """CLI 调用应在指定目录生成私钥与公钥文件，且私钥可被本项目加载"""
        key_dir = str(tmp_path / "keys")
        result = _run_keygen_cli("--key-dir", key_dir, "--comment", "e2e@test")

        assert result.returncode == 0, f"CLI 失败: stdout={result.stdout!r} stderr={result.stderr!r}"
        assert os.path.exists(os.path.join(key_dir, "id_ed25519")), "私钥文件应存在"
        assert os.path.exists(os.path.join(key_dir, "id_ed25519.pub")), "公钥文件应存在"

        # 私钥可被本项目 PrivateKey.from_file 加载
        pk = PrivateKey.from_file(os.path.join(key_dir, "id_ed25519"))
        assert pk.fingerprint.startswith("SHA256:")

        # 公钥可被 load_authorized_keys 加载
        authorized = load_authorized_keys(os.path.join(key_dir, "id_ed25519.pub"))
        assert len(authorized) == 1

        # 公钥文件注释应为 e2e@test
        pub_content = Path(os.path.join(key_dir, "id_ed25519.pub")).read_text(encoding="utf-8").strip()
        assert pub_content.endswith("e2e@test"), f"公钥注释错误: {pub_content}"

    def test_cli_output_json_contains_fingerprint(self, tmp_path):
        """CLI stdout 输出的 JSON 应包含 fingerprint 字段，且与加载后的一致"""
        key_dir = str(tmp_path / "keys")
        result = _run_keygen_cli("--key-dir", key_dir)
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"

        payload = _parse_json_block(result.stdout)
        assert payload["status"] == "ok"
        assert payload["type"] == "keygen"
        assert "fingerprint" in payload
        assert payload["fingerprint"].startswith("SHA256:")

        # 加载私钥比对指纹
        pk = PrivateKey.from_file(os.path.join(key_dir, "id_ed25519"))
        assert pk.fingerprint == payload["fingerprint"]

    def test_cli_refuses_existing_without_force(self, tmp_path):
        """文件已存在时无 --force 应退出码 1"""
        key_dir = str(tmp_path / "keys")
        first = _run_keygen_cli("--key-dir", key_dir)
        assert first.returncode == 0

        second = _run_keygen_cli("--key-dir", key_dir)
        assert second.returncode == 1, f"应退出码 1: {second.stdout!r}"

    def test_cli_force_overwrites(self, tmp_path):
        """--force 应覆盖已存在文件并生成新密钥对"""
        key_dir = str(tmp_path / "keys")
        _run_keygen_cli("--key-dir", key_dir)
        old_bytes = Path(os.path.join(key_dir, "id_ed25519")).read_bytes()

        result = _run_keygen_cli("--key-dir", key_dir, "--force")
        assert result.returncode == 0, f"--force 失败: {result.stderr}"
        new_bytes = Path(os.path.join(key_dir, "id_ed25519")).read_bytes()
        assert old_bytes != new_bytes, "覆盖后私钥内容应不同（新密钥对）"

    def test_cli_default_key_dir_under_userprofile(self, tmp_path):
        """默认密钥目录应为 <HOME>/.pty-agent/keys（用 USERPROFILE 隔离避免污染真实家目录）

        Windows 下 os.path.expanduser('~') 读 USERPROFILE；
        通过子进程环境变量重定向 HOME，验证默认路径解析正确。
        """
        # 隔离 HOME：复制当前 env 并覆写 USERPROFILE（Windows）与 HOME（Unix 兼容）
        isolated_env = os.environ.copy()
        isolated_env["USERPROFILE"] = str(tmp_path)
        isolated_env["HOME"] = str(tmp_path)

        result = _run_keygen_cli(env=isolated_env)
        assert result.returncode == 0, f"CLI 失败: stdout={result.stdout!r} stderr={result.stderr!r}"

        expected_dir = tmp_path / ".pty-agent" / "keys"
        assert (expected_dir / "id_ed25519").exists(), f"默认路径应存在私钥: {expected_dir}"
        assert (expected_dir / "id_ed25519.pub").exists(), f"默认路径应存在公钥: {expected_dir}"

        # CLI 输出的 privateKeyPath 应指向隔离 HOME 下的路径
        payload = _parse_json_block(result.stdout)
        assert payload["privateKeyPath"].replace("\\", "/").endswith(".pty-agent/keys/id_ed25519"), \
            f"privateKeyPath 错误: {payload['privateKeyPath']}"

    def test_cli_stderr_contains_authorized_keys_hint(self, tmp_path):
        """CLI stderr 应包含把公钥追加到 authorized_keys 的提示"""
        key_dir = str(tmp_path / "keys")
        result = _run_keygen_cli("--key-dir", key_dir)
        assert result.returncode == 0
        assert "authorized_keys" in result.stderr, f"stderr 应含 authorized_keys 提示: {result.stderr!r}"
        assert "公钥已生成" in result.stderr


@pytest.mark.skipif(_SSH_KEYGEN is None, reason="系统未安装 ssh-keygen，跳过互操作测试")
class TestKeygenSshKeygenInterop:
    """项目生成的密钥与 OpenSSH ssh-keygen 互操作性测试"""

    def test_pubkey_fingerprint_matches_ssh_keygen(self, tmp_path):
        """项目输出的指纹应与 ssh-keygen -lf 读取的一致"""
        key_dir = str(tmp_path / "keys")
        result = _run_keygen_cli("--key-dir", key_dir, "--comment", "interop@pub")
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"

        # 项目输出指纹
        payload = _parse_json_block(result.stdout)
        project_fp = payload["fingerprint"]

        # ssh-keygen -lf 读取公钥指纹
        ssh_fp = _ssh_keygen_fingerprint(os.path.join(key_dir, "id_ed25519.pub"))
        assert ssh_fp == project_fp, \
            f"项目指纹 {project_fp} 与 ssh-keygen 指纹 {ssh_fp} 不一致"

    def test_private_key_loadable_by_ssh_keygen(self, tmp_path):
        """项目生成的私钥应可被 ssh-keygen -y -f 读取并导出公钥

        同时验证导出的公钥行与项目公钥文件内容一致（除注释外）。
        """
        key_dir = str(tmp_path / "keys")
        result = _run_keygen_cli("--key-dir", key_dir, "--comment", "interop@priv")
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"

        private_path = os.path.join(key_dir, "id_ed25519")
        public_path = os.path.join(key_dir, "id_ed25519.pub")

        # ssh-keygen 从私钥导出公钥（输出不含注释，仅 "ssh-ed25519 AAAA..."）
        derived = _ssh_keygen_derive_public(private_path)
        derived_parts = derived.split()
        assert len(derived_parts) >= 2, f"ssh-keygen 导出格式异常: {derived!r}"
        assert derived_parts[0] == "ssh-ed25519"

        # 项目公钥文件内容（含注释）
        project_pub = Path(public_path).read_text(encoding="utf-8").strip()
        project_parts = project_pub.split()
        # 比对 blob 部分（[1]），注释 [2] 不参与互操作验证
        assert derived_parts[1] == project_parts[1], \
            "ssh-keygen 导出的公钥 blob 与项目公钥文件不一致"

    def test_project_loads_ssh_keygen_generated_key(self, tmp_path):
        """反向互操作：ssh-keygen 生成的密钥应可被本项目加载

        1. ssh-keygen -t ed25519 生成密钥对
        2. 本项目 PrivateKey.from_file 应能加载该私钥
        3. 本项目 load_authorized_keys 应能加载该公钥
        4. 双方指纹一致
        """
        key_dir = str(tmp_path / "ssh_keys")
        os.makedirs(key_dir, exist_ok=True)
        private_path = os.path.join(key_dir, "id_ed25519")
        public_path = os.path.join(key_dir, "id_ed25519.pub")

        # ssh-keygen -t ed25519 -N "" -f <path> 生成无密码密钥
        gen = subprocess.run(
            [_SSH_KEYGEN, "-t", "ed25519", "-N", "", "-f", private_path,
             "-C", "ssh-keygen@generated"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8", errors="replace",
        )
        assert gen.returncode == 0, f"ssh-keygen 生成失败: {gen.stderr}"

        # 本项目加载私钥
        pk = PrivateKey.from_file(private_path)
        assert pk.fingerprint.startswith("SHA256:")

        # 本项目加载公钥
        authorized = load_authorized_keys(public_path)
        assert len(authorized) == 1
        for fp in authorized:
            assert fp == pk.fingerprint, "ssh-keygen 公钥指纹应与本项目私钥指纹一致"

        # 与 ssh-keygen -lf 比对
        ssh_fp = _ssh_keygen_fingerprint(public_path)
        assert ssh_fp == pk.fingerprint, "ssh-keygen 指纹应与本项目一致"
