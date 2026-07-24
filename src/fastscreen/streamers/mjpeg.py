import asyncio
import queue
import threading
from typing import Optional

from fastscreencore import CaptureMethod
from .encoding.mjpeg import encode_bgra_to_jpeg
from .h264 import _drain_queue
from .manager import StreamManager, StreamKey, FrameData


class MjpegStreamer:
    def __init__(
        self,
        target_type: int,
        target_id: int,
        method: int = CaptureMethod.AUTO,
        fps: int = 15,
        quality: float = 0.8,
        scale_width: int = 0,
        scale_height: int = 0,
    ):
        self.target_type = target_type
        self.target_id = target_id
        self.method = method
        self.fps = fps
        self.quality = quality
        self.scale_width = scale_width
        self.scale_height = scale_height
        self._session = None
        self._running = False
        self._raw_queue: queue.Queue = queue.Queue(maxsize=2)
        self._encoded_queue: queue.Queue = queue.Queue(maxsize=2)
        self._encode_thread: Optional[threading.Thread] = None
        self._key = StreamKey(target_type, target_id, method, fps)
        self._manager = StreamManager.get()

    def _on_frame(self, frame_data: FrameData):
        if not self._running:
            return
        while self._raw_queue.full():
            try:
                self._raw_queue.get_nowait()
            except queue.Empty:
                break
        self._raw_queue.put(frame_data)

    def _encode_loop(self):
        while self._running:
            try:
                frame_data = self._raw_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame_data is None:
                break
            try:
                jpeg_data = encode_bgra_to_jpeg(
                    frame_data.data, frame_data.width, frame_data.height, frame_data.stride,
                    self.quality, self.scale_width, self.scale_height,
                )
                if jpeg_data:
                    while self._encoded_queue.full():
                        try:
                            self._encoded_queue.get_nowait()
                        except queue.Empty:
                            break
                    self._encoded_queue.put(jpeg_data)
            except Exception:
                pass

    async def start(self) -> bool:
        self._running = True

        self._encode_thread = threading.Thread(target=self._encode_loop, daemon=True)
        self._encode_thread.start()

        self._session = self._manager.subscribe(self._key, self._on_frame)
        if not self._session.is_running:
            self._running = False
            _drain_queue(self._raw_queue)
            try:
                self._raw_queue.put_nowait(None)
            except queue.Full:
                pass
            self._manager.unsubscribe(self._key, self._on_frame)
            self._session = None
            return False

        return True

    async def stop(self):
        self._running = False
        if self._session:
            self._manager.unsubscribe(self._key, self._on_frame)
            self._session = None
        # 清空 raw 队列后用 put_nowait 通知 _encode_loop 立即退出
        # 避免 put(None) 在队列满时永久阻塞事件循环线程
        _drain_queue(self._raw_queue)
        try:
            self._raw_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._encode_thread:
            self._encode_thread.join(timeout=3.0)
            self._encode_thread = None

    async def get_frame(self, timeout: float = 2.0) -> Optional[bytes]:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._encoded_queue.get(timeout=timeout)
            )
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._session is not None and self._session.is_running
