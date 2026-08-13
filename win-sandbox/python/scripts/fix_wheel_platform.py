"""Wheel 构建后修正平台标记：py3-none-any → py3-none-win_amd64。"""
import os
import sys
import zipfile
import shutil
import glob

DIST_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dist")
TARGET_PLATFORM = "win_amd64"


def fix_wheel_platform(wheel_path: str) -> str:
    """修正 wheel 的平台标记。

    Args:
        wheel_path: 原始 wheel 路径（如 dist/win_sandbox-0.1.0-py3-none-any.whl）

    Returns:
        修正后的 wheel 路径
    """
    if not wheel_path.endswith("-py3-none-any.whl"):
        print(f"wheel 不是 py3-none-any: {wheel_path}")
        return wheel_path

    # 新文件名
    new_path = wheel_path.replace("-py3-none-any.whl", f"-py3-none-{TARGET_PLATFORM}.whl")

    # 如果目标已存在，先删除
    if os.path.exists(new_path):
        os.remove(new_path)

    # 修改 WHEEL 元数据中的 Tag
    tmp_dir = wheel_path + ".tmp"
    try:
        with zipfile.ZipFile(wheel_path, "r") as zin:
            zin.extractall(tmp_dir)

        # 动态定位 dist-info 目录（目录名含版本号，不可硬编码），获取所有匹配项
        wheel_meta_paths = glob.glob(os.path.join(tmp_dir, "*.dist-info", "WHEEL"))
        for wheel_meta_path in wheel_meta_paths:
            if os.path.exists(wheel_meta_path):
                with open(wheel_meta_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = content.replace(
                    "Tag: py3-none-any",
                    f"Tag: py3-none-{TARGET_PLATFORM}",
                )
                with open(wheel_meta_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"已更新 WHEEL 元数据: Tag → py3-none-{TARGET_PLATFORM}")

        # 重新打包
        with zipfile.ZipFile(new_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for root, dirs, files in os.walk(tmp_dir):
                for fn in files:
                    abs_path = os.path.join(root, fn)
                    arcname = os.path.relpath(abs_path, tmp_dir)
                    zout.write(abs_path, arcname)

        print(f"已创建平台专用 wheel: {os.path.basename(new_path)}")
    finally:
        # 清理临时目录
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)

    return new_path


def main():
    wheels = glob.glob(os.path.join(DIST_DIR, "*.whl"))
    if not wheels:
        print(f"错误: dist 目录未找到 wheel 文件: {DIST_DIR}")
        sys.exit(1)

    for w in wheels:
        fix_wheel_platform(w)


if __name__ == "__main__":
    main()