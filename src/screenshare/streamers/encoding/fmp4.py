from __future__ import annotations

from ....logging import get_logger
import struct
from typing import Optional

logger = get_logger("screenshare.fmp4")


def _box(box_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I", 8 + len(data)) + box_type + data


def _fullbox(box_type: bytes, version: int, flags: int, data: bytes) -> bytes:
    return _box(box_type, struct.pack(">I", (version << 24) | flags) + data)


def _ftyp() -> bytes:
    return _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom")


def _mvhd(timescale: int, duration: int) -> bytes:
    data = struct.pack(">II", 0, 0)
    data += struct.pack(">II", timescale, duration)
    data += struct.pack(">I", 0x00010000)
    data += struct.pack(">H", 0x0100)
    data += b"\x00" * 10
    data += struct.pack(
        ">IIIIIIIII", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000
    )
    data += b"\x00" * 24
    data += struct.pack(">I", 2)
    return _fullbox(b"mvhd", 0, 0, data)


def _tkhd(track_id: int, width: int, height: int, duration: int) -> bytes:
    data = struct.pack(">II", 0, 0)
    data += struct.pack(">I", track_id)
    data += struct.pack(">I", 0)
    data += struct.pack(">I", duration)
    data += b"\x00" * 8
    data += struct.pack(">HH", 0, 0)
    data += struct.pack(">H", 0)
    data += struct.pack(">H", 0)
    data += struct.pack(
        ">IIIIIIIII", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000
    )
    data += struct.pack(">II", width << 16, height << 16)
    return _fullbox(b"tkhd", 0, 3, data)


def _mdhd(timescale: int, duration: int) -> bytes:
    data = struct.pack(">IIII", 0, 0, timescale, duration)
    data += struct.pack(">HH", 0x55C4, 0)
    return _fullbox(b"mdhd", 0, 0, data)


def _hdlr() -> bytes:
    data = struct.pack(">I", 0)
    data += b"vide"
    data += b"\x00" * 12
    data += b"VideoHandler\x00"
    return _fullbox(b"hdlr", 0, 0, data)


def _vmhd() -> bytes:
    data = struct.pack(">H", 0) + b"\x00" * 6
    return _fullbox(b"vmhd", 0, 1, data)


def _dref() -> bytes:
    entry = _fullbox(b"url ", 0, 1, b"")
    data = struct.pack(">I", 1) + entry
    return _fullbox(b"dref", 0, 0, data)


def _dinf() -> bytes:
    return _box(b"dinf", _dref())


def _avcC(sps: bytes, pps: bytes) -> bytes:
    data = struct.pack(">B", 1)
    data += struct.pack(">B", sps[1])
    data += struct.pack(">B", sps[2])
    data += struct.pack(">B", sps[3])
    data += struct.pack(">B", 0xFF)
    data += struct.pack(">B", 0xE1)
    data += struct.pack(">H", len(sps))
    data += sps
    data += struct.pack(">B", 1)
    data += struct.pack(">H", len(pps))
    data += pps
    return _box(b"avcC", data)


def _avc1(width: int, height: int, sps: bytes, pps: bytes) -> bytes:
    data = b"\x00" * 6
    data += struct.pack(">H", 1)
    data += b"\x00" * 2
    data += b"\x00" * 2
    data += b"\x00" * 12
    data += struct.pack(">HH", width, height)
    data += struct.pack(">II", 0x00480000, 0x00480000)
    data += struct.pack(">I", 0)
    data += struct.pack(">H", 1)
    data += b"\x00" * 32
    data += struct.pack(">H", 24)
    data += struct.pack(">h", -1)
    data += _avcC(sps, pps)
    return _box(b"avc1", data)


def _stsd(width: int, height: int, sps: bytes, pps: bytes) -> bytes:
    entry = _avc1(width, height, sps, pps)
    data = struct.pack(">I", 1) + entry
    return _fullbox(b"stsd", 0, 0, data)


def _stts() -> bytes:
    return _fullbox(b"stts", 0, 0, struct.pack(">I", 0))


def _stsc() -> bytes:
    return _fullbox(b"stsc", 0, 0, struct.pack(">I", 0))


def _stsz() -> bytes:
    return _fullbox(b"stsz", 0, 0, struct.pack(">II", 0, 0))


def _stco() -> bytes:
    return _fullbox(b"stco", 0, 0, struct.pack(">I", 0))


def _trex(track_id: int) -> bytes:
    data = struct.pack(">I", track_id)
    data += struct.pack(">I", 1)
    data += struct.pack(">I", 0)
    data += struct.pack(">I", 0)
    data += struct.pack(">I", 0)
    return _fullbox(b"trex", 0, 0, data)


def _mvex(track_id: int) -> bytes:
    return _box(b"mvex", _trex(track_id))


def _stbl(width: int, height: int, sps: bytes, pps: bytes) -> bytes:
    return _box(
        b"stbl", _stsd(width, height, sps, pps) + _stts() + _stsc() + _stsz() + _stco()
    )


def _minf(width: int, height: int, sps: bytes, pps: bytes) -> bytes:
    return _box(b"minf", _vmhd() + _dinf() + _stbl(width, height, sps, pps))


def _mdia(
    width: int, height: int, sps: bytes, pps: bytes, timescale: int, duration: int
) -> bytes:
    return _box(
        b"mdia", _mdhd(timescale, duration) + _hdlr() + _minf(width, height, sps, pps)
    )


def _trak(
    width: int, height: int, sps: bytes, pps: bytes, timescale: int, duration: int
) -> bytes:
    return _box(
        b"trak",
        _tkhd(1, width, height, duration)
        + _mdia(width, height, sps, pps, timescale, duration),
    )


def _moov(width: int, height: int, sps: bytes, pps: bytes, timescale: int) -> bytes:
    return _box(
        b"moov",
        _mvhd(timescale, 0) + _trak(width, height, sps, pps, timescale, 0) + _mvex(1),
    )


def _mfhd(sequence_number: int) -> bytes:
    return _fullbox(b"mfhd", 0, 0, struct.pack(">I", sequence_number))


def _tfhd(track_id: int) -> bytes:
    data = struct.pack(">I", track_id)
    return _fullbox(b"tfhd", 0, 0x020000, data)


def _tfdt(base_media_decode_time: int) -> bytes:
    return _fullbox(b"tfdt", 1, 0, struct.pack(">Q", base_media_decode_time))


def _traf(
    track_id: int,
    base_media_decode_time: int,
    samples: list[tuple[int, int, bool]],
    data_offset: int,
) -> bytes:
    return _box(
        b"traf",
        _tfhd(track_id)
        + _tfdt(base_media_decode_time)
        + _trun_with_offset(samples, data_offset),
    )


def _trun_with_offset(samples: list[tuple[int, int, bool]], data_offset: int) -> bytes:
    flags = 0x000701
    data = struct.pack(">I", len(samples))
    data += struct.pack(">i", data_offset)
    for duration, size, is_key in samples:
        data += struct.pack(">I", duration)
        data += struct.pack(">I", size)
        if is_key:
            data += struct.pack(">I", 0x02000000)
        else:
            data += struct.pack(">I", 0x01010000)
    return _fullbox(b"trun", 0, flags, data)


def _mdat(data: bytes) -> bytes:
    return _box(b"mdat", data)


def annex_b_to_avcc(data: bytes) -> list[bytes]:
    nals = []
    i = 0
    while i < len(data):
        start_code_len = 0
        if (
            i + 3 < len(data)
            and data[i] == 0
            and data[i + 1] == 0
            and data[i + 2] == 0
            and data[i + 3] == 1
        ):
            start_code_len = 4
        elif (
            i + 2 < len(data) and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1
        ):
            start_code_len = 3
        else:
            i += 1
            continue

        nal_start = i + start_code_len
        next_start = nal_start + 1
        while next_start < len(data) - 3:
            if data[next_start] == 0 and data[next_start + 1] == 0:
                if data[next_start + 2] == 1:
                    break
                if (
                    next_start < len(data) - 4
                    and data[next_start + 2] == 0
                    and data[next_start + 3] == 1
                ):
                    break
            next_start += 1
        else:
            next_start = len(data)

        nal_data = data[nal_start:next_start]
        nals.append(nal_data)
        i = next_start

    return nals


def extract_sps_pps(data: bytes) -> Optional[tuple[bytes, bytes]]:
    nals = annex_b_to_avcc(data)
    sps = None
    pps = None
    for nal in nals:
        nal_type = nal[0] & 0x1F
        if nal_type == 7:
            sps = nal
        elif nal_type == 8:
            pps = nal
    if sps and pps:
        return sps, pps
    return None


def nals_to_avcc(data: bytes) -> bytes:
    nals = annex_b_to_avcc(data)
    result = bytearray()
    for nal in nals:
        result += struct.pack(">I", len(nal))
        result += nal
    return bytes(result)


def is_keyframe_annexb(data: bytes) -> bool:
    nals = annex_b_to_avcc(data)
    for nal in nals:
        nal_type = nal[0] & 0x1F
        if nal_type == 5 or nal_type == 7:
            return True
    return False


class FMP4Muxer:
    def __init__(self, width: int, height: int, timescale: int = 1000):
        self.width = width
        self.height = height
        self.timescale = timescale
        self._sequence_number = 0
        self._base_media_decode_time = 0
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._init_segment: Optional[bytes] = None

    def _ensure_init_segment(self, sps: bytes, pps: bytes) -> bytes:
        if self._init_segment and self._sps == sps and self._pps == pps:
            return self._init_segment
        self._sps = sps
        self._pps = pps
        self._init_segment = _ftyp() + _moov(
            self.width, self.height, sps, pps, self.timescale
        )
        return self._init_segment

    def create_init_segment(self, first_frame_data: bytes) -> Optional[bytes]:
        result = extract_sps_pps(first_frame_data)
        if result is None:
            logger.warning(
                "create_init_segment: no SPS/PPS found in frame data (%d bytes)",
                len(first_frame_data),
            )
            return None
        sps, pps = result
        logger.info(
            "create_init_segment: SPS=%d bytes (profile=%d, compat=%d, level=%d), PPS=%d bytes, %dx%d",
            len(sps),
            sps[1],
            sps[2],
            sps[3],
            len(pps),
            self.width,
            self.height,
        )
        return self._ensure_init_segment(sps, pps)

    def create_media_segment(
        self,
        frames: list[tuple[bytes, int]],
    ) -> bytes:
        self._sequence_number += 1

        samples = []
        mdat_data = bytearray()

        for frame_data, duration_ms in frames:
            avcc_data = nals_to_avcc(frame_data)
            is_key = is_keyframe_annexb(frame_data)
            samples.append((duration_ms, len(avcc_data), is_key))
            mdat_data += avcc_data

        moof = _box(
            b"moof",
            _mfhd(self._sequence_number)
            + _traf(1, self._base_media_decode_time, samples, 0),
        )

        data_offset = len(moof) + 8
        moof = _box(
            b"moof",
            _mfhd(self._sequence_number)
            + _traf(1, self._base_media_decode_time, samples, data_offset),
        )

        mdat = _mdat(bytes(mdat_data))

        total_duration = sum(d for _, d in frames)
        self._base_media_decode_time += total_duration

        logger.debug(
            "media_segment: seq=%d, samples=%d, moof=%d, mdat=%d, bmdt=%d",
            self._sequence_number,
            len(samples),
            len(moof),
            len(mdat),
            self._base_media_decode_time - total_duration,
        )

        return moof + mdat
