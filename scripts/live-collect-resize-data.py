"""连接 daemon 复现用户场景：收集 resize_complete 的 (scrollback, snapshot) 到 JSON"""
import asyncio, json, re, uuid

WS_URL = "ws://127.0.0.1:18766/ws"

async def recv_until(ws, pred, timeout=8):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        if isinstance(m, dict) and pred(m):
            return m
    return None

async def main():
    import websockets
    sid = "repro_" + uuid.uuid4().hex[:6]
    data = {"steps": []}
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": "cmd /c chcp 65001 >nul & python -u -i",
            "cols": 80, "rows": 24,
        }))
        sub = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        if not sub:
            print("create failed")
            return
        uid = sub.get("sessionUid")
        print("uid:", uid)
        await asyncio.sleep(0.5)

        # 输出 dir 风格内容（含 __rikka 行 + 中文统计行）
        script = (
            "for i in range(60):\n"
            " print('2026/08/' + str(i%28+1).zfill(2) + '  ' + str(i).zfill(2) + ':00    <DIR>          dir_' + str(i).zfill(3))\n"
            "\n"
            "print('2026/08/23  13:45    <DIR>          __rikka_atomcode')\n"
            "print('2026/08/18  05:28    <DIR>          __rikka_codearts')\n"
            "print('2026/08/20  11:26    <DIR>          __rikka_crush')\n"
            "print('2026/08/20  10:29    <DIR>          __rikka_goose')\n"
            "print('2026/08/23  14:28    <DIR>          __rikka_kimi')\n"
            "print('2026/08/23  14:20    <DIR>          __rikka_omp')\n"
            "print('2026/08/23  13:53    <DIR>          __rikka_pi')\n"
            "print('              35 个文件        460,630 字节')\n"
            "print('              90 个目录 132,911,558,656 可用字节')\n"
        )
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": script.replace("\n", "\r\n") + "\r\n",
        }))
        await recv_until(ws, lambda m: m.get("type") == "output" and "可用字节" in m.get("data", ""), timeout=15)
        await asyncio.sleep(0.5)

        # resize 序列（模拟用户长期会话：窄→宽交替多次）
        seq = [(30, 7), (106, 25), (12, 17), (69, 17), (14, 22), (80, 22), (33, 25), (114, 25)]
        for cols, rows in seq:
            await ws.send(json.dumps({"type": "resize", "sessionUid": uid, "cols": cols, "rows": rows}))
            rc = await recv_until(ws, lambda m: m.get("type") == "resize_complete" and m.get("sessionUid") == uid)
            if rc:
                data["steps"].append({
                    "cols": cols, "rows": rows,
                    "scrollback": rc.get("scrollback") or "",
                    "snapshot": rc.get("snapshot") or "",
                })
                print(f"[resize{cols}x{rows}] sb_len={len(rc.get('scrollback') or '')} snap_len={len(rc.get('snapshot') or '')}")

        await ws.send(json.dumps({"type": "kill", "sessionUid": uid}))
    with open("scripts/live-resize-data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("saved to scripts/live-resize-data.json")

if __name__ == "__main__":
    asyncio.run(main())
