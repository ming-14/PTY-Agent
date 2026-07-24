import logging
import queue
import threading
from typing import Optional

from fastscreencore import CaptureMethod
from .manager import StreamManager, StreamKey, FrameData
from .encoding.h264 import H264Encoder

logger = logging.getLogger("fastscreen.h264_webcodecs")


def _drain_queue(q: queue.Queue) -> None:
    """非阻塞清空队列所有元素。用于 stop() 中 put 前清空，避免队列满时 put 阻塞。"""
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


class H264Streamer:
    def __init__(
        self,
        target_type: int,
        target_id: int,
        method: int = CaptureMethod.AUTO,
        fps: int = 30,
        scale_width: int = 0,
        scale_height: int = 0,
        bitrate: int = 2_000_000,
        gop_size: int = 30,
        quality: float = 0.8,
    ):
        self.target_type = target_type
        self.target_id = target_id
        self.method = method
        self.fps = fps
        self.scale_width = scale_width
        self.scale_height = scale_height
        self.bitrate = bitrate
        self.gop_size = gop_size
        self.quality = quality
        self._session = None
        self._running = False
        self._raw_queue: queue.Queue = queue.Queue(maxsize=3)
        self._nal_queue: queue.Queue = queue.Queue(maxsize=30)
        self._encode_thread: Optional[threading.Thread] = None
        self._encoder: Optional[H264Encoder] = None
        self._frame_count = 0
        self._key = StreamKey(target_type, target_id, method, fps)
        self._manager = StreamManager.get()

    @staticmethod
    def quality_to_crf(quality: float) -> int:
        return max(15, min(40, int(51 - quality * 36)))

    def _on_frame(self, frame_data: FrameData):
        if not self._running:
            return
        self._frame_count += 1
        while self._raw_queue.full():
            try:
                self._raw_queue.get_nowait()
            except queue.Empty:
                break
        self._raw_queue.put(frame_data)

    def _encode_loop(self):
        first_frame = True
        enc_w, enc_h = 0, 0  # 跟踪编码器当前配置的尺寸（检测窗口 resize）
        while self._running:
            try:
                frame_data = self._raw_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame_data is None:
                break
            try:
                # 对齐到偶数：libx264 yuv420p 要求 width/height 为偶数
                w = (self.scale_width if self.scale_width > 0 else frame_data.width) & ~1
                h = (self.scale_height if self.scale_height > 0 else frame_data.height) & ~1

                # 检测尺寸变化（首帧或窗口 resize），重新配置编码器
                if first_frame or self._encoder is None or enc_w != w or enc_h != h:
                    if self._encoder:
                        self._encoder.close()
                    # WebCodecs 路径禁用 NAL 类型重写：
                    # 重写 type 1→5 会导致 slice header 不匹配（IDR 需要 idr_pic_id），
                    # WebCodecs VideoDecoder 严格校验会解码失败。
                    # 前端通过 EncodedVideoChunk.type='key' 标记首帧为关键帧。
                    self._encoder = H264Encoder(w, h, self.fps, self.bitrate, self.gop_size, self.quality_to_crf(self.quality), rewrite_to_idr=False)
                    # 清空旧 raw 帧队列，避免旧尺寸帧被新编码器处理
                    while not self._raw_queue.empty():
                        try:
                            self._raw_queue.get_nowait()
                        except queue.Empty:
                            break
                    enc_w, enc_h = w, h
                    first_frame = False
                    logger.info("[WebCodecs-ENC] encoder created %dx%d, rewrite_to_idr=False", w, h)

                nals = self._encoder.encode_bgra(frame_data.data, frame_data.stride, frame_data.width, frame_data.height, w, h)

                if nals:
                    nal_types = []
                    for nal in nals:
                        while self._nal_queue.full():
                            try:
                                self._nal_queue.get_nowait()
                            except queue.Empty:
                                break
                        self._nal_queue.put(nal)
                        # 解析 NAL 类型用于诊断（支持 3-byte 和 4-byte 起始码）
                        nal_types.extend(H264Encoder._parse_nal_types(nal))
                    logger.debug("[WebCodecs-ENC] produced %d NALs, types=%s, queue_size=%d",
                                 len(nals), nal_types, self._nal_queue.qsize())
            except Exception as e:
                logger.error("[WebCodecs-ENC] encode_loop error: %s", e, exc_info=True)

    async def start(self) -> bool:
        self._running = True
        self._frame_count = 0

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
            pass  # 已清空，不应 Full；即使 Full，_running=False 已通知退出
        if self._encode_thread:
            self._encode_thread.join(timeout=3.0)
            self._encode_thread = None
        if self._encoder:
            self._encoder.close()
            self._encoder = None

    def get_nal(self, timeout: float = 0.1) -> Optional[bytes]:
        try:
            return self._nal_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._session is not None and self._session.is_running
