import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from fastscreencore import CaptureEngine, CapturedFrame

logger = logging.getLogger("fastscreen.manager")


@dataclass
class FrameData:
    data: bytes
    width: int
    height: int
    stride: int


class StreamKey:
    __slots__ = ('target_type', 'target_id', 'method', 'fps')

    def __init__(self, target_type: int, target_id: int, method: int, fps: int):
        self.target_type = target_type
        self.target_id = target_id
        self.method = method
        self.fps = fps

    def __eq__(self, other):
        if not isinstance(other, StreamKey):
            return NotImplemented
        return (self.target_type == other.target_type and
                self.target_id == other.target_id and
                self.method == other.method and
                self.fps == other.fps)

    def __hash__(self):
        return hash((self.target_type, self.target_id, self.method, self.fps))

    def __repr__(self):
        return f"StreamKey(t={self.target_type},id={self.target_id},m={self.method},fps={self.fps})"


class SharedSession:
    def __init__(self, key: StreamKey):
        self.key = key
        self._engine = CaptureEngine()
        self._subscribers: List[Callable[[FrameData], None]] = []
        self._lock = threading.Lock()
        self._running = False

    def subscribe(self, callback: Callable[[FrameData], None]):
        with self._lock:
            self._subscribers.append(callback)
        logger.info("Subscriber added to %s, total: %d", self.key, len(self._subscribers))

    def unsubscribe(self, callback: Callable[[FrameData], None]):
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass
        logger.info("Subscriber removed from %s, total: %d", self.key, len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True

        # 调试状态：跟踪帧间隔与尺寸变化（排查 resize 时帧停滞问题）
        state = {'last_ts': 0.0, 'last_w': 0, 'last_h': 0, 'count': 0, 'stall_logged': False}

        def on_frame(frame: CapturedFrame):
            if not self._running:
                frame.release()
                return

            now = time.monotonic()
            w, h = frame.width, frame.height
            gap_ms = (now - state['last_ts']) * 1000 if state['last_ts'] > 0 else 0
            state['count'] += 1
            state['stall_logged'] = False

            # 尺寸变化或前 3 帧或间隔过大时打日志
            if (state['last_w'] != w or state['last_h'] != h):
                logger.info("[CAPTURE] %s frame#%d size %dx%d -> %dx%d (gap=%.0fms)",
                            self.key, state['count'], state['last_w'], state['last_h'], w, h, gap_ms)
            elif state['count'] <= 3:
                logger.info("[CAPTURE] %s frame#%d size=%dx%d (gap=%.0fms)",
                            self.key, state['count'], w, h, gap_ms)
            elif gap_ms > 1000:
                logger.info("[CAPTURE] %s frame#%d slow gap=%.0fms size=%dx%d",
                            self.key, state['count'], gap_ms, w, h)

            state['last_ts'] = now
            state['last_w'] = w
            state['last_h'] = h

            frame_data = FrameData(
                data=frame.to_bytes_gil_safe(),
                width=frame.width,
                height=frame.height,
                stride=frame.stride,
            )
            frame.release()

            with self._lock:
                subs = list(self._subscribers)

            if not subs:
                return

            for callback in subs:
                try:
                    callback(frame_data)
                except Exception as e:
                    logger.error("Subscriber callback error: %s", e)

        self._running = True
        success = self._engine.start_continuous(
            self.key.target_type,
            self.key.target_id,
            on_frame,
            fps=self.key.fps,
            method=self.key.method,
        )

        if not success:
            self._running = False
            logger.error("Failed to start capture session: %s", self.key)

        return success

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._engine.stop_continuous()
        logger.info("Session stopped: %s", self.key)


class StreamManager:
    _instance: Optional['StreamManager'] = None

    @classmethod
    def get(cls) -> 'StreamManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._sessions: Dict[StreamKey, SharedSession] = {}
        self._lock = threading.Lock()

    def subscribe(self, key: StreamKey, callback: Callable[[FrameData], None]) -> SharedSession:
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = SharedSession(key)
                self._sessions[key] = session
                logger.info("New session created: %s", key)

            session.subscribe(callback)

        if not session.is_running:
            if not session.start():
                logger.error("Failed to start session: %s", key)

        return session

    def unsubscribe(self, key: StreamKey, callback: Callable[[FrameData], None]):
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return

            session.unsubscribe(callback)

            if session.subscriber_count == 0:
                session.stop()
                del self._sessions[key]
                logger.info("Session removed (no subscribers): %s", key)

    def get_session(self, key: StreamKey) -> Optional[SharedSession]:
        with self._lock:
            return self._sessions.get(key)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)
