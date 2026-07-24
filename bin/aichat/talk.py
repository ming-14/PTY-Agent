import argparse
import base64
import glob as globmod
import os
import sys

from common import (
    DEFAULT_CONFIG,
    check_config, run_aichat,
)


def sessions_dir() -> str:
    if "AICHAT_SESSIONS_DIR" in os.environ:
        return os.environ["AICHAT_SESSIONS_DIR"]
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "aichat", "sessions")


def cmd_clear(args: argparse.Namespace) -> int:
    sdir = sessions_dir()
    if not os.path.isdir(sdir):
        sys.stderr.write(f"Sessions directory not found: {sdir}\n")
        return 1

    pattern = args.name
    if "*" not in pattern and "?" not in pattern:
        pattern = f"*{pattern}*"

    files = globmod.glob(os.path.join(sdir, f"{pattern}.yaml"))
    if not files:
        sys.stderr.write(f"No sessions match '{args.name}' in {sdir}\n")
        return 1

    for f in files:
        sname = os.path.splitext(os.path.basename(f))[0]
        if args.yes:
            confirm = "y"
        else:
            confirm = input(f"Delete session '{sname}'? [y/N] ").strip().lower()
        if confirm == "y":
            os.remove(f)
            print(f"Deleted session '{sname}'")
        else:
            print(f"Skipped session '{sname}'")

    return 0


def cmd_talk(args: argparse.Namespace) -> int:
    err = check_config(args.config)
    if err:
        sys.stderr.write(err + "\n")
        return 1

    prompt = args.prompt
    if prompt.startswith("base64:"):
        prompt = base64.b64decode(prompt[len("base64:"):]).decode("utf-8")

    aichat_args = ["-f", args.file]
    if args.session:
        aichat_args += ["--session", args.session, "--save-session"]
    aichat_args.append(prompt)

    return run_aichat(
        aichat_args,
        config=args.config,
        timeout=args.timeout,
        no_think=args.no_think,
    )


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "clear":
        parser = argparse.ArgumentParser(description="Clear aichat sessions")
        parser.add_argument("--name", required=True, help="Session name (supports * ? wildcards)")
        parser.add_argument("--yes", action="store_true", help="Skip confirmation")
        return cmd_clear(parser.parse_args(argv[1:]))

    parser = argparse.ArgumentParser(description="Analyze content using aichat")
    parser.add_argument("-f", "--file", required=True, help="File to include (passed to aichat --file)")
    parser.add_argument("--prompt", required=True, help="Conversation content (supports base64: prefix)")
    parser.add_argument("--session", help="Session name (creates or resumes a session)")
    parser.add_argument("--no-think", action="store_true", help="Strip <think> block from output")
    parser.add_argument("--timeout", type=int, default=40, help="Timeout in seconds (default: 40)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"Path to config.yaml (default: {DEFAULT_CONFIG})")
    return cmd_talk(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
