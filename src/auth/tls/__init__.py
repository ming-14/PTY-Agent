"""TLS 认证支持 — 证书管理与 TOFU 信任存储

提供 TLS 传输层安全所需的基础设施：

- CertificateManager:  自签证书生成/加载/指纹计算（类似 SSH host key）
- KnownHosts:          TOFU 信任存储管理（类似 SSH known_hosts）
"""

from .cert_manager import CertificateManager
from .known_hosts import KnownHosts

__all__ = ["CertificateManager", "KnownHosts"]
