"""asciicast → MP4 视频导出适配。

回放 cast 事件 → pywezterm.Terminal 逐帧渲染 → 每帧 PNG 字节
（pywezterm render 模块：fontdb 字体发现 + fontdue 光栅化 + tiny-skia 合成，
含 CJK/符号/框线完整回退，无 Python 侧手绘）→ ffmpeg 编码（H.264）。

时间轴（可变帧率）：**只在终端画面真正变化时出帧**——事件驱动 feed，
渲染后与上一帧字节比较，相同则跳过（光标移动/无效序列不出帧）；
帧的时长 = 到下一次画面变化的时间间隔（空闲段不产生任何帧，
由播放器按帧时长自然停留）。末帧时长 = tail。
VFR 经 ffmpeg concat demuxer（ffconcat：file + duration）编码。
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

from leaf.adapters.castfile import open_from_path
from leaf.domain.asciicast import Output, Resize

log = logging.getLogger("leaf.mp4")

# pywezterm render 模块的网格度量（common.rs）：scale=1.0 时每格像素
_CELL_W = 8
_CELL_H = 17


def find_ffmpeg() -> str:
    """定位 ffmpeg：PATH 优先，其次常见安装位置（全部经环境变量动态推导）。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # 环境变量动态推导的常见安装位置；不硬编码任何具体机器路径
    candidates = []
    for var in ("LOCALAPPDATA", "USERPROFILE"):
        base = os.environ.get(var)
        if not base:
            continue
        candidates += [
            os.path.join(base, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
            os.path.join(base, "scoop", "shims", "ffmpeg.exe"),
        ]
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "")
        if program_files:
            candidates.append(os.path.join(program_files, "ffmpeg", "bin", "ffmpeg.exe"))
    else:
        candidates += ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    raise RuntimeError(
        "未找到 ffmpeg。请安装后加入 PATH（Windows: https://www.gyan.dev/ffmpeg/builds/，"
        "Linux: apt install ffmpeg）"
    )


def _parse_border_color(s: str) -> str:
    """解析边框颜色 → ffmpeg 0xRRGGBB 格式。

    支持格式：'R,G,B'（如 '12,12,12'）或 'RRGGBB' / '#RRGGBB' / '0xRRGGBB'。
    """
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    if "," in s:
        parts = [int(x.strip()) for x in s.split(",")]
        if len(parts) != 3:
            raise ValueError(f"border_color 需要 3 个逗号分隔的 0-255 值: {s!r}")
        if not all(0 <= v <= 255 for v in parts):
            raise ValueError(f"border_color 各分量须在 0-255 之间: {parts}")
        return f"0x{parts[0]:02x}{parts[1]:02x}{parts[2]:02x}"
    if len(s) == 6:
        int(s, 16)  # 校验
        return "0x" + s.lower()
    raise ValueError(
        f"无法解析 border_color: {s!r}，请使用 R,G,B（如 '12,12,12'）或 RRGGBB"
    )


def export_mp4(cast_path: str, mp4_path: str,
               cell_w: int = 8, overwrite: bool = False,
               tail: float = 1.0, padding: int = 14,
               border_color: str = "12,12,12") -> None:
    """把 asciicast 文件导出为 MP4（可变帧率，全量变化驱动）。

    渲染完全委托 pywezterm（render_image，含 CJK/符号完整字体回退）：
    leaf 不参与任何像素/字形绘制。边框在 ffmpeg 编码阶段经 pad filter
    添加（四周各 padding 像素，默认 14，颜色默认 12,12,12 与终端背景一致）。

    cast_path: 输入 asciicast（本地/URL/zstd）
    mp4_path: 输出 mp4 路径
    cell_w: 每格像素宽（scale = cell_w / CELL_W，格高与宽等比）
    overwrite: 覆盖已存在输出
    tail: 末帧（最后画面）保持秒数
    padding: 四周边框像素（0 = 无边框）
    border_color: 边框颜色，'R,G,B'（如 '12,12,12'）或 'RRGGBB' / '#RRGGBB'
    """
    ffmpeg = find_ffmpeg()

    if not overwrite and os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
        raise ValueError("输出文件已存在，使用 overwrite=True 覆盖")

    from leaf.drivers import _engine
    _engine.ensure_engine()
    import pywezterm

    header, _version, events = open_from_path(cast_path)
    cols, rows = header.cols, header.rows
    term = pywezterm.Terminal(cols, rows, scrollback=10000)

    # scale：pywezterm render 模块按 CELL_W/CELL_H 基准缩放；cell_w 指定
    # 每格像素宽，等比换算 scale
    scale = max(cell_w, 1) / _CELL_W
    frame_w = round(cols * _CELL_W * scale)
    frame_h = round(rows * _CELL_H * scale)
    # yuv420p 要求偶数宽高：输出尺寸向上取偶（边框随之各多 0~1px）
    out_w = (frame_w + 2 * padding + 1) // 2 * 2
    out_h = (frame_h + 2 * padding + 1) // 2 * 2
    pad_x = (out_w - frame_w) // 2
    pad_y = (out_h - frame_h) // 2

    t0 = time.monotonic()

    # ---- 事件驱动渲染：只在画面变化时出帧，每帧直接落盘，仅存元数据 ----
    # 之前版本把所有帧的 PNG 字节存内存（frames = [(t, png_bytes)]），
    # 长录制时内存暴涨。改为每帧写入临时文件，frames 只存 (t, 文件路径)。
    tmpdir = tempfile.mkdtemp(prefix="leaf_mp4_")
    try:
        frames = []  # [(t, filepath), ...]
        prev_png = b""
        # 首帧：t=0 的初始画面（即使无事件也有一帧基线）
        prev_png = term.render_image(scale, "png")
        p0 = os.path.join(tmpdir, "f00000.png")
        with open(p0, "wb") as pf:
            pf.write(prev_png)
        frames.append((0.0, p0))

        ev_list = list(events)
        i = 0
        n = len(ev_list)
        while i < n:
            t = ev_list[i].time
            while i < n and ev_list[i].time == t:
                ev = ev_list[i]
                if isinstance(ev.data, Output):
                    term.feed(ev.data.data.encode("utf-8"))
                elif isinstance(ev.data, Resize):
                    term.resize(ev.data.cols, ev.data.rows)
                i += 1
            png = term.render_image(scale, "png")
            if png != prev_png:
                if frames and t == frames[-1][0]:
                    # 同时间戳变化：覆盖上帧文件（首帧基线被首事件覆盖）
                    fp = frames[-1][1]
                else:
                    fp = os.path.join(tmpdir, f"f{len(frames):05d}.png")
                with open(fp, "wb") as pf:
                    pf.write(png)
                if frames and t == frames[-1][0]:
                    frames[-1] = (t, fp)
                else:
                    frames.append((t, fp))
                prev_png = png

        # ---- ffconcat 列表：file + duration（VFR 编码） ----
        # 每帧时长 = 下一帧时间 - 本帧时间；末帧 = tail
        list_path = os.path.join(tmpdir, "frames.ffconcat")
        with open(list_path, "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            for k, (t, fp) in enumerate(frames):
                # concat 文件路径用正斜杠（Windows 反斜杠会被转义）
                f.write(f"file '{fp.replace(os.sep, '/')}'\n")
                if k + 1 < len(frames):
                    dur = frames[k + 1][0] - t
                else:
                    dur = tail
                f.write(f"duration {max(dur, 0.001):.6f}\n")

        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
        ]
        if padding > 0:
            # 四周边框（pad filter：显式偶数输出尺寸 + 居中偏移）
            border = _parse_border_color(border_color)
            cmd += ["-vf", f"pad={out_w}:{out_h}:{pad_x}:{pad_y}:{border}"]
        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            mp4_path,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 导出失败 exit={proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace')[-500:]}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    elapsed = time.monotonic() - t0
    total_duration = frames[-1][0] + tail if frames else 0.0
    log.info("exported %s (%d 变化帧, %dx%d, %.2fs, 编码 %.1fs)",
             mp4_path, len(frames), out_w, out_h, total_duration, elapsed)
