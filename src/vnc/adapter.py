"""VNC 服务：实现 VncServicePort，管理 winvnc.exe 进程。

职责：
- 根据 ENABLE_VNC / VNC_WINVNC_PATH 配置推导各资源路径
- 实例化 VncProcessConfig + VncProcessManager
- 对外暴露 VncServicePort 接口

WebSocket→VNC TCP 代理由守护进程的 /vnc/websockify 端点实现，无需 websockify 子进程。
"""

from pathlib import Path
from typing import Optional

from ..config.common import PROJECT_ROOT
from ..config.daemon import ENABLE_VNC, VNC_WINVNC_PATH
from .ports import VncServicePort
from .process_manager import VncProcessConfig, VncProcessManager
from ..logging import get_logger

_logger = get_logger("pty-vnc")


def _resolve_vnc_module_dir() -> Path:
    """推导 VNC 模块根目录（src/vnc）。

    路径：src/vnc/adapter.py → src/vnc/ → src/ → src/vnc
    """
    here = Path(__file__).resolve()
    src_dir = here.parent.parent  # src/
    return src_dir / "vnc"


def _resolve_novnc_web_dir(vnc_module_dir: Path) -> Path:
    """noVNC 前端静态目录（用于定位 vnc_password.py 模块 + web 层 mount 静态资源）。"""
    # noVNC 前端资源统一放在 src/web/static/vendor/novnc
    # vnc_module_dir 是 src/vnc，parent 是 src/
    src_dir = vnc_module_dir.parent
    return src_dir / "web" / "static" / "vendor" / "novnc"


def _resolve_winvnc_exe() -> Path:
    """推导 winvnc.exe 路径。

    优先使用 VNC_WINVNC_PATH 配置，未配置时使用项目根 bin/ultravnc/winvnc.exe。
    """
    if VNC_WINVNC_PATH:
        return Path(VNC_WINVNC_PATH).expanduser().resolve()
    return (Path(PROJECT_ROOT) / "bin" / "ultravnc" / "winvnc.exe").resolve()


def get_novnc_web_dir() -> Path:
    """返回 noVNC 前端静态目录路径。

    供 web 层或其他外部模块查询 noVNC 前端资源位置，
    VNC 服务本身不关心此路径如何被使用。
    """
    return _resolve_novnc_web_dir(_resolve_vnc_module_dir())


class VncAdapter(VncServicePort):
    """VncServicePort 的 VNC 实现。

    通过 VncProcessManager 管理 winvnc.exe 进程。
    当 ENABLE_VNC=False 或 winvnc.exe 缺失时，is_available() 返回 False，
    所有操作方法会抛出 RuntimeError 或返回空状态。
    """

    def __init__(self):
        vnc_module_dir = _resolve_vnc_module_dir()
        self._novnc_web_dir = _resolve_novnc_web_dir(vnc_module_dir)
        vnc_src_dir = vnc_module_dir / "src"
        self._winvnc_exe = _resolve_winvnc_exe()
        self._ultravnc_dir = self._winvnc_exe.parent
        self._logs_dir = Path(PROJECT_ROOT) / "logs"

        self._config = VncProcessConfig(
            winvnc_exe=self._winvnc_exe,
            ultravnc_dir=self._ultravnc_dir,
            vnc_src_dir=vnc_src_dir,
            logs_dir=self._logs_dir,
        )
        self._manager = VncProcessManager(self._config)

        if ENABLE_VNC:
            _logger.info(
                "VNC service initialized: winvnc=%s available=%s",
                self._winvnc_exe,
                self.is_winvnc_available(),
            )
        else:
            _logger.info("VNC service disabled by config (ENABLE_VNC=False)")

    def is_available(self) -> bool:
        """VNC 功能是否可用（配置启用 + winvnc.exe 存在 + 前端目录存在）。"""
        if not ENABLE_VNC:
            return False
        if not self._winvnc_exe.exists():
            return False
        if not self._novnc_web_dir.exists():
            return False
        return True

    def is_winvnc_available(self) -> bool:
        """winvnc.exe 是否存在。"""
        return self._winvnc_exe.exists()

    def start(self) -> dict:
        """启动 winvnc.exe。"""
        if not ENABLE_VNC:
            raise RuntimeError("VNC disabled by config (ENABLE_VNC=False)")
        if not self._winvnc_exe.exists():
            raise RuntimeError(f"winvnc.exe not found: {self._winvnc_exe}")
        return self._manager.start_all()

    def stop(self) -> None:
        """停止 winvnc.exe。"""
        self._manager.stop_all()

    def get_status(self) -> dict:
        """返回运行状态。VNC 未启用时返回 disabled 状态。"""
        if not ENABLE_VNC:
            return {
                "running": False,
                "disabled": True,
                "vnc_running": False,
                "vnc_port": None,
                "vnc_pid": None,
                "password": None,
                "winvnc_available": False,
            }
        status = self._manager.get_status()
        status["disabled"] = False
        status["winvnc_available"] = self._winvnc_exe.exists()
        return status

    def get_connection_info(self) -> Optional[dict]:
        """返回前端连接所需信息。"""
        if not ENABLE_VNC:
            return None
        return self._manager.get_connection_info()

    @property
    def winvnc_exe(self) -> Path:
        """winvnc.exe 路径。"""
        return self._winvnc_exe

    def cleanup(self) -> None:
        """daemon 退出时清理子进程。"""
        try:
            self._manager.stop_all()
        except Exception as e:
            _logger.warning("VNC cleanup failed: %s", e)
