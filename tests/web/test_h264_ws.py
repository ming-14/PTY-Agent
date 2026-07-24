import asyncio
import json
import time
import struct

import pytest

import websockets
from src.fastscreen.server import create_app
from aiohttp import web


def parse_annex_b_nals(data: bytes) -> list[tuple[int, bytes]]:
    nals = []
    i = 0
    while i < len(data) - 3:
        start_code_len = 0
        if data[i:i+4] == b'\x00\x00\x00\x01':
            start_code_len = 4
        elif data[i:i+3] == b'\x00\x00\x01':
            start_code_len = 3
        else:
            i += 1
            continue

        nal_start = i + start_code_len
        nal_type = data[nal_start] & 0x1F

        next_start = nal_start + 1
        while next_start < len(data) - 3:
            if data[next_start:next_start+4] == b'\x00\x00\x00\x01' or data[next_start:next_start+3] == b'\x00\x00\x01':
                break
            next_start += 1
        else:
            next_start = len(data)

        nal_data = data[nal_start:next_start]
        nals.append((nal_type, nal_data))
        i = next_start

    return nals


@pytest.mark.asyncio
async def test_h264_websocket():
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()

    port = site._server.sockets[0].getsockname()[1]
    print(f"Server started on 127.0.0.1:{port}")

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/stream") as ws:
            await ws.send(json.dumps({
                "target_type": "monitor",
                "target_id": 0,
                "method": "auto",
                "fps": 15,
                "width": 0,
                "height": 0,
                "bitrate": 1000000,
                "gop_size": 15,
            }))

            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(response)
            print(f"Server response: {msg}")
            assert msg.get("status") == "streaming", f"Expected streaming, got: {msg}"

            keyframe_received = False
            pframe_received = False
            sps_found = False
            pps_found = False
            total_bytes = 0
            frame_count = 0
            start_time = time.time()

            while time.time() - start_time < 5.0:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    break

                if isinstance(data, str):
                    print(f"Text message: {data}")
                    continue

                total_bytes += len(data)
                frame_count += 1

                nals = parse_annex_b_nals(data)
                for nal_type, nal_data in nals:
                    if nal_type == 7:
                        sps_found = True
                        print(f"SPS found: {len(nal_data)} bytes")
                    elif nal_type == 8:
                        pps_found = True
                        print(f"PPS found: {len(nal_data)} bytes")
                    elif nal_type == 5:
                        keyframe_received = True
                        print(f"IDR frame: {len(nal_data)} bytes")
                    elif nal_type == 1:
                        pframe_received = True

                if frame_count <= 3:
                    print(f"Frame {frame_count}: {len(data)} bytes, {len(nals)} NALs, types={[t for t,_ in nals]}")

            elapsed = time.time() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0

            print(f"\n=== Results ===")
            print(f"Frames received: {frame_count}")
            print(f"Total bytes: {total_bytes}")
            print(f"FPS: {fps:.1f}")
            print(f"SPS found: {sps_found}")
            print(f"PPS found: {pps_found}")
            print(f"Keyframe received: {keyframe_received}")
            print(f"P-frame received: {pframe_received}")

            assert sps_found, "No SPS NAL found"
            assert pps_found, "No PPS NAL found"
            assert keyframe_received, "No keyframe (IDR) found"
            assert pframe_received, "No P-frames found"
            assert frame_count > 5, f"Too few frames: {frame_count}"

            print("\nAll assertions passed!")

    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(test_h264_websocket())
