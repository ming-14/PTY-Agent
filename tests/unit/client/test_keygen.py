"""Phase 4 单元测试 —— keygen 子命令

覆盖场景：
- 基本生成：密钥文件创建、格式正确、指纹可被 ssh-keygen 风格识别
- 私钥可被 PrivateKey.from_file 加载
- 公钥可被 load_authorized_keys 加载
- --force 覆盖已存在文件
- 文件已存在时拒绝覆盖（无 --force）
- --key-dir 自定义目录
- --comment 自定义注释
"""

import os
import sys
import json
import shutil
import tempfile
import argparse
import pytest
from pathlib import Path

# 确保能 import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from src.auth.keys import PrivateKey, PublicKey, load_authorized_keys, generate_keypair
from src.cli.commands import register_all
from src.cli.commands.keygen import KeygenCommand
from src.cli.registry import CommandRegistry


def _build_parser():
    """经命令注册表构建完整解析器"""
    registry = CommandRegistry()
    register_all(registry)
    return registry.build_parser(prog="pty-agent", description="", epilog="")


def _keygen(args):
    """执行 keygen 命令（ctx 不需要）"""
    return KeygenCommand().run(args, None)


def _make_args(key_dir, force=False, comment=None):
    """构造 keygen 所需的 argparse Namespace"""
    return argparse.Namespace(
        subcmd="keygen",
        force=force,
        key_dir=key_dir,
        comment=comment,
    )


class TestKeygenBasic:
    """基本生成功能测试"""

    def test_generates_keypair_files(self, tmp_path):
        """keygen 应创建私钥与公钥文件"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir))

        private_path = os.path.join(key_dir, "id_ed25519")
        public_path = os.path.join(key_dir, "id_ed25519.pub")
        assert os.path.exists(private_path), "私钥文件应存在"
        assert os.path.exists(public_path), "公钥文件应存在"

    def test_private_key_is_openssh_format(self, tmp_path):
        """私钥文件应为 OpenSSH 格式（PEM 包装）"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir))

        private_path = os.path.join(key_dir, "id_ed25519")
        content = Path(private_path).read_text(encoding="utf-8")
        assert "-----BEGIN OPENSSH PRIVATE KEY-----" in content
        assert "-----END OPENSSH PRIVATE KEY-----" in content

    def test_public_key_is_authorized_keys_format(self, tmp_path):
        """公钥文件应为 authorized_keys 格式（ssh-ed25519 AAAA... comment）"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir, comment="unit@test"))

        public_path = os.path.join(key_dir, "id_ed25519.pub")
        content = Path(public_path).read_text(encoding="utf-8").strip()
        parts = content.split()
        assert parts[0] == "ssh-ed25519", f"类型应为 ssh-ed25519, got {parts[0]}"
        assert parts[2] == "unit@test", f"注释应为 unit@test, got {parts[2]}"

    def test_generated_private_key_loadable(self, tmp_path):
        """生成的私钥可被 PrivateKey.from_file 加载"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir))

        private_path = os.path.join(key_dir, "id_ed25519")
        pk = PrivateKey.from_file(private_path)
        assert pk.fingerprint.startswith("SHA256:")

    def test_generated_public_key_loadable(self, tmp_path):
        """生成的公钥可被 load_authorized_keys 加载"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir, comment="auth@test"))

        public_path = os.path.join(key_dir, "id_ed25519.pub")
        authorized = load_authorized_keys(public_path)
        assert len(authorized) == 1, "应加载 1 个公钥"
        for fp in authorized:
            assert fp.startswith("SHA256:")

    def test_fingerprint_in_output_matches_loaded(self, tmp_path, capsys):
        """keygen 输出的指纹与加载后的一致"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir))
        captured = capsys.readouterr()

        # 从 stdout 解析 JSON 输出（_keygen 用 indent=2 多行输出，
        # 故从首个 '{' 抓到匹配的 '}' 而非按行解析）
        out = captured.out
        start = out.find("{")
        assert start != -1, "应有 JSON 输出"
        # 通过花括号配平定位 JSON 结束位置
        depth = 0
        end = -1
        for i in range(start, len(out)):
            if out[i] == "{":
                depth += 1
            elif out[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        assert end != -1, "JSON 块未闭合"
        result = json.loads(out[start:end])
        output_fp = result["fingerprint"]

        # 加载私钥比对指纹
        private_path = os.path.join(key_dir, "id_ed25519")
        pk = PrivateKey.from_file(private_path)
        assert pk.fingerprint == output_fp, "输出指纹应与加载的一致"


class TestKeygenForce:
    """--force 覆盖测试"""

    def test_refuse_without_force(self, tmp_path, capsys):
        """文件已存在时拒绝覆盖（无 --force）"""
        key_dir = str(tmp_path / "keys")
        # 先生成一次
        _keygen(_make_args(key_dir))
        # 再生成一次应失败
        with pytest.raises(SystemExit) as exc_info:
            _keygen(_make_args(key_dir))
        assert exc_info.value.code == 1

    def test_force_overwrites(self, tmp_path):
        """--force 覆盖已存在文件"""
        key_dir = str(tmp_path / "keys")
        # 先生成一次
        _keygen(_make_args(key_dir))
        private_path = os.path.join(key_dir, "id_ed25519")
        old_content = Path(private_path).read_bytes()

        # --force 覆盖
        _keygen(_make_args(key_dir, force=True))
        new_content = Path(private_path).read_bytes()

        assert old_content != new_content, "覆盖后内容应不同（新密钥对）"


class TestKeygenOptions:
    """命令行选项测试"""

    def test_custom_key_dir(self, tmp_path):
        """--key-dir 自定义目录"""
        custom_dir = str(tmp_path / "custom" / "nested" / "keys")
        _keygen(_make_args(custom_dir))
        assert os.path.exists(os.path.join(custom_dir, "id_ed25519"))
        assert os.path.exists(os.path.join(custom_dir, "id_ed25519.pub"))

    def test_custom_comment(self, tmp_path):
        """--comment 自定义注释"""
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir, comment="my@comment"))
        public_path = os.path.join(key_dir, "id_ed25519.pub")
        content = Path(public_path).read_text(encoding="utf-8").strip()
        assert content.endswith("my@comment")

    def test_default_comment_format(self, tmp_path):
        """默认注释格式为 用户名@主机名"""
        import getpass
        import socket
        key_dir = str(tmp_path / "keys")
        _keygen(_make_args(key_dir))  # comment=None
        public_path = os.path.join(key_dir, "id_ed25519.pub")
        content = Path(public_path).read_text(encoding="utf-8").strip()
        expected_suffix = f"{getpass.getuser()}@{socket.gethostname()}"
        assert content.endswith(expected_suffix), f"应以 {expected_suffix} 结尾"


class TestKeygenParser:
    """argparse 解析测试"""

    def test_keygen_in_subcommands(self):
        """build_parser 应包含 keygen 子命令"""
        parser = _build_parser()
        args = parser.parse_args(["keygen"])
        assert args.subcmd == "keygen"
        assert args.force is False
        assert args.key_dir is None
        assert args.comment is None

    def test_keygen_force_flag(self):
        """--force 标志解析"""
        parser = _build_parser()
        args = parser.parse_args(["keygen", "--force"])
        assert args.force is True

    def test_keygen_key_dir(self):
        """--key-dir 参数解析"""
        parser = _build_parser()
        args = parser.parse_args(["keygen", "--key-dir", "/tmp/mykeys"])
        assert args.key_dir == "/tmp/mykeys"

    def test_keygen_comment(self):
        """--comment 参数解析"""
        parser = _build_parser()
        args = parser.parse_args(["keygen", "-C", "user@host"])
        assert args.comment == "user@host"


class TestWriteKeyFile:
    """KeygenCommand._write_key_file 辅助函数测试"""

    def test_write_file_content(self, tmp_path):
        """写入内容正确"""
        path = str(tmp_path / "test.key")
        data = b"test content"
        KeygenCommand._write_key_file(path, data, mode=0o600)
        assert Path(path).read_bytes() == data

    def test_write_overwrites_existing(self, tmp_path):
        """覆盖已存在文件"""
        path = str(tmp_path / "test.key")
        Path(path).write_bytes(b"old")
        KeygenCommand._write_key_file(path, b"new", mode=0o600)
        assert Path(path).read_bytes() == b"new"
