import argparse
import asyncio
import json
import pathlib
import re

import pproxy

BASE_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8388,
    "cipher": "aes-256-gcm",
    "password": "",
    "blocked": [],
}

CIPHERS = [
    "aes-256-gcm", "aes-192-gcm", "aes-128-gcm",
    "aes-256-ctr", "aes-256-cfb", "aes-192-cfb", "aes-128-cfb",
    "chacha20", "chacha20-ietf", "chacha20-ietf-poly1305",
    "rc4-md5", "salsa20",
]


def load_config(path):
    cfg = dict(DEFAULTS)
    if pathlib.Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def build_uri(cfg):
    host = str(cfg["host"])
    listen = f"{host}:{cfg['port']}" if host else f":{cfg['port']}"
    return f"ss://{cfg['cipher']}:{cfg['password']}@{listen}"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Encrypted SOCKS5 proxy server (pproxy, ss:// cipher). Cross-platform: Windows / Linux / macOS."
    )
    parser.add_argument("-c", "--config", default=str(CONFIG_FILE),
                        help="JSON config file (default: config.json next to this script)")
    parser.add_argument("--host", help="listen address (default from config)")
    parser.add_argument("--port", type=int, help="listen port (default from config)")
    parser.add_argument("--cipher", choices=CIPHERS, help="encryption cipher (default from config)")
    parser.add_argument("-p", "--password", help="shared password used to derive the encryption key")
    return parser.parse_args()


def resolve(cfg, args):
    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port
    if args.cipher:
        cfg["cipher"] = args.cipher
    if args.password:
        cfg["password"] = args.password
    if not cfg["password"]:
        raise SystemExit("password required: set it in config.json or pass --password")
    return cfg


def build_args(cfg):
    block = None
    patterns = cfg.get("blocked")
    if patterns:
        block = re.compile("(" + "|".join(patterns) + ")$").match
    return {"rserver": [], "ruport": False, "block": block}


async def serve(cfg):
    uri = build_uri(cfg)
    option = pproxy.Server(uri)
    server = await option.start_server(build_args(cfg))
    print(f"[pproxy] listening: {uri}")
    for sock in server.sockets:
        laddr = sock.getsockname()
        print(f"[pproxy] bound to {laddr[0]}:{laddr[1]}")
    try:
        await asyncio.Event().wait()
    finally:
        server.close()
        await server.wait_closed()


def main():
    args = parse_args()
    cfg = resolve(load_config(args.config), args)
    asyncio.run(serve(cfg))


if __name__ == "__main__":
    main()