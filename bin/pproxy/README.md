# Encrypted SOCKS5 Proxy Server (pproxy)

Cross-platform (Windows / Linux / macOS) encrypted SOCKS5 proxy server built on
the [pproxy](https://pypi.org/project/pproxy/) library, using the standard
Shadowsocks (`ss://`) protocol with AEAD encryption.

## Install

Works on Windows, Linux and macOS with any Python >= 3.8:

```bash
python -m pip install -r requirements.txt
```

`uvloop` is only pulled in on Linux (`sys_platform == "linux"`) for extra
asyncio performance. On Windows/macOS it is skipped and plain asyncio is used.
`pycryptodome` provides the fast C implementation of the AES ciphers.

## Configure

Edit `config.json`:

```json
{
  "host": "0.0.0.0",
  "port": 8388,
  "cipher": "aes-256-gcm",
  "password": "REPLACE_WITH_A_STRONG_PASSWORD",
  "blocked": []
}
```

- `host` / `port`: where the server listens.
- `cipher`: encryption algorithm. Safe AEAD choices: `aes-256-gcm`,
  `aes-192-gcm`, `aes-128-gcm`, `chacha20-ietf-poly1305`.
- `password`: shared secret; client must use the same one. Avoid `:` and `@`
  in the password because it is embedded in the `ss://` URI.
- `blocked`: optional list of regex rules to block target hostnames.

## Start

```bash
python start.py                          # uses config.json
python start.py -p mypassword            # override password
python start.py --port 8388 --cipher aes-256-gcm -p mypassword
python start.py -h                       # all options
```

## Wire format (encryption)

The listening port speaks the Shadowsocks protocol (`ss://`). Connection bytes
are encrypted frames (AEAD / stream ciphers) derived from the shared password.
Plaintext SOCKS5 framing is not visible on the wire, so a passive sniffer
cannot read or detect the proxied traffic as plain SOCKS5.

## Client side

Connect from any Shadowsocks-capable client (Windows/Linux/macOS). With the
bundled pproxy CLI (installed alongside pproxy) you can expose a local plain
SOCKS5 on `127.0.0.1:8080` that tunnels through the encrypted server:

```bash
pproxy -r ss://aes-256-gcm:PASSWORD@SERVER_IP:8388 -l socks5://127.0.0.1:8080 -vv
```

Then point your browser/app at SOCKS5 `127.0.0.1:8080`.

本目录未附带测试客户端；用上述 pproxy CLI（或任意 `ss://` 客户端）即可联调验证。