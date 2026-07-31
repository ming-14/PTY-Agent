"""FastScreen 流媒体控制器（展示层）。

提供三种流格式的端点，复用 StreamManager 的多客户端共享会话：
- GET  /fastscreen/mjpeg          — MJPEG 流（HTTP multipart/x-mixed-replace）
- WS   /fastscreen/ws/mse         — H264 MSE 流（fmp4 init + media segment，binary 推送）
- WS   /fastscreen/ws/webcodecs   — H264 WebCodecs 流（annexb NAL，binary 推送）

所有端点接收 query/body 参数：target_type (monitor|window) / target_id / method / fps / quality / bitrate / gop_size / width / height

流端点直接调用 StreamManager.subscribe/unsubscribe，不经过应用层消息队列
（流是长连接，不适合消息队列；StreamManager 内部已实现多客户端共享）。
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional

from fastapi import APIRouter, Request, WebSocket, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ....fastscreen.ports import FastScreenServicePort
from ....config.common import IS_WINDOWS
from ...presentation.controllers.auth_controller import validate_ws_auth

_logger = logging.getLogger("pty-web-fastscreen")


def _is_window_valid(hwnd: int) -> bool:
    """检查窗口句柄是否仍然有效（窗口未被关闭/销毁）。

    仅 Windows 平台可用。非 Windows 或 hwnd 为 0 时返回 True（不阻断）。
    """
    if not IS_WINDOWS or hwnd == 0:
        return True
    try:
        import ctypes
        return ctypes.windll.user32.IsWindow(hwnd) != 0
    except Exception:
        return True  # 检查失败时不阻断，避免误判


def _is_window_minimized(hwnd: int) -> bool:
    """主动检查窗口是否已最小化（IsIconic）。

    用于替代旧的"超时计数"stall 检测：窗口最小化时 WGC 不产生帧，
    与其等超时累积，不如每次循环主动查 IsIconic，立即得到准确状态。
    仅 Windows 平台可用。非 Windows 或 hwnd 为 0 时返回 False（不阻断）。
    """
    if not IS_WINDOWS or hwnd == 0:
        return False
    try:
        import ctypes
        return ctypes.windll.user32.IsIconic(hwnd) != 0
    except Exception:
        return False


# 懒加载 fastscreen 常量（首次使用时导入，避免 adapter 未初始化时失败）
_TargetType = None
_CaptureMethod = None
_MjpegStreamer = None
_H264Streamer = None
_H264MSEStreamer = None
_modules_loaded = False
# 1x1 纯红 JPEG 信号帧：MJPEG 流中窗口最小化时发送，前端通过 naturalWidth===1 + 红色像素校验识别
# 在 _load_streamer_modules() 中懒加载生成（依赖 fastscreencore 已初始化）
_STALL_JPEG_BYTES: Optional[bytes] = None


def _load_streamer_modules():
    """懒加载 streamer 类与 fastscreen 常量。

    依赖 FastScreenAdapter 已将 bin/ 加入 sys.path（fastscreencore）。
    streamer 模块用相对导入（....fastscreen.streamers.*）避免 src/ 在 sys.path 时的包边界问题。
    """
    global _TargetType, _CaptureMethod, _MjpegStreamer, _H264Streamer, _H264MSEStreamer, \
        _modules_loaded, _STALL_JPEG_BYTES
    if _modules_loaded:
        return
    try:
        from fastscreencore import TargetType, CaptureMethod
        from ....fastscreen.streamers.mjpeg import MjpegStreamer
        from ....fastscreen.streamers.h264 import H264Streamer
        from ....fastscreen.streamers.h264_mse import H264MSEStreamer
        from ....fastscreen.streamers.encoding.mjpeg import encode_bgra_to_jpeg
        _TargetType = TargetType
        _CaptureMethod = CaptureMethod
        _MjpegStreamer = MjpegStreamer
        _H264Streamer = H264Streamer
        _H264MSEStreamer = H264MSEStreamer
        # 预生成 1x1 纯红 JPEG（BGRA: B=0,G=0,R=255,A=255）作为 stall 信号帧
        # 前端 MJPEG 通过 naturalWidth===1 + 红色像素校验识别
        try:
            _STALL_JPEG_BYTES = encode_bgra_to_jpeg(b'\x00\x00\xFF\xFF', 1, 1, 4, quality=0.9)
        except Exception as e:
            _logger.warning("stall JPEG pre-generation failed: %s", e)
            _STALL_JPEG_BYTES = b''
        _modules_loaded = True
    except Exception as e:
        _logger.exception("FastScreen streamer modules load failed: %s", e)
        _modules_loaded = True  # 标记已尝试


# ── 参数解析工具 ──

_METHOD_MAP = {
    "auto": None,  # 由 CaptureMethod.AUTO 填充
    "dxgi": None,
    "wgc": None,
    "bitblt": None,
}


def _parse_target_type(s: str):
    """字符串 → TargetType 枚举值。"""
    if _TargetType is None:
        return 0  # MONITOR
    return _TargetType.WINDOW if s.lower() == "window" else _TargetType.MONITOR


def _parse_method(s: str):
    """字符串 → CaptureMethod 枚举值。"""
    if _CaptureMethod is None:
        return 0  # AUTO
    m = {
        "auto": _CaptureMethod.AUTO,
        "dxgi": _CaptureMethod.DXGI,
        "wgc": _CaptureMethod.WGC,
        "bitblt": _CaptureMethod.BITBLT,
    }
    return m.get(s.lower(), _CaptureMethod.AUTO)


def _clamp(v, lo, hi, default=0):
    """整数夹值，无效时返回 default。"""
    try:
        x = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _clampf(v, lo, hi, default=0.8):
    """浮点夹值。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def create_fastscreen_router(
    service: FastScreenServicePort,
    auth_validator: Optional[Callable] = None,
    session_store: "SessionStore" = None,
) -> APIRouter:
    """构造 FastScreen 流媒体路由器。

    Args:
        service: FastScreenServicePort 实例（用于获取 StreamManager 与可用性检查）
        auth_validator: 认证校验函数，接收 Request 返回 bool；None 时跳过认证
        session_store: SessionStore 实例，用于 WebSocket 认证；None 时跳过
    """
    router = APIRouter(prefix="/fastscreen", tags=["fastscreen"])

    # ── GET /fastscreen/mjpeg — MJPEG 流 ──
    @router.get("/mjpeg")
    async def fastscreen_mjpeg(
        request: Request,
        target_type: str = Query("monitor"),
        target_id: str = Query("0"),
        method: str = Query("auto"),
        fps: int = Query(15, ge=1, le=60),
        quality: float = Query(0.8, ge=0.1, le=1.0),
        width: int = Query(0, ge=0),
        height: int = Query(0, ge=0),
    ):
        """MJPEG 流（multipart/x-mixed-replace）。"""
        if auth_validator is not None and not auth_validator(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        if not service.is_available():
            return StreamingResponse(
                iter([b"--frame\r\nContent-Type: text/plain\r\n\r\nFastScreen unavailable\r\n"]),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )

        _load_streamer_modules()
        if _MjpegStreamer is None:
            return StreamingResponse(
                iter([b"--frame\r\nContent-Type: text/plain\r\n\r\nStreamer module load failed\r\n"]),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )

        tt = _parse_target_type(target_type)
        tid = int(target_id) if target_id.isdigit() else 0
        m = _parse_method(method)
        # window 模式：记录句柄用于主动 IsIconic 检查（替代旧的超时计数 stall 检测）
        is_window_target = (target_type.lower() == "window")
        window_hwnd = tid if is_window_target else 0

        streamer = _MjpegStreamer(
            target_type=tt, target_id=tid, method=m,
            fps=fps, quality=quality,
            scale_width=width, scale_height=height,
        )

        async def generate():
            ok = await streamer.start()
            if not ok:
                yield b"--frame\r\nContent-Type: text/plain\r\n\r\nCapture start failed\r\n"
                return
            # 窗口最小化状态跟踪：仅在状态变化时打日志，避免刷屏
            was_minimized = False
            # 调试：流启动参数 + 循环计数（排查 IsIconic 检查是否生效）
            loop_tick = 0
            _logger.info("[MJPEG] generate started, is_window_target=%s, window_hwnd=%d, _STALL_JPEG_BYTES_len=%d",
                         is_window_target, window_hwnd, len(_STALL_JPEG_BYTES or b''))
            try:
                while streamer.is_running:
                    loop_tick += 1
                    # window 模式：主动检查窗口最小化（IsIconic），不依赖帧超时判断
                    # 窗口最小化时 WGC 不产生帧，主动检查可立即得到准确状态
                    if is_window_target:
                        minimized_now = _is_window_minimized(window_hwnd)
                        # 每 ~5s（10 次 ×0.5s）记录一次窗口状态，避免刷屏
                        if loop_tick % 10 == 1:
                            _logger.info("[MJPEG] tick=%d hwnd=%d IsIconic=%s was_minimized=%s streamer_running=%s",
                                         loop_tick, window_hwnd, minimized_now, was_minimized, streamer.is_running)
                        if minimized_now:
                            if not was_minimized:
                                _logger.info("[MJPEG] window minimized (IsIconic=true), sending stall signal, hwnd=%d",
                                             window_hwnd)
                                was_minimized = True
                            # 发 1x1 纯红 JPEG 作为 stall 信号帧，前端通过 naturalWidth===1 + 红色像素校验识别
                            if _STALL_JPEG_BYTES:
                                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + _STALL_JPEG_BYTES + b"\r\n"
                            # 短暂 sleep 避免 busy loop（最小化期间无需高频发帧）
                            await asyncio.sleep(0.5)
                            continue
                        elif was_minimized:
                            _logger.info("[MJPEG] window restored from minimized, hwnd=%d", window_hwnd)
                            was_minimized = False
                    # 窗口未最小化（或 monitor 模式）：正常获取帧
                    # timeout 降低到 0.5s，让 IsIconic 检查更及时
                    jpeg_data = await streamer.get_frame(timeout=0.5)
                    if jpeg_data is None:
                        if not streamer.is_running:
                            break
                        # 无帧但窗口未最小化：可能是 C++ 正在初始化或性能抖动，继续循环
                        continue
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg_data + b"\r\n"
            except (ConnectionResetError, ConnectionError):
                pass
            finally:
                await streamer.stop()

        return StreamingResponse(
            generate(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={},
        )

    # ── WS /fastscreen/ws/mse — H264 MSE 流（fmp4 segment） ──
    @router.websocket("/ws/mse")
    async def fastscreen_ws_mse(ws: WebSocket):
        await ws.accept()
        remote = ws.client.host if ws.client else "-"

        # 端点级认证校验
        if session_store is not None:
            if not validate_ws_auth(ws, session_store):
                _logger.warning("FastScreen WS/MSE auth failed from %s", remote)
                try:
                    await ws.send_json({"type": "auth_required"})
                except Exception:
                    pass
                try:
                    await ws.close(code=4001, reason="Unauthorized")
                except Exception:
                    pass
                return

        _logger.info("FastScreen WS/MSE connect from %s", remote)

        if not service.is_available():
            await ws.send_text(json.dumps({"error": "FastScreen unavailable"}))
            await ws.close()
            return

        _load_streamer_modules()
        if _H264MSEStreamer is None:
            await ws.send_text(json.dumps({"error": "Streamer module load failed"}))
            await ws.close()
            return

        streamer = None
        try:
            # 等待客户端发送参数 JSON
            msg = await ws.receive_text()
            try:
                params = json.loads(msg)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "invalid json"}))
                return

            tt = _parse_target_type(params.get("target_type", "monitor"))
            tid = int(params.get("target_id", 0))
            m = _parse_method(params.get("method", "auto"))
            f = _clamp(params.get("fps", 30), 1, 60, 30)
            sw = _clamp(params.get("width", 0), 0, 7680, 0)
            sh = _clamp(params.get("height", 0), 0, 4320, 0)
            br = _clamp(params.get("bitrate", 2_000_000), 100_000, 50_000_000, 2_000_000)
            gop = _clamp(params.get("gop_size", 30), 1, 300, 30)
            q = _clampf(params.get("quality", 0.8), 0.1, 1.0, 0.8)

            # 窗口目标：记录句柄用于检测窗口关闭
            is_window_target = (params.get("target_type", "monitor").lower() == "window")
            window_hwnd = tid if is_window_target else 0

            streamer = _H264MSEStreamer(
                target_type=tt, target_id=tid, method=m,
                fps=f, scale_width=sw, scale_height=sh,
                bitrate=br, gop_size=gop, quality=q,
            )
            ok = await streamer.start()
            if not ok:
                await ws.send_text(json.dumps({"error": "failed to start capture"}))
                return

            await ws.send_text(json.dumps({
                "status": "streaming", "fps": f, "width": sw, "height": sh,
            }))

            loop = asyncio.get_event_loop()
            # 窗口最小化状态跟踪：仅在状态变化时发送 stall 消息，避免刷屏
            was_minimized = False
            # 发送循环耗时跟踪：定位 resize 后 10s 延迟根因
            t_loop_start = time.monotonic()
            t_last_seg = t_loop_start
            seg_count = 0
            # 调试：循环计数（排查 IsIconic 检查是否生效）
            loop_tick = 0
            _logger.info("[MSE-WS] send loop started, fps=%d, sw=%d, sh=%d, is_window_target=%s, window_hwnd=%d",
                         f, sw, sh, is_window_target, window_hwnd)
            while streamer.is_running:
                loop_tick += 1
                # window 模式：主动检查窗口状态（IsIconic + IsWindow），不依赖帧超时判断
                # 窗口最小化时 WGC 不产生帧，主动检查可立即得到准确状态
                if is_window_target:
                    # 优先检查窗口是否已关闭（句柄失效）
                    if not _is_window_valid(window_hwnd):
                        _logger.info("[MSE-WS] window closed (hwnd=%d invalid), sending closed notify",
                                     window_hwnd)
                        try:
                            await ws.send_text(json.dumps({"closed": True, "message": "窗口已关闭"}))
                        except Exception:
                            pass
                        break
                    # 检查窗口是否最小化
                    minimized_now = _is_window_minimized(window_hwnd)
                    # 每 ~5s（10 次 ×0.5s 或 50 次 ×0.1s）记录一次窗口状态
                    if loop_tick % 50 == 1:
                        _logger.info("[MSE-WS] tick=%d hwnd=%d IsIconic=%s was_minimized=%s streamer_running=%s",
                                     loop_tick, window_hwnd, minimized_now, was_minimized, streamer.is_running)
                    if minimized_now:
                        if not was_minimized:
                            _logger.info("[MSE-WS] window minimized (IsIconic=true), sending stall notify, hwnd=%d",
                                         window_hwnd)
                            was_minimized = True
                            try:
                                await ws.send_text(json.dumps({"stall": True, "message": "窗口可能已最小化，等待恢复…"}))
                            except Exception:
                                pass
                        # 短暂 sleep 避免 busy loop（最小化期间无需获取 segment）
                        await asyncio.sleep(0.5)
                        continue
                    elif was_minimized:
                        _logger.info("[MSE-WS] window restored from minimized, sending recovery notify, hwnd=%d",
                                     window_hwnd)
                        was_minimized = False
                        try:
                            await ws.send_text(json.dumps({"stall": False, "message": "画面已恢复"}))
                        except Exception:
                            pass
                # 窗口未最小化（或 monitor 模式）：正常获取 segment
                result = await loop.run_in_executor(None, streamer.get_segment, 0.1)
                if result is None:
                    if not streamer.is_running:
                        break
                    # 无 segment 但窗口未最小化：可能是编码器初始化或性能抖动，继续循环
                    continue
                seg_type, seg_data = result
                seg_count += 1
                now = time.monotonic()
                gap_ms = (now - t_last_seg) * 1000
                t_last_seg = now
                # 关键事件详细记录：init segment（resize 后首个 segment）、前3帧、间隔>1s
                if seg_type == "init":
                    _logger.info("[MSE-WS] seg#%d type=init %d bytes, gap=%.0fms, elapsed=%.1fs",
                                 seg_count, len(seg_data), gap_ms, now - t_loop_start)
                elif seg_count <= 3:
                    _logger.info("[MSE-WS] seg#%d type=%s %d bytes, gap=%.0fms",
                                 seg_count, seg_type, len(seg_data), gap_ms)
                elif gap_ms > 1000:
                    _logger.info("[MSE-WS] seg#%d type=%s slow gap=%.0fms", seg_count, seg_type, gap_ms)
                try:
                    if seg_type == "init":
                        await ws.send_bytes(b"\x00\x00\x00\x01init" + seg_data)
                    else:
                        await ws.send_bytes(b"\x00\x00\x00\x01segm" + seg_data)
                except (ConnectionResetError, ConnectionError):
                    break
        except Exception as e:
            _logger.exception("FastScreen WS/MSE error: %s", e)
        finally:
            if streamer:
                await streamer.stop()
            try:
                await ws.close()
            except Exception:
                pass
            _logger.info("FastScreen WS/MSE closed from %s", remote)

    # ── WS /fastscreen/ws/webcodecs — H264 WebCodecs 流（annexb NAL） ──
    @router.websocket("/ws/webcodecs")
    async def fastscreen_ws_webcodecs(ws: WebSocket):
        await ws.accept()
        remote = ws.client.host if ws.client else "-"

        # 端点级认证校验
        if session_store is not None:
            if not validate_ws_auth(ws, session_store):
                _logger.warning("FastScreen WS/WebCodecs auth failed from %s", remote)
                try:
                    await ws.send_json({"type": "auth_required"})
                except Exception:
                    pass
                try:
                    await ws.close(code=4001, reason="Unauthorized")
                except Exception:
                    pass
                return

        _logger.info("FastScreen WS/WebCodecs connect from %s", remote)

        if not service.is_available():
            await ws.send_text(json.dumps({"error": "FastScreen unavailable"}))
            await ws.close()
            return

        _load_streamer_modules()
        if _H264Streamer is None:
            await ws.send_text(json.dumps({"error": "Streamer module load failed"}))
            await ws.close()
            return

        streamer = None
        try:
            msg = await ws.receive_text()
            try:
                params = json.loads(msg)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "invalid json"}))
                return

            tt = _parse_target_type(params.get("target_type", "monitor"))
            tid = int(params.get("target_id", 0))
            m = _parse_method(params.get("method", "auto"))
            f = _clamp(params.get("fps", 30), 1, 60, 30)
            sw = _clamp(params.get("width", 0), 0, 7680, 0)
            sh = _clamp(params.get("height", 0), 0, 4320, 0)
            br = _clamp(params.get("bitrate", 2_000_000), 100_000, 50_000_000, 2_000_000)
            gop = _clamp(params.get("gop_size", 30), 1, 300, 30)
            q = _clampf(params.get("quality", 0.8), 0.1, 1.0, 0.8)

            # 窗口目标：记录句柄用于检测窗口关闭
            is_window_target = (params.get("target_type", "monitor").lower() == "window")
            window_hwnd = tid if is_window_target else 0

            streamer = _H264Streamer(
                target_type=tt, target_id=tid, method=m,
                fps=f, scale_width=sw, scale_height=sh,
                bitrate=br, gop_size=gop, quality=q,
            )
            ok = await streamer.start()
            if not ok:
                await ws.send_text(json.dumps({"error": "failed to start capture"}))
                return

            await ws.send_text(json.dumps({
                "status": "streaming", "fps": f, "width": sw, "height": sh,
            }))

            loop = asyncio.get_event_loop()
            # 窗口最小化状态跟踪：仅在状态变化时发送 stall 消息，避免刷屏
            was_minimized = False
            # 调试：循环计数（排查 IsIconic 检查是否生效）
            loop_tick = 0
            _logger.info("[WebCodecs-WS] send loop started, is_window_target=%s, window_hwnd=%d",
                         is_window_target, window_hwnd)
            while streamer.is_running:
                loop_tick += 1
                # window 模式：主动检查窗口状态（IsIconic + IsWindow），不依赖帧超时判断
                # 窗口最小化时 WGC 不产生帧，主动检查可立即得到准确状态
                if is_window_target:
                    # 优先检查窗口是否已关闭（句柄失效）
                    if not _is_window_valid(window_hwnd):
                        _logger.info("[WebCodecs-WS] window closed (hwnd=%d invalid), sending closed notify",
                                     window_hwnd)
                        try:
                            await ws.send_text(json.dumps({"closed": True, "message": "窗口已关闭"}))
                        except Exception:
                            pass
                        break
                    # 检查窗口是否最小化
                    minimized_now = _is_window_minimized(window_hwnd)
                    # 每 ~5s（50 次 ×0.1s）记录一次窗口状态
                    if loop_tick % 50 == 1:
                        _logger.info("[WebCodecs-WS] tick=%d hwnd=%d IsIconic=%s was_minimized=%s streamer_running=%s",
                                     loop_tick, window_hwnd, minimized_now, was_minimized, streamer.is_running)
                    if minimized_now:
                        if not was_minimized:
                            _logger.info("[WebCodecs-WS] window minimized (IsIconic=true), sending stall notify, hwnd=%d",
                                         window_hwnd)
                            was_minimized = True
                            try:
                                await ws.send_text(json.dumps({"stall": True, "message": "窗口可能已最小化，等待恢复…"}))
                            except Exception:
                                pass
                        # 短暂 sleep 避免 busy loop（最小化期间无需获取 NAL）
                        await asyncio.sleep(0.5)
                        continue
                    elif was_minimized:
                        _logger.info("[WebCodecs-WS] window restored from minimized, sending recovery notify, hwnd=%d",
                                     window_hwnd)
                        was_minimized = False
                        try:
                            await ws.send_text(json.dumps({"stall": False, "message": "画面已恢复"}))
                        except Exception:
                            pass
                # 窗口未最小化（或 monitor 模式）：正常获取 NAL
                nal = await loop.run_in_executor(None, streamer.get_nal, 0.1)
                if nal is None:
                    if not streamer.is_running:
                        break
                    # 无 NAL 但窗口未最小化：可能是编码器初始化或性能抖动，继续循环
                    continue
                try:
                    await ws.send_bytes(nal)
                    _logger.debug("[WebCodecs-WS] sent NAL size=%d", len(nal))
                except (ConnectionResetError, ConnectionError):
                    break
        except Exception as e:
            _logger.exception("FastScreen WS/WebCodecs error: %s", e)
        finally:
            if streamer:
                await streamer.stop()
            try:
                await ws.close()
            except Exception:
                pass
            _logger.info("FastScreen WS/WebCodecs closed from %s", remote)

    return router
