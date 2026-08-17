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
        # stderr 提示也走 safe_print：stderr 被重定向且编码为 GBK 时强制 UTF-8，
        # 与 stdout 同理，避免中文提示在 UTF-8 管道里乱码
        safe_print(
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
            # Windows 无 Unix 0600 语义：私钥文件收紧 ACL（去继承 + 仅当前用户/
            # SYSTEM/Administrators），否则继承自父目录的多余 ACE（如 %TEMP% 的
            # AppContainer 条目）会让 OpenSSH 工具（ssh-keygen）拒绝读取私钥
            if mode == 0o600:
                _restrict_private_key_acl(path)
                _logger.debug("Windows 平台，私钥 ACL 已收紧: %s", path)
            else:
                _logger.debug("Windows 平台，公钥不设访问限制: %s", path)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            _logger.debug("Unix 平台，已设置权限 %o: %s", mode, path)


def _restrict_private_key_acl(path: str) -> None:
    """Windows 收紧私钥文件 ACL：移除继承，仅保留当前用户 + SYSTEM + Administrators

    通过进程令牌取当前用户 SID、CreateWellKnownSid 取 SYSTEM/Administrators SID，
    用 InitializeAcl + AddAccessAllowedAce 构建仅含三者的 DACL，配合
    PROTECTED_DACL 标志切断继承后以 SetNamedSecurityInfoW 应用。
    与 OpenSSH 写私钥时的 ACL 构成一致。
    """
    import ctypes
    from ctypes import wintypes as W

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kern = ctypes.WinDLL("kernel32", use_last_error=True)

    # ── 常量 ──
    TOKEN_QUERY = 0x0008
    TokenUser = 1  # TOKEN_INFORMATION_CLASS
    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 0x00000004
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    # WellKnownSidType：WinLocalSystemSid / WinBuiltinAdministratorsSid
    WIN_LOCAL_SYSTEM_SID = 22
    WIN_BUILTIN_ADMINISTRATORS_SID = 26
    ACL_REVISION = 2
    FILE_ALL_ACCESS = 0x1F01FF

    class TOKEN_USER(ctypes.Structure):
        """TOKEN_USER — TokenUser 信息类返回结构（取当前用户 SID）"""

        _fields_ = [
            ("Sid", ctypes.c_void_p),
            ("Attributes", W.DWORD),
        ]

    open_ = adv.OpenProcessToken
    open_.restype = W.BOOL
    open_.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
    get_token_info = adv.GetTokenInformation
    get_token_info.restype = W.BOOL
    get_token_info.argtypes = [
        W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)
    ]
    close_handle = kern.CloseHandle
    close_handle.restype = W.BOOL
    close_handle.argtypes = [W.HANDLE]
    get_length_sid = adv.GetLengthSid
    get_length_sid.restype = W.DWORD
    get_length_sid.argtypes = [ctypes.c_void_p]
    create_well_known_sid = adv.CreateWellKnownSid
    create_well_known_sid.restype = W.BOOL
    create_well_known_sid.argtypes = [
        W.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(W.DWORD)
    ]
    initialize_acl = adv.InitializeAcl
    initialize_acl.restype = W.BOOL
    initialize_acl.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD]
    add_access_allowed_ace = adv.AddAccessAllowedAce
    add_access_allowed_ace.restype = W.BOOL
    add_access_allowed_ace.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p]
    set_named_security_info = adv.SetNamedSecurityInfoW
    set_named_security_info.restype = W.DWORD
    set_named_security_info.argtypes = [
        W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ]

    def _current_user_sid() -> bytes:
        """从进程令牌取当前用户 SID 字节"""
        token = W.HANDLE()
        if not open_(kern.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            needed = W.DWORD(0)
            get_token_info(token, TokenUser, None, 0, ctypes.byref(needed))
            buf = ctypes.create_string_buffer(needed.value)
            if not get_token_info(
                token, TokenUser, buf, needed.value, ctypes.byref(needed)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            psid = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents.Sid
            return ctypes.string_at(psid, get_length_sid(ctypes.c_void_p(psid)))
        finally:
            close_handle(token)

    def _well_known_sid(sid_type: int) -> bytes:
        """取内置 SID 字节（SID 最大 68 字节缓冲）"""
        buf = ctypes.create_string_buffer(68)
        length = W.DWORD(68)
        if not create_well_known_sid(sid_type, None, buf, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.raw[: length.value]

    # 三个受托者：当前用户 + SYSTEM + Administrators（与 OpenSSH 私钥 ACL 构成一致）
    sids = [
        _current_user_sid(),
        _well_known_sid(WIN_LOCAL_SYSTEM_SID),
        _well_known_sid(WIN_BUILTIN_ADMINISTRATORS_SID),
    ]

    # ACL 大小 = 头(8) + 每个 ACE(头 8 + SID 变长)；缓冲留足余量
    acl = ctypes.create_string_buffer(8 + sum(8 + len(s) for s in sids) + 32)
    if not initialize_acl(acl, ctypes.sizeof(acl), ACL_REVISION):
        raise ctypes.WinError(ctypes.get_last_error())
    for sid_bytes in sids:
        # SID 拷贝必须存续到 AddAccessAllowedAce 完成（ctypes 自动对象不保活）
        sid_buf = ctypes.create_string_buffer(sid_bytes)
        if not add_access_allowed_ace(acl, ACL_REVISION, FILE_ALL_ACCESS, sid_buf):
            raise ctypes.WinError(ctypes.get_last_error())

    rc = set_named_security_info(
        path,
        SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None,
    )
    if rc != 0:
        raise ctypes.WinError(rc)