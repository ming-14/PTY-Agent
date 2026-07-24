import asyncio
import json
import time
import struct

import pytest

import websockets
from src.fastscreen.server import create_app
from aiohttp import web


def parse_boxes(data: bytes) -> list[tuple[str, int, bytes]]:
    boxes = []
    offset = 0
    while offset + 8 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        box_type = data[offset + 4:offset + 8].decode("ascii", errors="replace")
        if size < 8 or offset + size > len(data):
            break
        box_data = data[offset + 8:offset + size]
        boxes.append((box_type, size, box_data))
        offset += size
    return boxes


def find_box(boxes: list[tuple[str, int, bytes]], box_type: str) -> list[tuple[str, int, bytes]]:
    return [(t, s, d) for t, s, d in boxes if t == box_type]


def find_box_in_data(data: bytes, box_type: str) -> list[tuple[str, int, bytes]]:
    return find_box(parse_boxes(data), box_type)


@pytest.mark.asyncio
async def test_mse_websocket():
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()

    port = site._server.sockets[0].getsockname()[1]
    print(f"Server started on 127.0.0.1:{port}")

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/stream_mse") as ws:
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

            init_segment_received = False
            media_segment_received = False
            total_bytes = 0
            segment_count = 0
            start_time = time.time()

            while time.time() - start_time < 8.0:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    break

                if isinstance(data, str):
                    print(f"Text message: {data}")
                    continue

                raw = data
                if len(raw) < 8:
                    continue

                marker = raw[4:8].decode("ascii", errors="replace")
                payload = raw[8:]
                segment_count += 1
                total_bytes += len(raw)

                if segment_count <= 5:
                    print(f"Segment {segment_count}: marker={marker}, payload={len(payload)} bytes")

                if marker == "init":
                    init_segment_received = True
                    boxes = parse_boxes(payload)
                    box_types = [t for t, _, _ in boxes]
                    print(f"  Init segment boxes: {box_types}")
                    assert "ftyp" in box_types, f"ftyp not found in init segment: {box_types}"
                    assert "moov" in box_types, f"moov not found in init segment: {box_types}"

                    moov_data = None
                    for t, s, d in boxes:
                        if t == "moov":
                            moov_data = d
                            break
                    assert moov_data is not None

                    moov_sub_boxes = parse_boxes(moov_data)
                    moov_sub_types = [t for t, _, _ in moov_sub_boxes]
                    assert "mvhd" in moov_sub_types, f"mvhd not found in moov: {moov_sub_types}"
                    assert "trak" in moov_sub_types, f"trak not found in moov: {moov_sub_types}"

                elif marker == "segm":
                    media_segment_received = True
                    boxes = parse_boxes(payload)
                    box_types = [t for t, _, _ in boxes]
                    if segment_count <= 5:
                        print(f"  Media segment boxes: {box_types}")
                    assert "moof" in box_types, f"moof not found in media segment: {box_types}"
                    assert "mdat" in box_types, f"mdat not found in media segment: {box_types}"

            elapsed = time.time() - start_time
            fps = segment_count / elapsed if elapsed > 0 else 0

            print(f"\n=== Results ===")
            print(f"Segments received: {segment_count}")
            print(f"Total bytes: {total_bytes}")
            print(f"Segments/sec: {fps:.1f}")
            print(f"Init segment received: {init_segment_received}")
            print(f"Media segment received: {media_segment_received}")

            assert init_segment_received, "No init segment received"
            assert media_segment_received, "No media segment received"
            assert segment_count > 3, f"Too few segments: {segment_count}"

            print("\nAll assertions passed!")

    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(test_mse_websocket())
