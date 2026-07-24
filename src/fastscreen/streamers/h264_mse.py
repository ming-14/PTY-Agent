import logging
import queue
import threading
import time
from typing import Optional

from fastscreencore import CaptureMethod
from .encoding.h264 import H264Encoder
from .encoding.fmp4 import FMP4Muxer, is_keyframe_annexb, annex_b_to_avcc
from .h264 import H264Streamer, _drain_queue
from .manager import StreamManager, StreamKey, FrameData

logger = logging.getLogger("fastscreen.h264_mse")


class H264MSEStreamer:
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
        self._segment_queue: queue.Queue = queue.Queue(maxsize=30)
        self._encode_thread: Optional[threading.Thread] = None
        self._encoder: Optional[H264Encoder] = None
        self._muxer: Optional[FMP4Muxer] = None
        self._init_segment: Optional[bytes] = None
        self._pending_nals: list[tuple[bytes, int]] = []
        self._frame_count = 0
        self._frames_per_segment = max(1, self.fps // 10)
        self._key = StreamKey(target_type, target_id, method, fps)
        self._manager = StreamManager.get()

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
                    t_resize = time.monotonic()
                    logger.info("[MSE-RESIZE] detected %dx%d -> %dx%d, rebuilding encoder",
                                enc_w, enc_h, w, h)
                    if self._encoder:
                        self._encoder.close()
                    t_enc_start = time.monotonic()
                    self._encoder = H264Encoder(w, h, self.fps, self.bitrate, self.gop_size, H264Streamer.quality_to_crf(self.quality))
                    t_enc_end = time.monotonic()
                    logger.info("[MSE-RESIZE] H264Encoder created in %.0fms", (t_enc_end - t_enc_start) * 1000)
                    # 保留 PTS 时间线：resize 时只更新 muxer 尺寸，不重建 muxer
                    # 这样 _base_media_decode_time 和 _sequence_number 保持连续，
                    # 新 media segment 的 PTS 从旧位置继续，前端无需重置 video.currentTime
                    if self._muxer is None:
                        self._muxer = FMP4Muxer(w, h, timescale=1000)
                    else:
                        self._muxer.width = w
                        self._muxer.height = h
                        self._muxer._init_segment = None  # 强制用新 SPS/PPS 重新生成 init segment
                    self._init_segment = None
                    self._pending_nals = []
                    # 清空旧 segment 队列（旧 SPS/PPS 的 segment 无法用新解码器配置播放）
                    while not self._segment_queue.empty():
                        try:
                            self._segment_queue.get_nowait()
                        except queue.Empty:
                            break
                    # 清空 raw 帧队列（避免旧尺寸帧延迟新编码）
                    while not self._raw_queue.empty():
                        try:
                            self._raw_queue.get_nowait()
                        except queue.Empty:
                            break
                    enc_w, enc_h = w, h
                    first_frame = False
                    logger.info("[MSE-RESIZE] encoder rebuilt, waiting for keyframe frame_data=%dx%d",
                                frame_data.width, frame_data.height)

                t_encode_start = time.monotonic()
                nals = self._encoder.encode_bgra(frame_data.data, frame_data.stride, frame_data.width, frame_data.height, w, h)
                t_encode_end = time.monotonic()

                for nal in nals:
                    is_key = is_keyframe_annexb(nal)
                    nal_types = [n[0] & 0x1F for n in annex_b_to_avcc(nal)]

                    if self._init_segment is None and is_key:
                        t_init_start = time.monotonic()
                        self._init_segment = self._muxer.create_init_segment(nal)
                        t_init_end = time.monotonic()
                        if self._init_segment:
                            logger.info("[MSE-RESIZE] init segment created: %d bytes, encode=%.0fms, init=%.0fms, total_since_resize=%.0fms, nal_types=%s",
                                        len(self._init_segment),
                                        (t_encode_end - t_encode_start) * 1000,
                                        (t_init_end - t_init_start) * 1000,
                                        (t_init_end - t_resize) * 1000,
                                        nal_types)
                            while self._segment_queue.full():
                                try:
                                    self._segment_queue.get_nowait()
                                except queue.Empty:
                                    break
                            self._segment_queue.put(("init", self._init_segment))
                    elif not is_key and self._init_segment is None:
                        logger.info("[MSE-RESIZE] non-keyframe nal_types=%s, still waiting for keyframe", nal_types)

                    duration_ms = int(1000 / self.fps)
                    self._pending_nals.append((nal, duration_ms))

                    if len(self._pending_nals) >= self._frames_per_segment:
                        segment = self._muxer.create_media_segment(self._pending_nals)
                        logger.debug("Media segment: %d frames, %d bytes", len(self._pending_nals), len(segment))
                        while self._segment_queue.full():
                            try:
                                self._segment_queue.get_nowait()
                            except queue.Empty:
                                break
                        self._segment_queue.put(("media", segment))
                        self._pending_nals = []

            except Exception as e:
                logger.error("encode_loop error: %s", e, exc_info=True)
                # 清空 pending_nals，避免异常后旧数据累积导致 create_media_segment 持续失败
                self._pending_nals = []

        if self._pending_nals and self._muxer:
            try:
                segment = self._muxer.create_media_segment(self._pending_nals)
                self._segment_queue.put(("media", segment))
            except Exception as e:
                logger.error("flush pending nals error: %s", e)

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
            pass
        if self._encode_thread:
            self._encode_thread.join(timeout=3.0)
            self._encode_thread = None
        if self._encoder:
            self._encoder.close()
            self._encoder = None

    def get_segment(self, timeout: float = 0.1) -> Optional[tuple[str, bytes]]:
        try:
            return self._segment_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._session is not None and self._session.is_running
