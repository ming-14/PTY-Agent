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


@pytest.mark.asyncio
async def test_mse_detailed():
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
            assert msg.get("status") == "streaming"

            init_received = False
            media_received = False
            total_bytes = 0
            seg_count = 0
            start_time = time.time()

            while time.time() - start_time < 10.0:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    break

                if isinstance(data, str):
                    continue

                raw = data
                if len(raw) < 8:
                    continue

                marker = raw[4:8].decode("ascii", errors="replace")
                payload = raw[8:]
                seg_count += 1
                total_bytes += len(raw)

                boxes = parse_boxes(payload)
                box_summary = " ".join(f"{t}({s})" for t, s, _ in boxes)

                if marker == "init":
                    init_received = True
                    print(f"[INIT] {len(payload)} bytes: {box_summary}")
                    for btype, bsize, bdata in boxes:
                        if btype == "moov":
                            sub = parse_boxes(bdata)
                            print(f"  moov sub-boxes: {' '.join(f'{t}({s})' for t, s, _ in sub)}")
                            for st, ss, sd in sub:
                                if st == "mvex":
                                    mvex_sub = parse_boxes(sd)
                                    print(f"  mvex sub-boxes: {' '.join(f'{t}({s})' for t, s, _ in mvex_sub)}")

                elif marker == "segm":
                    media_received = True
                    if seg_count <= 5:
                        print(f"[SEGM #{seg_count}] {len(payload)} bytes: {box_summary}")
                        for btype, bsize, bdata in boxes:
                            if btype == "moof":
                                sub = parse_boxes(bdata)
                                print(f"  moof sub-boxes: {' '.join(f'{t}({s})' for t, s, _ in sub)}")
                                for st, ss, sd in sub:
                                    if st == "traf":
                                        traf_sub = parse_boxes(sd)
                                        print(f"  traf sub-boxes: {' '.join(f'{t}({s})' for t, s, _ in traf_sub)}")
                                        for tt, ts, td in traf_sub:
                                            if tt == "trun":
                                                flags = struct.unpack(">I", td[0:4])[0] & 0xFFFFFF
                                                sample_count = struct.unpack(">I", td[4:8])[0]
                                                print(f"  trun: flags=0x{flags:06x} samples={sample_count}")
                                                if flags & 0x000001:
                                                    data_offset = struct.unpack(">i", td[8:12])[0]
                                                    print(f"  trun data_offset={data_offset}")
                                            elif tt == "tfdt":
                                                bmdt = struct.unpack(">Q", td[4:12])[0]
                                                print(f"  tfdt base_media_decode_time={bmdt}")

            elapsed = time.time() - start_time
            print(f"\n=== Results ===")
            print(f"Segments: {seg_count}, Bytes: {total_bytes}, Segs/sec: {seg_count/elapsed:.1f}")
            print(f"Init: {init_received}, Media: {media_received}")

            assert init_received, "No init segment"
            assert media_received, "No media segment"
            assert seg_count > 3, f"Too few segments: {seg_count}"
            print("All assertions passed!")

    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(test_mse_detailed())
