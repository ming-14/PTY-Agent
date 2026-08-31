"""将 _analysis.json 转换为可视化 HTML 报告。"""
from __future__ import annotations

import html
import json
from pathlib import Path

# 项目根目录：本脚本位于 <root>/tools/analyze/，向上两级
ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DATA = json.loads((SCRIPT_DIR / "_analysis.json").read_text(encoding="utf-8"))

OUT = SCRIPT_DIR / "code-scale-report.html"


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_num(n: int) -> str:
    return f"{n:,}"


def esc(s: str) -> str:
    return html.escape(str(s))


# ---- 计算汇总 ----
top = DATA["top"]
src_sub = DATA["src_sub"]
web_sub = DATA["web_sub"]
external = DATA["external"]

# 主统计（排除外部）
main_modules = [m for m in top]
total_files = sum(m["files"] for m in main_modules)
total_code_files = sum(m["code_files"] for m in main_modules)
total_lines = sum(m["code_lines"] for m in main_modules)
total_bytes = sum(m["total_bytes"] for m in main_modules)
total_blank = sum(m["blank_lines"] for m in main_modules)
total_comment = sum(m["comment_lines"] for m in main_modules)
total_code_only = total_lines - total_blank - total_comment

# 按代码行数排序的顶层模块（用于主条形图）
top_by_lines = sorted(main_modules, key=lambda m: m["code_lines"], reverse=True)
max_top_lines = max((m["code_lines"] for m in top_by_lines), default=1)

# src 子模块按行数排序
src_sub_by_lines = sorted(src_sub, key=lambda m: m["code_lines"], reverse=True)
max_sub_lines = max((m["code_lines"] for m in src_sub_by_lines), default=1)

# src/web 子模块按行数排序
web_sub_by_lines = sorted(web_sub, key=lambda m: m["code_lines"], reverse=True)
max_web_lines = max((m["code_lines"] for m in web_sub_by_lines), default=1)

# 文件类型分布（汇总主模块）
ext_map: dict[str, int] = {}
for m in main_modules:
    for ext, cnt in m["by_ext"].items():
        ext_map[ext] = ext_map.get(ext, 0) + cnt
ext_sorted = sorted(ext_map.items(), key=lambda x: -x[1])
max_ext_cnt = ext_sorted[0][1] if ext_sorted else 1

# Top 15 文件（按行数）
all_top_files: list[dict] = []
for m in main_modules:
    for f in m["top_files"]:
        all_top_files.append({"path": f["path"], "lines": f["lines"], "module": m["name"]})
all_top_files.sort(key=lambda x: x["lines"], reverse=True)
top15_files = all_top_files[:15]

# 颜色调色板（按模块分配稳定颜色）
PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab",
    "#86b086", "#8cd17d", "#b6992d", "#f1ce63", "#499894",
    "#d37a80", "#a0cbe8", "#ffbe7d", "#8e9eab", "#79706e",
    "#d4b4a0",
]


def color_for(idx: int) -> str:
    return PALETTE[idx % len(PALETTE)]


# ---- 生成卡片 ----
def overview_card(label: str, value: str, sub: str = "", color: str = "#4e79a7") -> str:
    return f"""
    <div class="card" style="border-top-color:{color}">
      <div class="card-label">{esc(label)}</div>
      <div class="card-value">{esc(value)}</div>
      <div class="card-sub">{esc(sub)}</div>
    </div>"""


# ---- 顶层模块条形图行 ----
def top_bar_row(m: dict, idx: int) -> str:
    pct = m["code_lines"] / max_top_lines * 100
    share = m["code_lines"] / total_lines * 100 if total_lines else 0
    c = color_for(idx)
    return f"""
    <div class="bar-row" data-module="{esc(m['name'])}">
      <div class="bar-label">
        <span class="dot" style="background:{c}"></span>
        <span class="bar-name">{esc(m['name'])}</span>
        <span class="bar-desc">{esc(m['desc'])}</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:{pct:.2f}%;background:{c}"></div>
        <span class="bar-value">{fmt_num(m['code_lines'])} 行</span>
      </div>
      <div class="bar-share">{share:.1f}%</div>
      <div class="bar-files">{fmt_num(m['files'])} 文件</div>
      <div class="bar-size">{fmt_bytes(m['total_bytes'])}</div>
    </div>"""


# ---- src 子模块条形图行 ----
def sub_bar_row(m: dict, idx: int, max_lines: int | None = None) -> str:
    mx = max_lines if max_lines is not None else max_sub_lines
    pct = m["code_lines"] / mx * 100 if mx else 0
    c = color_for(idx)
    return f"""
    <div class="bar-row sub">
      <div class="bar-label">
        <span class="dot" style="background:{c}"></span>
        <span class="bar-name">{esc(m['name'])}</span>
        <span class="bar-desc">{esc(m['desc'])}</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:{pct:.2f}%;background:{c}"></div>
        <span class="bar-value">{fmt_num(m['code_lines'])} 行</span>
      </div>
      <div class="bar-share">{fmt_num(m['files'])} 文件</div>
    </div>"""


# ---- 文件类型分布行 ----
def ext_bar_row(ext: str, cnt: int, idx: int) -> str:
    pct = cnt / max_ext_cnt * 100
    c = color_for(idx)
    return f"""
    <div class="bar-row ext">
      <div class="bar-label">
        <span class="dot" style="background:{c}"></span>
        <span class="bar-name ext-name">{esc(ext)}</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:{pct:.2f}%;background:{c}"></div>
        <span class="bar-value">{fmt_num(cnt)} 个</span>
      </div>
    </div>"""


# ---- 外部模块行 ----
def ext_module_row(m: dict, idx: int) -> str:
    c = "#9aa0a6"
    return f"""
    <div class="bar-row external">
      <div class="bar-label">
        <span class="dot" style="background:{c}"></span>
        <span class="bar-name">{esc(m['name'])}</span>
        <span class="bar-desc">{esc(m['desc'])}</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:100%;background:{c};opacity:0.5"></div>
        <span class="bar-value">{fmt_num(m['code_lines'])} 行</span>
      </div>
      <div class="bar-share">{fmt_num(m['files'])} 文件</div>
      <div class="bar-size">{fmt_bytes(m['total_bytes'])}</div>
    </div>"""


# ---- Top 文件表行 ----
def top_file_row(f: dict, idx: int) -> str:
    return f"""
    <tr>
      <td class="rank">{idx + 1}</td>
      <td class="file-path">{esc(f['path'])}</td>
      <td class="file-module">{esc(f['module'])}</td>
      <td class="file-lines">{fmt_num(f['lines'])}</td>
    </tr>"""


# ---- 模块详情卡片 ----
def module_detail_card(m: dict, idx: int) -> str:
    c = color_for(idx)
    ext_items = ""
    for ext, cnt in list(m["by_ext"].items())[:6]:
        ext_items += f'<span class="ext-chip"><b>{esc(ext)}</b> {cnt}</span>'
    top_files_html = ""
    for f in m["top_files"][:5]:
        top_files_html += f'<li><span class="tf-path">{esc(f["path"])}</span><span class="tf-lines">{fmt_num(f["lines"])} 行</span></li>'
    code_only = m["code_lines"] - m["blank_lines"] - m["comment_lines"]
    return f"""
    <div class="detail-card" style="border-top-color:{c}">
      <div class="detail-header">
        <span class="dot" style="background:{c}"></span>
        <h3>{esc(m['name'])}</h3>
        <span class="detail-desc">{esc(m['desc'])}</span>
      </div>
      <div class="detail-stats">
        <div><span class="stat-k">代码行</span><span class="stat-v">{fmt_num(m['code_lines'])}</span></div>
        <div><span class="stat-k">纯代码</span><span class="stat-v">{fmt_num(code_only)}</span></div>
        <div><span class="stat-k">注释</span><span class="stat-v">{fmt_num(m['comment_lines'])}</span></div>
        <div><span class="stat-k">空行</span><span class="stat-v">{fmt_num(m['blank_lines'])}</span></div>
        <div><span class="stat-k">文件数</span><span class="stat-v">{fmt_num(m['files'])}</span></div>
        <div><span class="stat-k">大小</span><span class="stat-v">{fmt_bytes(m['total_bytes'])}</span></div>
      </div>
      <div class="detail-ext">{ext_items}</div>
      {('<ul class="detail-top-files">' + top_files_html + '</ul>') if top_files_html else ''}
    </div>"""


# ---- 组装 HTML ----
overview_cards = (
    overview_card("代码总行数", fmt_num(total_lines), f"纯代码 {fmt_num(total_code_only)} · 注释 {fmt_num(total_comment)} · 空行 {fmt_num(total_blank)}", "#4e79a7")
    + overview_card("文件总数", fmt_num(total_files), f"代码文件 {fmt_num(total_code_files)}", "#f28e2b")
    + overview_card("总大小", fmt_bytes(total_bytes), "主模块合计", "#59a14f")
    + overview_card("顶层模块", str(len(main_modules)), f"src 子模块 {len(src_sub)}", "#af7aa1")
)

top_bars = "\n".join(top_bar_row(m, i) for i, m in enumerate(top_by_lines))
sub_bars = "\n".join(sub_bar_row(m, i) for i, m in enumerate(src_sub_by_lines))
web_bars = "\n".join(sub_bar_row(m, i, max_web_lines) for i, m in enumerate(web_sub_by_lines))
ext_bars = "\n".join(ext_bar_row(ext, cnt, i) for i, (ext, cnt) in enumerate(ext_sorted[:20]))
external_bars = "\n".join(ext_module_row(m, i) for i, m in enumerate(external))
top_file_rows = "\n".join(top_file_row(f, i) for i, f in enumerate(top15_files))
detail_cards = "\n".join(module_detail_card(m, i) for i, m in enumerate(top_by_lines))

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PTY-Agent 代码规模报告</title>
<style>
  :root {{
    --bg: #0f1419;
    --panel: #1a1f29;
    --panel-2: #222836;
    --border: #2d3548;
    --text: #e6e6e6;
    --text-dim: #9aa0a6;
    --text-mute: #6b7280;
    --accent: #4e79a7;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "Segoe UI", "Microsoft YaHei", -apple-system, sans-serif;
    line-height: 1.5;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px 64px; }}
  header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 20px;
    margin-bottom: 28px;
  }}
  header h1 {{ margin: 0 0 6px; font-size: 28px; font-weight: 600; }}
  header .subtitle {{ color: var(--text-dim); font-size: 14px; }}
  header .meta {{ color: var(--text-mute); font-size: 12px; margin-top: 8px; }}

  .overview {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }}
  .card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-top: 3px solid var(--accent);
    border-radius: 8px;
    padding: 16px 18px;
  }}
  .card-label {{ color: var(--text-dim); font-size: 12px; margin-bottom: 6px; }}
  .card-value {{ font-size: 24px; font-weight: 600; }}
  .card-sub {{ color: var(--text-mute); font-size: 11px; margin-top: 6px; }}

  section {{ margin-bottom: 36px; }}
  section > h2 {{
    font-size: 18px;
    font-weight: 600;
    margin: 0 0 16px;
    padding-left: 10px;
    border-left: 3px solid var(--accent);
  }}
  section > .sec-desc {{ color: var(--text-mute); font-size: 12px; margin: -10px 0 16px 14px; }}

  .bar-row {{
    display: grid;
    grid-template-columns: 220px 1fr 70px 90px 90px;
    gap: 12px;
    align-items: center;
    padding: 7px 0;
    border-bottom: 1px solid rgba(45,53,72,0.4);
  }}
  .bar-row.sub {{ grid-template-columns: 220px 1fr 90px; }}
  .bar-row.ext {{ grid-template-columns: 100px 1fr; }}
  .bar-row.external {{ opacity: 0.75; }}
  .bar-label {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .bar-name {{ font-weight: 600; font-size: 13px; white-space: nowrap; }}
  .bar-name.ext-name {{ font-family: "Consolas", monospace; }}
  .bar-desc {{ color: var(--text-mute); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ position: relative; height: 22px; background: rgba(45,53,72,0.5); border-radius: 4px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.4s ease; }}
  .bar-value {{ position: absolute; right: 8px; top: 50%; transform: translateY(-50%); font-size: 11px; color: var(--text); font-weight: 600; text-shadow: 0 1px 2px rgba(0,0,0,0.7); }}
  .bar-share, .bar-files, .bar-size {{ font-size: 12px; color: var(--text-dim); text-align: right; white-space: nowrap; }}

  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px 18px; }}
  .panel.external-panel {{ border-style: dashed; opacity: 0.85; }}
  .panel.external-panel > h3 {{ color: var(--text-dim); margin: 0 0 12px; font-size: 14px; }}
  .ext-note {{ color: var(--text-mute); font-size: 11px; margin: -8px 0 12px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; color: var(--text-dim); font-weight: 600; padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 12px; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid rgba(45,53,72,0.4); }}
  tr:hover td {{ background: rgba(78,121,167,0.08); }}
  .rank {{ color: var(--text-mute); width: 40px; text-align: center; }}
  .file-path {{ font-family: "Consolas", monospace; font-size: 12px; color: #b6c2d9; }}
  .file-module {{ color: var(--text-dim); font-size: 12px; }}
  .file-lines {{ text-align: right; font-weight: 600; }}

  .details-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 14px; }}
  .detail-card {{ background: var(--panel); border: 1px solid var(--border); border-top: 3px solid var(--accent); border-radius: 8px; padding: 14px 16px; }}
  .detail-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }}
  .detail-header h3 {{ margin: 0; font-size: 15px; }}
  .detail-desc {{ color: var(--text-mute); font-size: 11px; }}
  .detail-stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px; }}
  .detail-stats > div {{ display: flex; flex-direction: column; background: var(--panel-2); border-radius: 4px; padding: 6px 8px; }}
  .stat-k {{ font-size: 10px; color: var(--text-mute); }}
  .stat-v {{ font-size: 14px; font-weight: 600; }}
  .detail-ext {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
  .ext-chip {{ background: var(--panel-2); border-radius: 10px; padding: 2px 8px; font-size: 11px; color: var(--text-dim); }}
  .ext-chip b {{ color: #b6c2d9; font-family: "Consolas", monospace; }}
  .detail-top-files {{ list-style: none; margin: 6px 0 0; padding: 0; }}
  .detail-top-files li {{ display: flex; justify-content: space-between; font-size: 11px; padding: 3px 0; border-bottom: 1px dashed rgba(45,53,72,0.4); }}
  .tf-path {{ color: var(--text-dim); font-family: "Consolas", monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 8px; }}
  .tf-lines {{ color: var(--text-mute); flex-shrink: 0; }}

  footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--text-mute); font-size: 11px; text-align: center; }}

  @media (max-width: 900px) {{
    .overview {{ grid-template-columns: repeat(2, 1fr); }}
    .bar-row {{ grid-template-columns: 160px 1fr 60px; }}
    .bar-row .bar-files, .bar-row .bar-size {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>PTY-Agent 代码规模报告</h1>
    <div class="subtitle">各模块文件数、代码行数与体积对比</div>
    <div class="meta">统计范围：项目主模块（已排除 wezterm 上游 / third_party 等外部目录）· 代码行数仅计入源码文件</div>
  </header>

  <div class="overview">
    {overview_cards}
  </div>

  <section>
    <h2>顶层模块对比（按代码行数）</h2>
    <div class="sec-desc">横向条形长度按各模块最大行数归一化；右侧依次为占比、文件数、磁盘大小</div>
    <div class="panel">
      {top_bars}
    </div>
  </section>

  <section>
    <h2>src 子模块对比（按代码行数）</h2>
    <div class="sec-desc">核心服务 src/ 目录下的各功能模块</div>
    <div class="panel">
      {sub_bars}
    </div>
  </section>

  <section>
    <h2>src/web/static 子模块对比（按代码行数）</h2>
    <div class="sec-desc">前端静态资源：样式表、前端脚本（js 下含洋葱架构四层）及根入口文件</div>
    <div class="panel">
      {web_bars}
    </div>
  </section>

  <section>
    <h2>文件类型分布（Top 20 扩展名）</h2>
    <div class="panel">
      {ext_bars}
    </div>
  </section>

  <section>
    <h2>Top 15 最大文件（按行数）</h2>
    <div class="panel">
      <table>
        <thead>
          <tr><th>#</th><th>文件路径</th><th>所属模块</th><th style="text-align:right">行数</th></tr>
        </thead>
        <tbody>
          {top_file_rows}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>各模块详情</h2>
    <div class="details-grid">
      {detail_cards}
    </div>
  </section>

  <section>
    <h2>外部参考 / 第三方（不计入主统计）</h2>
    <div class="ext-note">以下为项目中包含的上游开源项目源码或第三方库，仅作参考，未计入上方主统计</div>
    <div class="panel external-panel">
      {external_bars}
    </div>
  </section>

  <footer>
    由 analyze.py + gen_html.py 自动生成 · 数据文件 _analysis.json
  </footer>
</div>
</body>
</html>
"""

OUT.write_text(HTML, encoding="utf-8")
print(f"已生成 {OUT}")
print(f"大小: {fmt_bytes(OUT.stat().st_size)}")
