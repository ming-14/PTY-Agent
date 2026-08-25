"""直连 daemon WS 验证 resize 后 scrollback 完整性（真实 ConPTY 场景）"""
import asyncio, json, re, uuid

WS_URL = "ws://127.0.0.1:18766/ws"

def strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07", "", s)

def split_snapshot(s):
    """snapshot 格式：每行 CSI row;1H + 内容——按 CSI 定位分割"""
    # 去掉光标显示序列后，按 \x1b[<n>;1H 或 \x1b[<n>;1f 分割
    cleaned = re.sub(r"\x1b\[\?[0-9;]*[hl]", "", s)
    parts = re.split(r"\x1b\[\d+;\d+[Hf]", cleaned)
    return [p.strip() for p in parts if p.strip()]

def split_residue(lines):
    n = 0
    for i in range(len(lines) - 1):
        if lines[i].endswith(" ") and lines[i + 1].startswith(" "):
            n += 1
    return n

async def recv_until(ws, pred, timeout=8):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        if isinstance(m, dict) and pred(m):
            return m
    return None

async def main():
    import websockets
    sid = "vr_" + uuid.uuid4().hex[:6]
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": "cmd /c chcp 65001 >nul & python -u -i",
            "cols": 90, "rows": 22,
        }))
        sub = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        if not sub:
            print("create/subscribe failed")
            return
        uid = sub.get("sessionUid")
        print("uid:", uid)
        await asyncio.sleep(0.8)

        # 输出 60 行 dir + __rikka 行 + 中文统计行（接近用户场景）
        script = (
            "for i in range(60):\n"
            " print('2026/08/' + str(i%28+1).zfill(2) + '  ' + str(i).zfill(2) + ':00    <DIR>          dir_' + str(i).zfill(3))\n"
            "\n"
            "print('2026/08/20  10:29    <DIR>          __rikka_goose')\n"
            "print('2026/08/23  14:28    <DIR>          __rikka_kimi')\n"
            "print('2026/08/23  14:20    <DIR>          __rikka_omp')\n"
            "print('2026/08/23  13:53    <DIR>          __rikka_pi')\n"
            "print('              35 个文件        460,630 字节')\n"
            "print('              90 个目录 132,898,054,144 可用字节')\n"
        )
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": script.replace("\n", "\r\n") + "\r\n",
        }))
        # 等待 90 个目录 输出到达
        await recv_until(ws, lambda m: m.get("type") == "output" and "可用字节" in m.get("data", ""), timeout=15)
        await asyncio.sleep(0.5)

        # baseline：resize 前模型 scrollback 行数（新连接订阅）
        async with websockets.connect(WS_URL) as ws2:
            await ws2.send(json.dumps({"type": "subscribe", "sessionUid": uid}))
            sub2 = await recv_until(ws2, lambda m: m.get("type") == "subscribed" and m.get("sessionUid") == uid)
            if sub2:
                pre_sb = sub2.get("scrollback", "") or ""
                pre_lines = [l for l in strip_ansi(pre_sb).split("\r\n") if l.strip()]
                print(f"[baseline] model scrollback lines={len(pre_lines)}")
                kimi_pre = [l for l in pre_lines if "__rikka_kimi" in l]
                print(f"  kimi in scrollback: {kimi_pre[:1]}")

        # 连续 resize：14 → 80 → 22 → 90
        # 连续 resize 两轮（模拟用户多次 resize 累积）
        for cycle in range(2):
            for cols in (14, 80, 22, 90):
                await ws.send(json.dumps({"type": "resize", "sessionUid": uid, "cols": cols, "rows": 22}))
                rc = await recv_until(ws, lambda m, c=cols: m.get("type") == "resize_complete" and m.get("sessionUid") == uid)
                if not rc:
                    print(f"[cycle{cycle} resize{cols}] no resize_complete")
                    continue
                sb = rc.get("scrollback") or ""
                snap = rc.get("snapshot") or ""
                lines = [l for l in strip_ansi(sb).split("\r\n") if l.strip()]
                snap_lines = split_snapshot(snap)
                # 冗余检测：scrollback 尾部行是否与 snapshot 顶部行相同（重叠）
                overlap = 0
                if lines and snap_lines:
                    for i in range(min(4, len(lines), len(snap_lines))):
                        if lines[-1 - i].strip() == snap_lines[i].strip():
                            overlap += 1
                # scrollback 内部重复检测：尾部 N 行 == 前面 N 行（reflow 残留）
                internal_dup = 0
                for n in range(1, min(8, len(lines) // 2)):
                    if [l.strip() for l in lines[-n:]] == [l.strip() for l in lines[-2 * n:-n]]:
                        internal_dup = n
                print(f"[cycle{cycle} resize{cols}] lines={len(lines)} snap_lines={len(snap_lines)} "
                      f"tail_head_overlap={overlap} internal_dup={internal_dup} split_residue={split_residue(lines)}")
                if overlap > 0:
                    print(f"  sb_tail: {[l[:40] for l in lines[-3:]]}")
                    print(f"  snap_head: {[l[:40] for l in snap_lines[:3]]}")

        # drop_feed 残留检测：resize 后再输出，检查模型 scrollback 是否增长
        await ws.send(json.dumps({"type": "input", "sessionUid": uid,
                                  "data": "print('after-resize-marker')\r\n"}))
        await asyncio.sleep(2.0)
        async with websockets.connect(WS_URL) as ws3:
            await ws3.send(json.dumps({"type": "subscribe", "sessionUid": uid}))
            sub3 = await recv_until(ws3, lambda m: m.get("type") == "subscribed" and m.get("sessionUid") == uid)
            if sub3:
                sb3 = sub3.get("scrollback", "") or ""
                lines3 = [l for l in strip_ansi(sb3).split("\r\n") if l.strip()]
                marker = [l for l in lines3 if "after-resize-marker" in l]
                print(f"[post-resize] scrollback lines={len(lines3)} marker_in_scrollback={bool(marker)}")

        await ws.send(json.dumps({"type": "kill", "sessionUid": uid}))

if __name__ == "__main__":
    asyncio.run(main())
