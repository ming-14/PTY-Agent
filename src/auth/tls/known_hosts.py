"""TOFU 信任存储（类似 SSH known_hosts）

管理客户端已知的服务端证书指纹。

信任模型：
- 首次连接（TOFU）：自动信任并存储服务端证书指纹
- 后续连接：比对指纹，不匹配则拒绝
- 类似 SSH 的 StrictHostKeyChecking=accept-new

文件格式:
    <host>:<port> <sha256-fingerprint>
    # 注释行以 # 开头
"""

from ...logging import get_logger
import os
import threading
from typing import Dict, Optional, Tuple

_logger = get_logger("pty-auth-tls")


class KnownHosts:
    """TOFU 信任存储管理器

    线程安全：内部使用锁保护读写操作。

    Attributes:
        path: known_hosts 文件路径。
    """

    def __init__(self, path: str):
        self.path = os.path.expanduser(path)
        self._lock = threading.Lock()
        self._entries: Dict[Tuple[str, int], str] = {}
        self._load()

    def _load(self):
        """从文件加载已知主机列表

        文件不存在时视为空列表，不报错。
        格式错误行跳过并记录警告。
        """
        if not os.path.exists(self.path):
            _logger.debug("known_hosts 文件不存在: %s", self.path)
            return

        with open(self.path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    _logger.warning(
                        "known_hosts 第 %d 行格式错误，跳过: %s",
                        line_no,
                        line[:50],
                    )
                    continue
                host_port, fingerprint = parts
                # 解析 host:port
                if ":" not in host_port:
                    _logger.warning(
                        "known_hosts 第 %d 行 host:port 格式错误: %s",
                        line_no,
                        host_port,
                    )
                    continue
                host, port_str = host_port.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    _logger.warning(
                        "known_hosts 第 %d 行端口非数字: %s",
                        line_no,
                        port_str,
                    )
                    continue
                self._entries[(host, port)] = fingerprint

        _logger.debug("已加载 %d 条 known_hosts 记录", len(self._entries))

    def get(self, host: str, port: int) -> Optional[str]:
        """获取已信任的指纹

        Args:
            host: 服务端主机名或 IP。
            port: 服务端端口。

        Returns:
            已信任的指纹字符串，未找到返回 None。
        """
        with self._lock:
            return self._entries.get((host, port))

    def trust(self, host: str, port: int, fingerprint: str):
        """信任新指纹（TOFU 首次连接）

        将指纹写入内存并持久化到文件。

        Args:
            host: 服务端主机名或 IP。
            port: 服务端端口。
            fingerprint: 证书指纹（格式 "sha256:<hex>"）。
        """
        with self._lock:
            self._entries[(host, port)] = fingerprint
            self._save()

        _logger.info(
            "TOFU 信任新主机: %s:%d (指纹: %s...)", host, port, fingerprint[:32]
        )

    def verify(self, host: str, port: int, fingerprint: str) -> bool:
        """验证指纹（TOFU 模型）

        - 已信任：比对指纹，匹配返回 True，不匹配返回 False
        - 未信任：TOFU 信任（存储指纹），返回 True

        Args:
            host: 服务端主机名或 IP。
            port: 服务端端口。
            fingerprint: 待验证的证书指纹。

        Returns:
            True 表示信任（首次或匹配），False 表示指纹不匹配。
        """
        with self._lock:
            existing = self._entries.get((host, port))
            if existing is None:
                # 首次连接，TOFU 信任
                self._entries[(host, port)] = fingerprint
                self._save()
                _logger.info(
                    "TOFU 首次信任: %s:%d (指纹: %s...)",
                    host,
                    port,
                    fingerprint[:32],
                )
                return True

            if existing == fingerprint:
                _logger.debug("指纹验证通过: %s:%d", host, port)
                return True

            _logger.warning(
                "指纹不匹配! %s:%d\n  已知: %s\n  实际: %s",
                host,
                port,
                existing[:32],
                fingerprint[:32],
            )
            return False

    def remove(self, host: str, port: int) -> bool:
        """移除已信任的主机

        用于证书重新生成后删除旧指纹。

        Args:
            host: 服务端主机名或 IP。
            port: 服务端端口。

        Returns:
            True 表示已移除，False 表示原本不存在。
        """
        with self._lock:
            key = (host, port)
            if key in self._entries:
                del self._entries[key]
                self._save()
                _logger.info("已移除 known_hosts 记录: %s:%d", host, port)
                return True
            return False

    def _save(self):
        """持久化到文件

        将所有条目写入 known_hosts 文件。
        调用方需持有 _lock。
        """
        # 确保目录存在
        dir_path = os.path.dirname(self.path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# PTY-Agent known_hosts - TOFU 信任存储\n")
            f.write("# 格式: <host>:<port> <sha256-fingerprint>\n")
            f.writelines(
                f"{host}:{port} {fp}\n"
                for (host, port), fp in sorted(self._entries.items())
            )
