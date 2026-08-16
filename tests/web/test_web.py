import os
import sys

import pytest
import time
import struct

# fastscreen.dll 为 Windows 编译产物；缺失（非 Windows 或未构建）时模块级跳过
_FASTSCREEN_DLL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bin", "fastscreencore", "fastscreen.dll",
)
if sys.platform != "win32" or not os.path.isfile(_FASTSCREEN_DLL):
    pytest.skip("依赖 fastscreen.dll（Windows 编译产物），跳过", allow_module_level=True)

from fastscreencore import CaptureEngine
from src.screenshare.streamers.encoding.h264 import H264Encoder
from src.screenshare.streamers.encoding.mjpeg import frame_to_jpeg, frame_to_png, encode_bgra_to_jpeg
from src.screenshare.streamers.encoding.fmp4 import (
    FMP4Muxer, annex_b_to_avcc, extract_sps_pps,
    nals_to_avcc, is_keyframe_annexb,
)
from src.screenshare.streamers.manager import StreamManager, StreamKey, FrameData, SharedSession


class TestH264Encoder:
    def test_create_encoder(self):
        enc = H264Encoder(640, 480, fps=30, bitrate=1000000)
        assert enc.width == 640
        assert enc.height == 480
        enc.close()

    def test_encode_single_frame(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        assert frame is not None

        enc = H264Encoder(frame.width, frame.height, fps=30)
        bgra = frame.to_bytes()
        nals = enc.encode_bgra(bgra, frame.stride, frame.width, frame.height)

        assert len(nals) >= 1
        total = sum(len(n) for n in nals)
        assert total > 0

        frame.release()
        enc.close()

    def test_encode_multiple_frames(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        enc = H264Encoder(frame.width, frame.height, fps=30)
        bgra = frame.to_bytes()

        for i in range(5):
            nals = enc.encode_bgra(bgra, frame.stride, frame.width, frame.height)
            assert len(nals) >= 1

        frame.release()
        enc.close()

    def test_encode_with_scaling(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        enc = H264Encoder(640, 400, fps=30)
        bgra = frame.to_bytes()

        nals = enc.encode_bgra(bgra, frame.stride, frame.width, frame.height, 640, 400)
        assert len(nals) >= 1

        frame.release()
        enc.close()

    def test_encode_performance(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        enc = H264Encoder(frame.width, frame.height, fps=30)
        bgra = frame.to_bytes()

        times = []
        for i in range(10):
            start = time.perf_counter()
            enc.encode_bgra(bgra, frame.stride, frame.width, frame.height)
            times.append((time.perf_counter() - start) * 1000)

        avg = sum(times) / len(times)
        assert avg < 50, f"Encoding too slow: {avg:.1f}ms avg (need <50ms for 30fps)"

        frame.release()
        enc.close()


class TestFrameToJpeg:
    def test_frame_to_jpeg(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        data = frame_to_jpeg(frame, quality=0.8)
        assert len(data) > 0
        assert data[:2] == b'\xff\xd8'
        frame.release()

    def test_frame_to_jpeg_scaled(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        data = frame_to_jpeg(frame, quality=0.8, width=640, height=400)
        assert len(data) > 0
        assert data[:2] == b'\xff\xd8'
        frame.release()

    def test_frame_to_png(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        data = frame_to_png(frame)
        assert len(data) > 0
        assert data[:4] == b'\x89PNG'
        frame.release()


class TestToImageBytes:
    def test_to_image_bytes_jpeg(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        data = frame.to_image_bytes(format="jpeg", quality=0.8)
        assert len(data) > 0
        assert data[:2] == b'\xff\xd8'
        frame.release()

    def test_to_image_bytes_png(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        data = frame.to_image_bytes(format="png")
        assert len(data) > 0
        assert data[:4] == b'\x89PNG'
        frame.release()

    def test_to_image_bytes_scaled(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        full = frame.to_image_bytes(format="jpeg", quality=0.8)
        scaled = frame.to_image_bytes(format="jpeg", quality=0.8, width=640, height=400)
        assert len(scaled) > 0
        assert len(scaled) < len(full)
        frame.release()


class TestFMP4Muxer:
    def _get_h264_nals(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        assert frame is not None

        enc = H264Encoder(frame.width, frame.height, fps=30)
        bgra = frame.to_bytes()
        nals = enc.encode_bgra(bgra, frame.stride, frame.width, frame.height)

        frame.release()
        enc.close()
        return nals

    def test_annex_b_to_avcc(self):
        nals = self._get_h264_nals()
        assert len(nals) >= 1

        for nal in nals:
            avcc_nals = annex_b_to_avcc(nal)
            assert len(avcc_nals) >= 1
            for n in avcc_nals:
                assert len(n) > 0
                nal_type = n[0] & 0x1F
                assert 1 <= nal_type <= 8

    def test_extract_sps_pps(self):
        nals = self._get_h264_nals()
        first_nal = nals[0]

        result = extract_sps_pps(first_nal)
        assert result is not None
        sps, pps = result
        assert len(sps) > 0
        assert len(pps) > 0
        assert (sps[0] & 0x1F) == 7
        assert (pps[0] & 0x1F) == 8

    def test_nals_to_avcc(self):
        nals = self._get_h264_nals()
        first_nal = nals[0]

        avcc = nals_to_avcc(first_nal)
        assert len(avcc) > 0

        offset = 0
        while offset < len(avcc):
            nal_len = struct.unpack(">I", avcc[offset:offset + 4])[0]
            assert nal_len > 0
            assert offset + 4 + nal_len <= len(avcc)
            offset += 4 + nal_len

    def test_is_keyframe(self):
        nals = self._get_h264_nals()
        first_nal = nals[0]
        assert is_keyframe_annexb(first_nal) is True

    def test_create_init_segment(self):
        nals = self._get_h264_nals()
        first_nal = nals[0]

        muxer = FMP4Muxer(1920, 1080)
        init_seg = muxer.create_init_segment(first_nal)
        assert init_seg is not None
        assert len(init_seg) > 0

        assert init_seg[4:8] == b"ftyp"

        ftyp_size = struct.unpack(">I", init_seg[:4])[0]
        assert init_seg[ftyp_size + 4:ftyp_size + 8] == b"moov"

    def test_create_media_segment(self):
        nals = self._get_h264_nals()
        first_nal = nals[0]

        muxer = FMP4Muxer(1920, 1080)
        init_seg = muxer.create_init_segment(first_nal)
        assert init_seg is not None

        frames = [(first_nal, 33)]
        media_seg = muxer.create_media_segment(frames)
        assert len(media_seg) > 0

        assert media_seg[4:8] == b"moof"

    def test_muxer_sequence_numbers(self):
        nals = self._get_h264_nals()
        first_nal = nals[0]

        muxer = FMP4Muxer(1920, 1080)
        muxer.create_init_segment(first_nal)

        seg1 = muxer.create_media_segment([(first_nal, 33)])
        seg2 = muxer.create_media_segment([(first_nal, 33)])

        assert seg1 != seg2


class TestStreamManager:
    def test_stream_key_equality(self):
        k1 = StreamKey(0, 0, 0, 30)
        k2 = StreamKey(0, 0, 0, 30)
        k3 = StreamKey(0, 0, 0, 15)
        assert k1 == k2
        assert k1 != k3
        assert hash(k1) == hash(k2)
        assert hash(k1) != hash(k3)

    def test_stream_key_as_dict_key(self):
        d = {}
        k1 = StreamKey(0, 0, 0, 30)
        k2 = StreamKey(0, 0, 0, 30)
        d[k1] = "test"
        assert d[k2] == "test"

    def test_frame_data_immutable(self):
        fd = FrameData(data=b"\x00\x01\x02", width=1, height=1, stride=4)
        assert fd.data == b"\x00\x01\x02"
        assert fd.width == 1
        assert fd.height == 1
        assert fd.stride == 4

    def test_shared_session_subscribe_unsubscribe(self):
        key = StreamKey(99, 99, 99, 30)
        session = SharedSession(key)
        calls = []

        def cb(fd):
            calls.append(fd)

        session.subscribe(cb)
        assert session.subscriber_count == 1

        session.unsubscribe(cb)
        assert session.subscriber_count == 0

    def test_shared_session_multiple_subscribers(self):
        key = StreamKey(99, 99, 99, 30)
        session = SharedSession(key)
        calls_a = []
        calls_b = []

        session.subscribe(lambda fd: calls_a.append(fd))
        session.subscribe(lambda fd: calls_b.append(fd))
        assert session.subscriber_count == 2

        fd = FrameData(data=b"\x00", width=1, height=1, stride=4)
        for cb in list(session._subscribers):
            cb(fd)

        assert len(calls_a) == 1
        assert len(calls_b) == 1
        assert calls_a[0] is calls_b[0]

    def test_stream_manager_subscribe_unsubscribe(self):
        mgr = StreamManager()
        key = StreamKey(99, 99, 99, 30)
        calls = []
        cb = lambda fd: calls.append(fd)

        session = mgr.subscribe(key, cb)
        assert session is not None
        assert mgr.session_count == 1

        mgr.unsubscribe(key, cb)
        assert mgr.session_count == 0

    def test_stream_manager_same_key_shares_session(self):
        mgr = StreamManager()
        key = StreamKey(0, 0, 0, 30)

        s1 = mgr.subscribe(key, lambda fd: None)
        s2 = mgr.subscribe(key, lambda fd: None)
        assert s1 is s2
        assert mgr.session_count == 1
        assert s1.subscriber_count == 2

    def test_stream_manager_different_keys_separate_sessions(self):
        mgr = StreamManager()
        k1 = StreamKey(0, 0, 0, 30)
        k2 = StreamKey(0, 0, 0, 15)

        s1 = mgr.subscribe(k1, lambda fd: None)
        s2 = mgr.subscribe(k2, lambda fd: None)
        assert s1 is not s2
        assert mgr.session_count == 2


class TestEncodeBgraToJpeg:
    def test_encode_bgra_to_jpeg(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        bgra = frame.to_bytes()
        data = encode_bgra_to_jpeg(bgra, frame.width, frame.height, frame.stride, quality=0.8)
        assert len(data) > 0
        assert data[:2] == b'\xff\xd8'
        frame.release()

    def test_encode_bgra_to_jpeg_scaled(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        if not monitors:
            pytest.skip("No monitors available")

        frame = engine.capture_monitor(monitors[0].id)
        bgra = frame.to_bytes()
        data = encode_bgra_to_jpeg(bgra, frame.width, frame.height, frame.stride, quality=0.8, scale_width=640, scale_height=400)
        assert len(data) > 0
        assert data[:2] == b'\xff\xd8'
        frame.release()
