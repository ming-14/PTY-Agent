from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

from common import DEFAULT_CONFIG


def _set_yaml_value(content: str, key: str, value: str) -> str:
    patterns = {
        "model": r'^(model:)[ \t]*\S*',
        "prompt": r'^(prompt:)[ \t]*.*$',
        "timeout": r'^(timeout:)[ \t]*\S*',
        "type": r'^([ \t]*-[ \t]*type:)[ \t]*\S*',
        "name": r'^([ \t]+name:)[ \t]*\S*',
        "api_base": r'^([ \t]+api_base:)[ \t]*\S*',
        "baseurl": r'^([ \t]+api_base:)[ \t]*\S*',
        "api_key": r'^([ \t]+api_key:)[ \t]*\S*',
    }
    if key not in patterns:
        return content
    # 函数式替换：value 含 \ / $ 等字符时不会被当作反向引用/转义解析
    new_content = re.sub(
        patterns[key],
        lambda m: m.group(1) + " " + value,
        content, count=1, flags=re.MULTILINE,
    )
    if new_content == content:
        lines = content.splitlines()
        if key in ("model", "prompt", "timeout"):
            lines.insert(0, f"{key}: {value}")
        else:
            lines.append(f"    {key}: {value}")
        new_content = "\n".join(lines) + "\n"
    return new_content


def cmd_init_config(config_path: str) -> int:
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    if not os.path.exists(config_path):
        example_path = config_path + ".example"
        if os.path.exists(example_path):
            shutil.copy2(example_path, config_path)
            print(f"Created {config_path} (from template)")
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write("model: \n")
                f.write("prompt: 全面分析该内容，只按内容说话，不给出下一步，不提建议\n")
                f.write("timeout: 120\n")
                f.write("clients:\n")
                f.write("  - type: \n")
                f.write("    name: \n")
                f.write("    api_base: \n")
            print(f"Created {config_path}")

    return 0


def cmd_show_config(config_path: str) -> int:
    print(f"Config file: {config_path}")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            print(f.read())
    else:
        print("(not found)")

    return 0


def cmd_set_config(config_path: str, pairs: list[tuple[str, str]]) -> int:
    for key, value in pairs:
        if key in ("model", "type", "name", "api_base", "baseurl", "apikey", "prompt", "timeout"):
            yaml_key = "api_base" if key == "baseurl" else "api_key" if key == "apikey" else key
            if key == "timeout" and not value.isdigit():
                print(f"timeout 必须为正整数，收到: {value}", file=sys.stderr)
                return 1
            if not os.path.exists(config_path):
                print(f"Config file not found: {config_path}", file=sys.stderr)
                return 1
            with open(config_path, encoding="utf-8") as f:
                content = f.read()
            content = _set_yaml_value(content, yaml_key, value)
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Updated {yaml_key} in {config_path}")
        else:
            print(f"Unknown config key: {key}. Use: model, type, name, api_base, baseurl, apikey, prompt, timeout", file=sys.stderr)
            return 1
    return 0


def run_config(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Show or modify configuration")
    parser.add_argument("--init", action="store_true", help="Initialize config files with templates")
    parser.add_argument("--show-config", action="store_true", help="Show current configuration")
    parser.add_argument("--set-config", action="append", nargs=2, metavar=("KEY", "VALUE"), help="Set config key=value (can be used multiple times). Keys: model, prompt, timeout, type, name, api_base, baseurl, apikey")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.init:
        return cmd_init_config(args.config)
    ret = 0
    if args.set_config:
        ret = cmd_set_config(args.config, args.set_config)
    if args.show_config:
        cmd_show_config(args.config)
    return ret


if __name__ == "__main__":
    sys.exit(run_config(sys.argv[1:]))
