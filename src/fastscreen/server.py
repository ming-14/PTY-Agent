"""FastScreen 流媒体测试服务器（aiohttp）。

独立于 daemon 的流端点服务器，供 tests/web/ 下的 e2e 测试
（test_mse_ws / test_mse_detailed / test_h264_ws）与手动调试使用。
生产入口为 web 层（web/presentation/controllers/fastscreen_controller.py）。
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 将 bin/ 加入 sys.path 以导入 fastscreencore 包
_bin_dir = str(Path(__file__).resolve().parent.parent.parent / "bin")
if _bin_dir not in sys.path:
    sys.path.insert(0, _bin_dir)

from aiohttp import web, WSMsgType

from fastscreencore import CaptureEngine, TargetType, CaptureMethod
from .streamers.mjpeg import MjpegStreamer
from .streamers.encoding.mjpeg import frame_to_jpeg, frame_to_png
from .streamers.h264 import H264Streamer
from .streamers.h264_mse import H264MSEStreamer

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
for _name in ("fastscreen.fmp4", "fastscreen.h264_mse"):
    logging.getLogger(_name).setLevel(logging.INFO)

_METHOD_MAP = {
    "auto": CaptureMethod.AUTO,
    "dxgi": CaptureMethod.DXGI,
    "wgc": CaptureMethod.WGC,
    "bitblt": CaptureMethod.BITBLT,
}

_TARGET_MAP = {
    "monitor": TargetType.MONITOR,
    "window": TargetType.WINDOW,
}

_engine = CaptureEngine()


async def api_monitors(request: web.Request) -> web.Response:
    monitors = _engine.enumerate_monitors()
    data = []
    for m in monitors:
        data.append({
            "id": m.id,
            "name": m.name,
            "left": m.left,
            "top": m.top,
            "width": m.width,
            "height": m.height,
            "primary": bool(m.primary),
        })
    return web.json_response(data)


async def api_windows(request: web.Request) -> web.Response:
    windows = _engine.enumerate_windows()
    data = []
    for w in windows:
        data.append({
            "hwnd": int(w.hwnd) if w.hwnd else 0,
            "title": w.title,
            "class_name": w.class_name,
            "left": w.left,
            "top": w.top,
            "width": w.width,
            "height": w.height,
            "visible": bool(w.visible),
        })
    return web.json_response(data)


async def api_capture(request: web.Request) -> web.Response:
    target_type_str = request.query.get("target_type", "monitor")
    target_id_str = request.query.get("target_id", "0")
    method_str = request.query.get("method", "auto")
    fmt = request.query.get("format", "jpeg")
    width_str = request.query.get("width", "0")
    height_str = request.query.get("height", "0")

    target_type = _TARGET_MAP.get(target_type_str, TargetType.MONITOR)
    target_id = int(target_id_str)
    method = _METHOD_MAP.get(method_str, CaptureMethod.AUTO)
    scale_w = int(width_str)
    scale_h = int(height_str)

    if target_type == TargetType.MONITOR:
        frame = _engine.capture_monitor(target_id, method)
    else:
        frame = _engine.capture_window(target_id, method)

    if frame is None:
        return web.json_response(
            {"error": "capture failed"},
            status=500,
        )

    try:
        if fmt == "png":
            data = frame_to_png(frame, width=scale_w, height=scale_h)
            content_type = "image/png"
        else:
            data = frame_to_jpeg(frame, quality=0.85, width=scale_w, height=scale_h)
            content_type = "image/jpeg"
    finally:
        frame.release()

    return web.Response(body=data, content_type=content_type)


async def api_stream_mjpeg(request: web.Request) -> web.StreamResponse:
    target_type_str = request.query.get("target_type", "monitor")
    target_id_str = request.query.get("target_id", "0")
    method_str = request.query.get("method", "auto")
    fps_str = request.query.get("fps", "15")
    quality_str = request.query.get("quality", "0.8")
    width_str = request.query.get("width", "0")
    height_str = request.query.get("height", "0")

    target_type = _TARGET_MAP.get(target_type_str, TargetType.MONITOR)
    target_id = int(target_id_str)
    method = _METHOD_MAP.get(method_str, CaptureMethod.AUTO)
    fps = max(1, min(60, int(fps_str)))
    quality = max(0.1, min(1.0, float(quality_str)))
    scale_w = int(width_str)
    scale_h = int(height_str)

    streamer = MjpegStreamer(target_type, target_id, method, fps, quality, scale_w, scale_h)
    ok = await streamer.start()
    if not ok:
        return web.json_response(
            {"error": "failed to start capture"},
            status=500,
        )

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
        },
    )
    await response.prepare(request)

    try:
        while streamer.is_running:
            jpeg_data = await streamer.get_frame(timeout=3.0)
            if jpeg_data is None:
                if not streamer.is_running:
                    break
                continue
            boundary = b"--frame\r\n"
            header = b"Content-Type: image/jpeg\r\n\r\n"
            await response.write(boundary + header + jpeg_data + b"\r\n")
    except (ConnectionResetError, ConnectionError):
        pass
    finally:
        await streamer.stop()

    return response


async def ws_stream_h264(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    msg = None
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                params = json.loads(msg.data)
            except json.JSONDecodeError:
                await ws.send_json({"error": "invalid json"})
                continue

            target_type_str = params.get("target_type", "monitor")
            target_id_str = params.get("target_id", "0")
            method_str = params.get("method", "auto")
            fps = max(1, min(60, int(params.get("fps", 30))))
            scale_w = int(params.get("width", 0))
            scale_h = int(params.get("height", 0))
            bitrate = int(params.get("bitrate", 2_000_000))
            gop_size = int(params.get("gop_size", 30))
            quality = max(0.1, min(1.0, float(params.get("quality", 0.8))))

            target_type = _TARGET_MAP.get(target_type_str, TargetType.MONITOR)
            target_id = int(target_id_str)
            method = _METHOD_MAP.get(method_str, CaptureMethod.AUTO)

            streamer = H264Streamer(
                target_type, target_id, method, fps,
                scale_w, scale_h, bitrate, gop_size, quality,
            )
            ok = await streamer.start()
            if not ok:
                await ws.send_json({"error": "failed to start capture"})
                continue

            await ws.send_json({"status": "streaming", "fps": fps, "width": scale_w, "height": scale_h})

            try:
                while streamer.is_running and not ws.closed:
                    nal = await asyncio.get_event_loop().run_in_executor(
                        None, streamer.get_nal, 0.1
                    )
                    if nal is None:
                        continue
                    try:
                        await ws.send_bytes(nal)
                    except (ConnectionResetError, ConnectionError):
                        break
            finally:
                await streamer.stop()
            break

    return ws


async def ws_stream_h264_mse(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    msg = None
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                params = json.loads(msg.data)
            except json.JSONDecodeError:
                await ws.send_json({"error": "invalid json"})
                continue

            target_type_str = params.get("target_type", "monitor")
            target_id_str = params.get("target_id", "0")
            method_str = params.get("method", "auto")
            fps = max(1, min(60, int(params.get("fps", 30))))
            scale_w = int(params.get("width", 0))
            scale_h = int(params.get("height", 0))
            bitrate = int(params.get("bitrate", 2_000_000))
            gop_size = int(params.get("gop_size", 30))
            quality = max(0.1, min(1.0, float(params.get("quality", 0.8))))

            target_type = _TARGET_MAP.get(target_type_str, TargetType.MONITOR)
            target_id = int(target_id_str)
            method = _METHOD_MAP.get(method_str, CaptureMethod.AUTO)

            streamer = H264MSEStreamer(
                target_type, target_id, method, fps,
                scale_w, scale_h, bitrate, gop_size, quality,
            )
            ok = await streamer.start()
            if not ok:
                await ws.send_json({"error": "failed to start capture"})
                continue

            await ws.send_json({"status": "streaming", "fps": fps, "width": scale_w, "height": scale_h})

            try:
                while streamer.is_running and not ws.closed:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, streamer.get_segment, 0.1
                    )
                    if result is None:
                        continue
                    seg_type, seg_data = result
                    try:
                        if seg_type == "init":
                            await ws.send_bytes(b"\x00\x00\x00\x01init" + seg_data)
                        else:
                            await ws.send_bytes(b"\x00\x00\x00\x01segm" + seg_data)
                    except (ConnectionResetError, ConnectionError):
                        break
            finally:
                await streamer.stop()
            break

    return ws


def create_app() -> web.Application:
    app = web.Application()

    app.router.add_get("/api/monitors", api_monitors)
    app.router.add_get("/api/windows", api_windows)
    app.router.add_get("/api/capture", api_capture)
    app.router.add_get("/api/stream", api_stream_mjpeg)
    app.router.add_get("/ws/stream", ws_stream_h264)
    app.router.add_get("/ws/stream_mse", ws_stream_h264_mse)

    return app


def main():
    parser = argparse.ArgumentParser(description="FastScreen Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    args = parser.parse_args()

    app = create_app()
    web.run_app(app, host=args.host, port=args.port, print=lambda msg: print(msg))


if __name__ == "__main__":
    main()
