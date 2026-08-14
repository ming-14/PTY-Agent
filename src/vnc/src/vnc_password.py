import ctypes
import shutil
from pathlib import Path

kernel32 = ctypes.windll.kernel32


_BIT_REVERSE_TABLE = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))


def _reverse_bits(byte: int) -> int:
    return _BIT_REVERSE_TABLE[byte]


VNC_FIXED_KEY = bytes([23, 82, 107, 6, 35, 78, 88, 7])
VNC_DES_KEY = bytes(_reverse_bits(b) for b in VNC_FIXED_KEY)


def _des_encrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    cipher = Cipher(TripleDES(key * 3), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()


def encrypt_vnc_password(password: str) -> bytes:
    if not password:
        return b""
    padded = password.encode("latin-1")[:8].ljust(8, b"\x00")
    return _des_encrypt(VNC_DES_KEY, padded)


def _write_profile_string(section: str, key: str, value: str, filepath: str):
    kernel32.WritePrivateProfileStringW(section, key, value, filepath)


def _write_profile_struct(section: str, key: str, data: bytes, filepath: str):
    buf = ctypes.create_string_buffer(data, len(data))
    kernel32.WritePrivateProfileStructW(section, key, buf, len(data), filepath)


def write_ultravnc_ini(
    ultravnc_dir: Path,
    password: str = "",
    port: int = 5900,
    remove_wallpaper: bool = False,
):
    ini_path = ultravnc_dir / "ultravnc.ini"
    ini_str = str(ini_path)

    if not ini_path.exists():
        example_path = ultravnc_dir / "ultravnc.ini.example"
        if example_path.exists():
            shutil.copy2(str(example_path), str(ini_path))

    _write_profile_string("UltraVNC", "Secure", "0", ini_str)
    _write_profile_string("admin", "UseRegistry", "0", ini_str)
    _write_profile_string("admin", "DebugMode", "2", ini_str)
    _write_profile_string("admin", "Avilog", "0", ini_str)
    _write_profile_string("admin", "path", str(ultravnc_dir), ini_str)
    _write_profile_string("admin", "DebugLevel", "10", ini_str)
    _write_profile_string("admin", "DisableTrayIcon", "0", ini_str)
    _write_profile_string("admin", "LoopbackOnly", "0", ini_str)
    _write_profile_string("admin", "AcceptHTTP", "0", ini_str)
    _write_profile_string("admin", "AuthRequired", "1", ini_str)
    _write_profile_string("admin", "ConnectPriority", "0", ini_str)
    _write_profile_string(
        "admin", "RemoveWallpaper", "1" if remove_wallpaper else "0", ini_str
    )
    _write_profile_string("admin", "PortNumber", str(port), ini_str)
    _write_profile_string("admin", "HTTPPortNumber", "0", ini_str)
    _write_profile_string("admin", "AutoPortSelect", "0", ini_str)
    _write_profile_string("admin", "SocketConnect", "1", ini_str)

    enc_pw = b""
    if password:
        enc_pw = encrypt_vnc_password(password)
        _write_profile_struct("UltraVNC", "passwd", enc_pw, ini_str)
        _write_profile_struct("UltraVNC", "passwd2", enc_pw, ini_str)

    marker = ultravnc_dir / "ultravnc.portable"
    if not marker.exists():
        marker.touch()

    return enc_pw.hex().upper() if enc_pw else ""


if __name__ == "__main__":
    import sys

    pw = sys.argv[1] if len(sys.argv) > 1 else "123456"
    result = encrypt_vnc_password(pw)
    print(f"Password '{pw}' -> {result.hex().upper()}")
