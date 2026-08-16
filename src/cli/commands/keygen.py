"""keygen 命令：生成 Ed25519 公私钥对"""

import argparse
import getpass
import json
import os
import socket
import sys

from ..base import Command, CommandContext
from ...logging import get_logger

_logger = get_logger("pty-client")


class KeygenCommand(Command):
    """keygen 命令"""

    name = "keygen"
    help = "生成 Ed25519 公私钥对（用于公私钥认证）"
    description = (
        "生成 Ed25519 密钥对并写入 ~/.pty-agent/keys/，\n"
        "用于 CONNECT_MODE=tls 时的非对称认证。\n"
        "生成后需把公钥追加到服务端 ~/.pty-agent/authorized_keys"
    )
    use_common_args = False
    needs_client = False
    formatter_class = argparse.RawDescriptionHelpFormatter

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force", "-f", action="store_true", default=False, help="覆盖已存在的密钥文件"
        )
        parser.add_argument(
            "--key-dir", default=None, help="密钥目录（默认 ~/.pty-agent/keys）"
        )
        parser.add_argument(
            "--comment", "-C", default=None, help="公钥注释（默认 用户名@主机名）"
        )

    def run(self, args, ctx: CommandContext) -> None:
        """生成 Ed25519 密钥对并写入文件"""
        from ...auth.keys import generate_keypair
        from ...client.input import safe_print
        from ...protocol.response import Response

        # 确定密钥目录（expandvars 展开 %TEMP%/$TEMP 类环境变量，expanduser 展开 ~）
        if args.key_dir:
            key_dir = os.path.expandvars(os.path.expanduser(args.key_dir))
        else:
            key_dir = os.path.join(os.path.expanduser("~"), ".pty-agent", "keys")

        private_key_path = os.path.join(key_dir, "id_ed25519")
        public_key_path = os.path.join(key_dir, "id_ed25519.pub")

        # 检查文件是否存在（除非 --force）
        if not args.force:
            if os.path.exists(private_key_path):
                safe_print(
                    json.dumps(
                        Response.error(
                            f"私钥文件已存在: {private_key_path}\n使用 --force 覆盖"
                        ),
                        ensure_ascii=False,
                    )
                )
                sys.exit(1)
            if os.path.exists(public_key_path):
                safe_print(
                    json.dumps(
                        Response.error(
                            f"公钥文件已存在: {public_key_path}\n使用 --force 覆盖"
                        ),
                        ensure_ascii=False,
                    )
                )
                sys.exit(1)

        # 创建目录
        os.makedirs(key_dir, exist_ok=True)

        # 生成密钥对
        _logger.info("开始生成 Ed25519 密钥对到 %s", key_dir)
        private_key = generate_keypair()

        # 写入私钥文件（OpenSSH 格式，无密码）
        private_bytes = private_key.to_openssh_bytes()
        self._write_key_file(private_key_path, private_bytes, mode=0o600)

        # 写入公钥文件（OpenSSH authorized_keys 格式）
        comment = args.comment or f"{getpass.getuser()}@{socket.gethostname()}"
        public_line = private_key.public_key.to_ssh_line(comment=comment)
        self._write_key_file(public_key_path, (public_line + "\n").encode("utf-8"), mode=0o644)

        fingerprint = private_key.fingerprint
        _logger.info("Ed25519 密钥对已生成: %s", private_key_path)

        safe_print(
            json.dumps(
                {
                    "type": "keygen",
                    "status": "ok",
                    "privateKeyPath": private_key_path,
                    "publicKeyPath": public_key_path,
                    "fingerprint": fingerprint,
                    "publicKey": public_line,
                    "comment": comment,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        authorized_keys_path = os.path.join(
            os.path.expanduser("~"), ".pty-agent", "authorized_keys"
        )
        print(
            f"\n公钥已生成，请将其追加到服务端 authorized_keys 文件:\n"
            f"  {authorized_keys_path}\n\n"
            f"公钥内容:\n  {public_line}\n\n"
            f"指纹: {fingerprint}",
            file=sys.stderr,
        )

    @staticmethod
    def _write_key_file(path: str, data: bytes, mode: int) -> None:
        """写入密钥文件并设置权限"""
        if os.name == "nt":
            with open(path, "wb") as f:
                f.write(data)
            _logger.debug("Windows 平台，跳过权限设置: %s", path)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            _logger.debug("Unix 平台，已设置权限 %o: %s", mode, path)