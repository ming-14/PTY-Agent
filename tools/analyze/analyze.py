"""统计各模块代码规模，输出 JSON 数据供 _gen_html.py 生成可视化报告。"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# 项目根目录：本脚本位于 <root>/tools/analyze/，向上两级
ROOT = Path(__file__).resolve().parents[2]
# 数据产物放脚本同目录
SCRIPT_DIR = Path(__file__).resolve().parent

# 顶层模块定义：目录名 + 中文描述
TOP_MODULES: dict[str, str] = {
    "src": "核心服务 (Python)",
    "fastscreen": "快速截屏 (C++)",
    "web_rime": "Web 输入法 (Rime)",
    "wezterm-py": "WezTerm 终端集成",
    "win-sandbox": "Windows 沙箱 (C++)",
    "tests": "测试套件",
    "docs": "文档",
    "config": "配置",
    "bin": "可执行/脚本",
    ".agents": "Agent 配置",
}

# 外部参考/第三方目录（单独标注，不计入主统计）
EXTERNAL_MODULES: dict[str, str] = {
    "wezterm-py/wezterm": "wezterm Rust 源码 (上游)",
    "win-sandbox/third_party": "win-sandbox 第三方库",
    "src/web/static/vendor": "Web 前端第三方库",
}

# 完全忽略的目录（不统计、不展示）
IGNORE_PATHS: set[str] = {
    "reference",
    ".cli-test",
}

# src 子模块描述
SRC_SUBMODULES: dict[str, str] = {
    "auth": "认证授权",
    "client": "客户端",
    "config": "配置管理",
    "daemon": "守护进程",
    "encoding": "编码处理",
    "cli": "命令行接口",
    "common": "通用工具",
    "config": "配置管理",
    "daemonctl": "守护进程控制",
    "encoding": "编码处理",
    "input": "输入处理",
    "ipc": "进程间通信",
    "logging": "日志系统",
    "output": "输出处理",
    "plugins": "插件系统",
    "process": "进程管理",
    "protocol": "协议层",
    "pty": "PTY 核心",
    "sandbox": "沙箱集成",
    "screenshare": "屏幕共享",
    "session": "会话管理",
    "terminal": "终端模拟",
    "transfer": "文件传输",
    "vnc": "VNC 远程桌面",
    "web": "Web 服务",
    "workflow": "工作流",
}

# src/web/static 子模块描述（前端资源）
WEB_SUBMODULES: dict[str, str] = {
    "css": "样式表",
    "js": "前端脚本 (洋葱架构)",
}

# 代码文件扩展名
CODE_EXTS = {
    ".py", ".pyi",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".rst",
    ".ps1", ".sh", ".bat", ".cmd",
    ".cmake", ".def",
    ".txt", ".gitignore", ".gitattributes",
    ".lua", ".rs", ".go", ".java", ".kt",
}

# 仅统计代码（不含文档/配置）的扩展名，用于"代码行数"指标
PURE_CODE_EXTS = {
    ".py", ".pyi",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".less",
    ".ps1", ".sh", ".bat", ".cmd",
    ".lua", ".rs", ".go", ".java", ".kt",
}

# 跳过的目录
SKIP_DIRS = {
    "__pycache__", ".pytest_cache", ".ruff_cache", ".git",
    "node_modules", "build", "target", "dist", ".venv", "venv",
    "env", ".env", ".mypy_cache", ".tox", ".eggs", "eggs",
}

# 二进制文件扩展名（计入文件数与大小，但不计入代码行数）
BINARY_EXTS = {".dll", ".exe", ".so", ".dylib", ".lib", ".a", ".obj", ".o", ".pyd", ".pdb"}

# 额外计入的代码扩展名（Rust 等）
CODE_EXTS |= {".rs", ".lock"}
PURE_CODE_EXTS |= {".rs"}


@dataclass
class ModuleStat:
    name: str
    desc: str
    files: int = 0
    code_files: int = 0
    total_bytes: int = 0
    code_lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0
    by_ext: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    top_files: list[tuple[str, int]] = field(default_factory=list)  # (相对路径, 行数)

    def add_file(self, path: Path, size: int) -> None:
        ext = path.suffix.lower() or "(无扩展名)"
        self.files += 1
        self.total_bytes += size
        self.by_ext[ext] += 1

        if ext not in PURE_CODE_EXTS:
            return

        self.code_files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return

        lines = text.splitlines()
        self.code_lines += len(lines)
        # 粗略统计空行/注释行
        for ln in lines:
            s = ln.strip()
            if not s:
                self.blank_lines += 1
            elif s.startswith("#") or s.startswith("//") or s.startswith("/*") or s.startswith("*"):
                self.comment_lines += 1
        self.top_files.append((str(path.relative_to(ROOT)), len(lines)))


def walk_module(root: Path, name: str, desc: str, exclude: set[str] | None = None) -> ModuleStat:
    stat = ModuleStat(name=name, desc=desc)
    exclude = exclude or set()
    if not root.exists():
        return stat
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath).resolve()
        # 相对于项目根的路径（用于匹配 IGNORE_PATHS）
        try:
            rel_to_proj = cur.relative_to(ROOT.resolve())
            rel_to_proj_str = rel_to_proj.as_posix()
        except ValueError:
            rel_to_proj_str = ""
        # 原地修改 dirnames 以跳过
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        # 排除指定子目录（仅对 root 的直接子目录生效）
        if cur == root_resolved:
            dirnames[:] = [d for d in dirnames if d not in exclude]
        # 跳过任意深度的忽略目录与外部目录（外部目录仅在 ext_stats 单独统计）
        skip_paths = IGNORE_PATHS | set(EXTERNAL_MODULES.keys())
        dirnames[:] = [
            d for d in dirnames
            if f"{rel_to_proj_str}/{d}" not in skip_paths
        ]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            ext = p.suffix.lower()
            if ext in CODE_EXTS or not ext:
                stat.add_file(p, size)
    stat.top_files.sort(key=lambda x: x[1], reverse=True)
    stat.top_files = stat.top_files[:10]
    return stat


def main() -> None:
    # 顶层模块
    top_stats: list[ModuleStat] = []
    module_excludes: dict[str, set[str]] = {
        "wezterm-py": {"wezterm"},
        "win-sandbox": {"third_party"},
    }
    for dirname, desc in TOP_MODULES.items():
        top_stats.append(walk_module(ROOT / dirname, dirname, desc, module_excludes.get(dirname)))

    # 根目录散落文件
    root_stat = ModuleStat(name="(根目录)", desc="根目录文件")
    for p in ROOT.iterdir():
        if p.is_file():
            ext = p.suffix.lower()
            if ext in CODE_EXTS or not ext:
                root_stat.add_file(p, p.stat().st_size)
    root_stat.top_files.sort(key=lambda x: x[1], reverse=True)
    root_stat.top_files = root_stat.top_files[:10]
    top_stats.append(root_stat)

    # src 子模块
    src_sub_stats: list[ModuleStat] = []
    src_root = ROOT / "src"
    for sub in sorted(src_root.iterdir()):
        if not sub.is_dir() or sub.name in SKIP_DIRS:
            continue
        desc = SRC_SUBMODULES.get(sub.name, sub.name)
        src_sub_stats.append(walk_module(sub, f"src/{sub.name}", desc))

    # src 根文件
    src_files_stat = ModuleStat(name="src/(根文件)", desc="src 根文件")
    for p in src_root.iterdir():
        if p.is_file():
            ext = p.suffix.lower()
            if ext in CODE_EXTS or not ext:
                src_files_stat.add_file(p, p.stat().st_size)
    src_files_stat.top_files.sort(key=lambda x: x[1], reverse=True)
    src_files_stat.top_files = src_files_stat.top_files[:10]
    src_sub_stats.append(src_files_stat)

    # src/web/static 子模块（前端资源）
    web_sub_stats: list[ModuleStat] = []
    web_root = ROOT / "src" / "web" / "static"
    skip_paths = IGNORE_PATHS | set(EXTERNAL_MODULES.keys())
    for sub in sorted(web_root.iterdir()):
        if not sub.is_dir() or sub.name in SKIP_DIRS:
            continue
        rel = f"src/web/static/{sub.name}"
        if rel in skip_paths:
            continue
        desc = WEB_SUBMODULES.get(sub.name, sub.name)
        web_sub_stats.append(walk_module(sub, f"src/web/static/{sub.name}", desc))

    # src/web/static 根文件
    web_files_stat = ModuleStat(name="src/web/static/(根文件)", desc="static 根文件")
    for p in web_root.iterdir():
        if p.is_file():
            ext = p.suffix.lower()
            if ext in CODE_EXTS or not ext:
                web_files_stat.add_file(p, p.stat().st_size)
    web_files_stat.top_files.sort(key=lambda x: x[1], reverse=True)
    web_files_stat.top_files = web_files_stat.top_files[:10]
    web_sub_stats.append(web_files_stat)

    # 外部参考模块（单独列出，不混入主统计）
    ext_stats: list[ModuleStat] = []
    for relpath, desc in EXTERNAL_MODULES.items():
        ext_stats.append(walk_module(ROOT / relpath, relpath, desc))

    # 序列化
    def to_dict(s: ModuleStat) -> dict:
        return {
            "name": s.name,
            "desc": s.desc,
            "files": s.files,
            "code_files": s.code_files,
            "total_bytes": s.total_bytes,
            "code_lines": s.code_lines,
            "blank_lines": s.blank_lines,
            "comment_lines": s.comment_lines,
            "by_ext": dict(sorted(s.by_ext.items(), key=lambda x: -x[1])),
            "top_files": [{"path": fp, "lines": ln} for fp, ln in s.top_files],
        }

    data = {
        "top": [to_dict(s) for s in top_stats],
        "src_sub": [to_dict(s) for s in src_sub_stats],
        "web_sub": [to_dict(s) for s in web_sub_stats],
        "external": [to_dict(s) for s in ext_stats],
    }

    out = SCRIPT_DIR / "_analysis.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out}")
    print(f"顶层模块数: {len(top_stats)}")
    print(f"src 子模块数: {len(src_sub_stats)}")
    total_lines = sum(s.code_lines for s in top_stats)
    total_files = sum(s.files for s in top_stats)
    print(f"总文件数: {total_files}")
    print(f"总代码行数: {total_lines}")


if __name__ == "__main__":
    main()
