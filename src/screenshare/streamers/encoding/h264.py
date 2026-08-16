from __future__ import annotations

from ....logging import get_logger
import logging
from fractions import Fraction

import av
import numpy as np

_logger = get_logger("pty-screenshareservice-encoder")


class H264Encoder:
    """H264 编码器（基于 PyAV / libx264）。

    背景：PyAV 无法通过 options 传递 forced-idr=1（被静默丢弃），
    pict_type=I 也只能产生 non-IDR I 帧（NAL type 1），而非 IDR（NAL type 5）。

    两种 keyframe 处理策略（由 rewrite_to_idr 参数选择）：

    1. rewrite_to_idr=True（默认，供 MSE 使用）：
       将 forced keyframe 的 NAL type 1 重写为 type 5（IDR）。
       注意：这只改 NAL header，不改 slice header。IDR slice header 需要
       idr_pic_id 字段，而 non-IDR slice 没有此字段。MSE 的浏览器内部解码器
       能容忍此不匹配，但 WebCodecs VideoDecoder 会严格校验导致解码失败。

    2. rewrite_to_idr=False（供 WebCodecs 使用）：
       不重写 NAL type，保留正确的 non-IDR slice header。
       前端通过 EncodedVideoChunk.type='key' 告知 WebCodecs 这是关键帧
       （WebCodecs 的 key frame 检查基于 type 字段，不是 NAL type）。
       解码器按 NAL type 1 解析 slice header（语法正确），
       I-frame 内容（全 intra 宏块）可独立解码，无需参考帧。
    """

    def __init__(
        self,
        width: int,
        height: int,
        fps: int = 30,
        bitrate: int = 2_000_000,
        gop_size: int = 30,
        crf: int = 23,
        rewrite_to_idr: bool = True,
    ):
        # 对齐到偶数：libx264 的 yuv420p 要求 width/height 为偶数（色度子采样）
        self.width = width & ~1
        self.height = height & ~1
        self.fps = fps
        self.bitrate = bitrate
        self.gop_size = gop_size
        self.crf = crf
        self.rewrite_to_idr = rewrite_to_idr
        self._codec = av.CodecContext.create("h264", "w")
        self._codec.width = self.width
        self._codec.height = self.height
        self._codec.framerate = Fraction(fps, 1)
        self._codec.pix_fmt = "yuv420p"
        self._codec.options = {
            "preset": "ultrafast",
            "tune": "zerolatency",
            "crf": str(crf),
            "threads": "1",
            "slices": "1",
            # open-gop=0：关闭 open GOP，确保 GOP 边界的关键帧是 IDR
            "open-gop": "0",
        }
        self._codec.gop_size = gop_size
        self._codec.open()
        # 首帧强制 keyframe：确保编码器重建后立即生成 I-frame + SPS/PPS
        self._force_keyframe = True
        # 延迟重写标志：x264 输出延迟一帧，slice 在下次 encode 时才输出
        # 仅 rewrite_to_idr=True 时使用
        self._pending_kf_rewrite = False

    def encode_bgra(
        self,
        bgra_data: bytes,
        stride: int,
        width: int,
        height: int,
        target_width: int = 0,
        target_height: int = 0,
    ) -> list[bytes]:
        arr = np.frombuffer(bgra_data, dtype=np.uint8).reshape((height, stride))
        arr = arr[:, : width * 4].reshape((height, width, 4))
        bgra = np.ascontiguousarray(arr)

        frame = av.VideoFrame.from_ndarray(bgra, format="bgra")

        # 目标尺寸：优先用调用方指定的 scale，否则用编码器尺寸（已对齐偶数）
        tw = (target_width if target_width > 0 else self.width) & ~1
        th = (target_height if target_height > 0 else self.height) & ~1
        if tw != width or th != height:
            frame = frame.reformat(width=tw, height=th)

        # reformat 后再设置 pict_type（reformat 会创建新 frame，丢失属性）
        if self._force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I
            self._force_keyframe = False
            # 仅 rewrite_to_idr=True 时标记延迟重写
            if self.rewrite_to_idr:
                self._pending_kf_rewrite = True

        packets = self._codec.encode(frame)
        result = []
        for pkt in packets:
            data = bytes(pkt)
            # 统一转换为纯 annexb 格式（处理 AVCC/混合格式）
            data = H264Encoder._normalize_to_annexb(data)
            # 延迟重写：将 forced keyframe 的 non-IDR slice 重写为 IDR
            # 仅 rewrite_to_idr=True（MSE 路径）时执行
            if self.rewrite_to_idr and self._pending_kf_rewrite:
                data = self._rewrite_to_idr(data)
            result.append(data)
            # 诊断日志：解析 annexb NAL 类型
            if _logger.isEnabledFor(logging.DEBUG):
                nal_types = self._parse_nal_types(data)
                _logger.debug(
                    "encoded NAL types=%s, size=%d, pending_kf_rewrite=%s, rewrite_to_idr=%s",
                    nal_types,
                    len(data),
                    self._pending_kf_rewrite,
                    self.rewrite_to_idr,
                )
        return result

    @staticmethod
    def _normalize_to_annexb(data: bytes) -> bytes:
        """将 packet 数据统一转换为纯 annexb 格式。

        处理三种格式：
        1. 纯 annexb（包含 slice type 1/5）：直接返回
        2. 纯 AVCC（4-byte length prefix）：转换为 annexb
        3. 混合格式（SPS/PPS annexb + SLICE AVCC）：提取 AVCC slice，拼接为纯 annexb

        PyAV/libavcodec 在某些配置下可能输出 AVCC 或混合格式，
        导致只搜 00 00 01 的解析器找不到 slice。
        """
        # 快速检查：如果包含 slice (type 1/5)，说明是纯 annexb，直接返回
        types = H264Encoder._parse_nal_types(data)
        if any(t in (1, 5) for t in types):
            return data

        # 没有找到 slice，可能是 AVCC 或混合格式
        # 找到所有 annexb 起始码 00 00 01 的位置
        start_positions = []
        i = 0
        while i < len(data) - 2:
            if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
                start_positions.append(i)
                i += 3
            else:
                i += 1

        if not start_positions:
            # 没有 annexb 起始码，尝试纯 AVCC
            return H264Encoder._avcc_to_annexb(data)

        # 有 annexb 起始码但没有 slice：可能是混合格式
        # 最后一个 annexb NAL 的数据可能包含 AVCC slice
        last_start = start_positions[-1]
        last_nal_start = last_start + 3  # NAL header 位置（跳过 00 00 01）
        last_nal_data = data[last_nal_start:]

        # 诊断日志：hex dump 前 64 字节
        hex_dump = last_nal_data[:64].hex(" ")
        _logger.debug(
            "normalize_to_annexb: types=%s, start_positions=%s, "
            "last_nal_start=%d, last_nal_data_len=%d, hex=%s",
            types,
            start_positions,
            last_nal_start,
            len(last_nal_data),
            hex_dump,
        )

        # SPS/PPS 数据通常 < 100 bytes；若 last_nal_data 很长，可能内嵌 AVCC slice
        if len(last_nal_data) > 100:
            # 在 last_nal_data 中搜索 AVCC length-prefix（NAL type 1/5）
            # 从位置 2 开始搜索（PPS NAL 至少 1 byte header + 1 byte data）
            search_end = min(200, len(last_nal_data) - 5)
            for sps_pps_len in range(2, search_end):
                if len(last_nal_data) < sps_pps_len + 5:
                    break
                avcc_len = (
                    (last_nal_data[sps_pps_len] << 24)
                    | (last_nal_data[sps_pps_len + 1] << 16)
                    | (last_nal_data[sps_pps_len + 2] << 8)
                    | last_nal_data[sps_pps_len + 3]
                )
                if 0 < avcc_len <= len(last_nal_data) - sps_pps_len - 4:
                    nal_header = last_nal_data[sps_pps_len + 4]
                    nal_type = nal_header & 0x1F
                    if nal_type in (1, 5):
                        # 找到 AVCC slice，构建纯 annexb 数据
                        result = bytearray()
                        # annexb 部分（到 SPS/PPS 数据结束）
                        result += data[: last_nal_start + sps_pps_len]
                        # AVCC slice 转换为 annexb 起始码
                        result += b"\x00\x00\x00\x01"
                        result += last_nal_data[
                            sps_pps_len + 4 : sps_pps_len + 4 + avcc_len
                        ]
                        _logger.debug(
                            "normalize_to_annexb: FOUND AVCC slice at pos=%d, "
                            "type=%d, avcc_len=%d, result_size=%d",
                            sps_pps_len,
                            nal_type,
                            avcc_len,
                            len(result),
                        )
                        return bytes(result)

            _logger.debug(
                "normalize_to_annexb: AVCC search FAILED (no valid slice in pos 2-%d), "
                "last_nal_data_len=%d",
                search_end - 1,
                len(last_nal_data),
            )

        # 无法识别为 AVCC/混合格式，返回原始数据
        return data

    @staticmethod
    def _avcc_to_annexb(data: bytes) -> bytes:
        """将纯 AVCC 格式（4-byte length prefix）转换为 annexb 格式（起始码）。"""
        result = bytearray()
        i = 0
        while i + 4 <= len(data):
            length = (
                (data[i] << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3]
            )
            if length <= 0 or length > len(data) - i - 4:
                break
            result += b"\x00\x00\x00\x01"
            result += data[i + 4 : i + 4 + length]
            i += 4 + length
        # 只有完整解析（到达数据末尾）才返回转换结果
        if i == len(data) and len(result) > 0:
            _logger.debug(
                "avcc_to_annexb: converted %d bytes AVCC -> %d bytes annexb",
                len(data),
                len(result),
            )
            return bytes(result)
        return data

    @staticmethod
    def _parse_nal_types(data: bytes) -> list:
        """解析 annexb 数据中的所有 NAL 类型。

        支持 3-byte (00 00 01) 和 4-byte (00 00 00 01) 起始码。
        x264/ffmpeg 输出的 annexb 中首个 NAL 用 4-byte，后续 NAL 用 3-byte。
        搜索 00 00 01 即可匹配两种格式（4-byte 中的 00 00 01 在第二个 00 处匹配）。
        NAL 起始码之间不会出现 00 00 01（H.264 emulation prevention 保证）。
        """
        types = []
        i = 0
        while i < len(data) - 2:
            if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
                if i + 3 < len(data):
                    types.append(data[i + 3] & 0x1F)
                i += 3
            else:
                i += 1
        return types

    def _rewrite_to_idr(self, data: bytes) -> bytes:
        """将 annexb 数据中的 non-IDR slice (type 1) 重写为 IDR (type 5)。

        x264 延迟一帧输出：SPS/PPS 先出，slice 在下次 encode 时才出。
        当 _pending_kf_rewrite 为 True 时：
        - 如果输出只有 SPS/PPS（无 slice），保持标志，等待下次
        - 如果输出包含 slice（type 1 或 5），重写 type 1→5 并清除标志

        支持 3-byte (00 00 01) 和 4-byte (00 00 00 01) 起始码。
        NAL header 格式：forbidden_zero_bit(1) | nal_ref_idc(2) | nal_unit_type(5)
        只改 lower 5 bits（type），保留 nal_ref_idc。
        """
        result = bytearray(data)
        had_slice = False
        i = 0
        while i < len(result) - 2:
            if result[i] == 0 and result[i + 1] == 0 and result[i + 2] == 1:
                # NAL header 在 i+3
                if i + 3 < len(result):
                    nal_type = result[i + 3] & 0x1F
                    if nal_type == 1:  # non-IDR slice → IDR
                        result[i + 3] = (result[i + 3] & 0xE0) | 0x05
                        had_slice = True
                        _logger.debug("rewrite NAL type 1→5 (forced keyframe)")
                    elif nal_type == 5:  # 已经是 IDR
                        had_slice = True
                i += 3
            else:
                i += 1
        # 只有输出包含 slice 时才清除标志（SPS/PPS-only 输出需等待下次）
        if had_slice:
            self._pending_kf_rewrite = False
        return bytes(result)

    def force_keyframe(self):
        """标记下一帧为强制 keyframe。"""
        self._force_keyframe = True

    def close(self):
        if self._codec:
            try:
                self._codec.encode(None)
            except Exception:
                pass
            self._codec = None
