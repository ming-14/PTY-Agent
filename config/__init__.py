"""配置数据目录（非 Python 包，仅占位保持一致导入语义）

配置按侧分离：
- 共享配置（common/shared/transfer.toml）留在本目录根
- daemon 专属（daemon/logging/web/sandbox.toml，sandbox.toml 可选）在 daemon/ 子目录
- client 专属（client.toml）在 client/ 子目录

加载规则见 ../src/config/__init__.py，清单见 README.md。
"""