# 文件传输设计（file upload / file download）

> 状态：设计待确认（Phase 0）
> 适用范围：`src/files/transfer/`、`src/protocol/transfer.py`、`src/config/files.toml`、CLI 子命令、`daemon/handlers/file_upload_handler.py` / `file_download_handler.py`、`client/transport.py`
> 关联文档：[files-tools.md](files-tools.md)、[ARCHITECTURE.md](../ARCHITECTURE.md)

---

## 1. 背景与目标

pty-agent 现有 `file read/write/edit/grep/glob` 五工具均基于 JSON 行消息协议，受 `MAX_MESSAGE_LENGTH` 限制（且 base64 有 33% 开销），无法承载大文件。目标新增 **upload / download** 文件传输能力：

| 需求项 | 决策（已与用户对齐） |
|--------|---------------------|
| 方向 | 双向：upload = CLI 本机 → daemon 侧（按会话 cwd 落盘）；download = daemon 侧 → CLI 本机 |
| 通道 | 复用现有 TCP 连接：握手走 JSON 消息（沿用签名/认证），握手后切换**二进制分块帧**传输（无 base64 开销） |
| 范围 | 单文件 + 目录递归（含空目录） |
| 完整性 | 每文件传输后 SHA256 校验，失败中止并清理 |
| 覆盖策略 | 目标已存在且判定"相同"→ 跳过；判定"不同"→ 拒绝并提示 `--force` 覆盖 |
| 相同判定 | **大小 + mtime 映射**：daemon 持久记录"远端路径 → (cli_size, cli_mtime, remote_mtime)"，两端均未变即相同 |
| 进度显示 | TTY：实时进度条（`\r` 刷新，百分比+速度+当前文件）；非 TTY：每 60s 强制打印一行；最后汇总 |
| 状态机 | upload 不受 read-before-write 限制（与 scp/rsync 同理）；文本文件落 history 版本链 + 状态机双刷；二进制跳过 |
| 中断安全 | 写 `<目标>.pty-tmp` 临时文件，完整校验通过后 rename 原子落盘；中断/失败自动清理 |
| 断点续传 | 不做（本次范围外） |

---

## 2. 命令设计

```
pty-agent file upload   <local-path> <remote-path> [--force] [--timeout N] -s SID
pty-agent file download <remote-path> <local-path> [--force] [--timeout N] -s SID
```

| 项 | 语义 |
|----|------|
| `local-path` | CLI 本机路径，**CLI 侧**解析（abspath + expanduser），可为文件或目录 |
| `remote-path` | daemon 侧路径，**daemon 侧**按 `-s` 会话 cwd 解析（`resolve_session_path`，与 file 工具一致）；可为文件或目录 |
| `--force` | 目标已存在且内容不同时允许覆盖 |
| `--timeout N` | **整个传输命令的总时限（秒）**，默认 120；超时中止传输并清理临时文件 |
| 目录 | local/remote 同为目录（或同为文件），按相对路径树递归传输 |

TCP type：`file_upload_start` / `file_download_start`（握手），传输阶段走二进制帧。

---

## 3. 目标架构

```
src/protocol/transfer.py       # ═══ 二进制帧编解码（零业务，最底层） ═══
│   recv_frame(sock) / send_frame(sock, ftype, payload)
│   帧格式: [4B payload_len 大端][1B type][payload]

src/files/transfer/            # ═══ 传输业务（两端共用） ═══
├── __init__.py                # 聚合导出
├── map.py                     # TransferMap：mtime 映射表（SQLite，history.db 新表 transfer_map）
├── judge.py                   # 相同判定/计划生成纯函数（classify / build_plan）
├── scan.py                    # 目录树扫描（全量传输，不过滤隐藏/忽略项；scp -r 语义）
├── common.py                  # 帧类型常量、传输错误、进度信息结构
├── client_upload.py           # CLI 侧上传驱动（握手→清单→逐文件→进度）
├── client_download.py         # CLI 侧下载驱动
├── daemon_upload.py           # daemon 侧上传接收（落盘→校验→rename→history→映射）
└── daemon_download.py         # daemon 侧下载发送（扫描→逐文件发送）

daemon/handlers/file_upload_handler.py    # 薄封装：握手校验 → daemon_upload
daemon/handlers/file_download_handler.py  # 薄封装：握手校验 → daemon_download
```

依赖方向：

```
daemon/handlers/ ──> files/transfer/（daemon 侧驱动）
client/ ──> files/transfer/（client 侧驱动）──> protocol/transfer.py（帧 IO）
files/transfer/ 不依赖 daemon/、client/、session/、web/
```

> 约定扩展：现有"client/ 不 import files/"的约定在传输场景放开（files/ 仍不反向依赖 client/，依赖单向）。CLI 侧需要扫描/判定/进度业务，复制到 client/ 会破坏"业务全部在 files/"的既有结构。

---

## 4. 二进制帧协议

### 4.1 帧格式

```
[4B payload_len（大端，不含 type 字节）][1B frame_type][payload]
```

| frame_type | 方向 | payload | 用途 |
|------------|------|---------|------|
| `0x01 DATA` | CLI→daemon（upload）/ daemon→CLI（download） | 原始字节（≤ TRANSFER_CHUNK_SIZE） | 文件数据块 |
| `0x02 FILE_END` | 同 DATA | JSON：`{"relpath","sha256","size","mtime"}` | 单文件数据结束 + 校验值 |
| `0x03 ACK` | 反向 | JSON：`{"relpath","ok","error"?}` | 单文件结果（每文件一次往返） |
| `0x04 MANIFEST` | CLI→daemon（upload）/ daemon→CLI（download 也可） | JSON：`{"entries":[{relpath,kind,size,mtime},...]}` | 文件清单（单帧，上限 TRANSFER_MAX_CONTROL） |
| `0x05 PLAN` | 判定方 → 传输发起方 | JSON：`{"transfers":[...],"skips":[{"relpath","reason"}],"mkdirs":[...]}` | 传输计划（跳过/需要传输/需建目录） |
| `0x06 ABORT` | 双向 | JSON：`{"reason"}` | 中止传输 |

- payload_len 上限：DATA 帧 = `TRANSFER_CHUNK_SIZE`；控制帧 = `TRANSFER_MAX_CONTROL`（16MB，清单上限防御）
- 帧读取从 `Message._recv_buffers` 取残留缓冲（握手后可能已预读二进制字节，协议正确性要求），不足时 `sock.recv` 补齐

### 4.2 握手（JSON，走现有 Message 机制，签名/认证不变）

```
upload:  CLI → daemon: {"type":"file_upload_start","path":remote,"force":bool,"cwd_session":sid}
         daemon → CLI: {"commandType":"file_upload_start","ok":true,"remote_path":...}
                       | {"commandType":"file_upload_start","ok":false,"error":...}  # 路径非法/会话无效等
download: 对称，type=file_download_start；ok 响应带 "kind":"file|dir"、"entries"（daemon 扫描远端树）
```

- 握手错误（cwd_session 无效、路径超长、远端不存在等）**不进入二进制阶段**，直接 JSON 错误返回
- 握手响应后 CLI 才发二进制帧 → daemon 的 recv 缓冲在进入二进制读前无残留（时序保证），`recv_frame` 仍统一处理残留以保正确

### 4.3 传输流程（upload，单文件与目录统一）

```
1. CLI 扫描本地（单文件 → entries=[1项]；目录 → 递归树）得到 entries（含 size/mtime）
2. 握手 file_upload_start（JSON，仅路径+force+kind）→ daemon 校验路径/会话 → ok
3. CLI → MANIFEST 帧（全部 entries）→ daemon 对每个条目 classify → PLAN 帧
4. CLI 按 PLAN 逐条目：
   - mkdir：建目录（含空目录）
   - transfer：DATA 帧流 → FILE_END(sha256) → daemon 校验 → rename → 落 history → ACK
   - skip：不传
5. CLI 发收尾 JSON（file_upload_end 不需要——PLAN 执行完毕即结束），daemon 汇总（由最后一个 ACK 携带或 CLI 本地汇总）
```

download 对称：握手 → daemon 扫描远端树（MANIFEST 帧由 daemon→CLI）→ CLI 本地判定（需要 daemon 映射表，故**判定在 daemon**：CLI 的 MANIFEST 带本地 exists/size/mtime → daemon classify → PLAN）→ daemon 逐文件发送 → CLI 校验/rename/`os.utime` → ACK。

### 4.4 相同判定（judge.py，核心算法）

```
classify(remote_stat, rec, cli_size, cli_mtime, force) -> "transfer" | "skip" | "denied"

- 远端不存在            → transfer
- force                 → transfer（强制覆盖；"相同"判定不受 force 影响，相同仍 skip）
- 大小不同              → denied（提示 --force）
- 映射表有 rec 且
    rec.cli_size == cli_size 且 rec.cli_mtime == cli_mtime   （CLI 文件未变过）
    且 remote_stat.st_mtime == rec.remote_mtime              （远端文件未被外部修改）
      → skip（相同文件，不重传）
- 其余                  → denied
```

映射表（map.py，SQLite，`~/.pty-agent/history.db` 新表）：

```sql
CREATE TABLE IF NOT EXISTS transfer_map (
    path TEXT PRIMARY KEY,
    cli_size INTEGER NOT NULL,
    cli_mtime REAL NOT NULL,
    remote_mtime REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

- 每路径一行（UPSERT），仅存最近一次传输
- **upload 落盘后**：`os.utime(远端, cli_mtime)`（远端 mtime 对齐 CLI）→ UPSERT `(远端路径, cli_size, cli_mtime, remote_mtime=cli_mtime)`
- **download 落盘后**：CLI `os.utime(本地, remote_mtime)` → UPSERT `(远端路径, cli_size=本地size, cli_mtime=remote_mtime, remote_mtime)`
- 持久化原因：daemon 重启不丢，避免"重启后全部重传"

### 4.5 落盘与安全（daemon_upload / client_download）

- 写 `<目标> + TRANSFER_TMP_SUFFIX`（随机后缀防并发冲突），逐块 `file.write`（文件对象缓冲）
- 全部 DATA 接收完 → 校验 SHA256（增量 `hashlib.update`，逐块计算）：
  - 匹配 → `os.replace` 原子落盘 → mtime 对齐 → history/状态机 → ACK ok
  - 不匹配 → 删除 tmp → ACK error → 对端发 ABORT 中止整个传输
- 任何异常/断连 → 清理 tmp 文件（finally），不留半文件
- 目录条目按 PLAN 顺序，mkdir 先于子文件
- 权限：落盘前调 `PermissionPolicy.check(action, path)`（与 writer 一致）

### 4.6 history 与状态机（仅 upload，daemon 侧）

- 写前尝试读旧内容（UTF-8）；rename 后新内容可 UTF-8 解码时落版本链（时序同 writer._commit_write）：
  - `get_latest` 无记录 → `create(path, 旧内容或"")`
  - 历史最新 ≠ 磁盘旧内容 → `create_version(旧内容)`
  - `create_version(新内容)`
- 新内容非 UTF-8（二进制）→ 跳过 history，记日志
- 状态机：`store.record_write(path)` + `record_read(path, 文件mtime)`（与 writer 双刷一致，后续 file edit 语义不变）
- download 不落 history/状态机（CLI 本地无此机制）

### 4.7 进度显示（client 侧）

- 输出到 **stderr**（不污染 stdout 的 JSON 响应）
- TTY（`sys.stderr.isatty()`）：`\r` 刷新单行：`[3/12] file.log 42.3% 1.2MB/s 512KB/1.2MB`
- 非 TTY：每 `TRANSFER_PROGRESS_INTERVAL`（60s）打印一行（含已传/总量），不刷行
- 传输结束 `print_response` 输出汇总 JSON：`{transferred, skipped, denied, failed, total_size, duration}`

### 4.8 超时（--timeout，默认 120s）

- `--timeout N` 是**整个传输命令的总时限**（含握手 + 清单/计划 + 全部文件传输），由 CLI 侧强制：
  - `deadline = monotonic() + N`；每帧读写前 `sock.settimeout(max(0, deadline - now))`
  - 超时 → `socket.timeout` → 发 ABORT（尽力）→ 本地清理 tmp → 报错 "transfer timed out after Ns"
- daemon 侧无需独立超时参数：CLI 超时断开后，daemon 的 recv 得到连接关闭 → 清理 tmp（中断安全路径兜底）；daemon 侧同时保留既有 socket 超时防自身挂死

---

## 5. 配置（files.toml 新增）

```toml
[files]
TRANSFER_CHUNK_SIZE = 262144       # 传输数据帧大小（256KB）
TRANSFER_MAX_FILES = 100000        # 单次传输条目数上限（目录树防御）
TRANSFER_MAX_CONTROL = 16777216    # 控制帧 payload 上限（16MB，清单/计划）
TRANSFER_MAX_SIZE = 0              # 单文件大小上限；0 = 无限制
TRANSFER_TMP_SUFFIX = ".pty-tmp"   # 传输临时文件后缀
TRANSFER_PROGRESS_INTERVAL = 60    # 非 TTY 强制进度打印间隔（秒）
```

---

## 6. 分 Phase 实现计划

### Phase 1：协议与基础
- `src/protocol/transfer.py`：帧编解码 + `recv_frame`/`send_frame`（残留缓冲处理）
- `src/files/transfer/map.py` + `judge.py` + `common.py` + `scan.py`
- `src/config/files.toml` 新配置项 + `files.py` 加载
- 测试：帧编解码（边界：零长度/超限/粘包拆包）、judge 全分支、map 持久化、scan 树结构

### Phase 2：upload 链路
- `client_upload.py` + `daemon_upload.py` + `file_upload_handler.py`
- CLI：`file upload` parser + `cmd_file_upload`
- 测试：单测（握手校验/判定/落盘/校验失败/ABORT）+ e2e（真实 daemon：小文件/大文件/目录递归/跳过/force/拒绝/二进制/文本落 history/中断清理）

### Phase 3：download 链路
- `client_download.py` + `daemon_download.py` + `file_download_handler.py`
- CLI：`file download` parser + `cmd_file_download`
- 测试：单测 + e2e（对称场景全覆盖）

### Phase 4：进度 + 回归 + 文档
- TTY/非 TTY 进度实现与测试
- 文档：ARCHITECTURE.md / filestree / SKILL.md / CLI.md / README
- 全量回归 + e2e 冒烟

> e2e 限制：沙箱 PTY 无法起真实会话（既有 6.2 限制），`-s` 会话链路以 handler 单测（FakeManager mock）+ CLI→daemon 错误路径覆盖；真实会话 e2e 视环境而定，必要时用户协助起会话。

---

## 7. 风险与注意

1. **mtime 映射误判**：CLI 文件内容变但 mtime/大小均未变（如同步工具保留时间戳）→ 误判相同跳过。用户指定方案，固有局限，文档注明
2. **客户端与 daemon 版本不一致**：旧 daemon 不认识 `file_upload_start` → 返回未知命令错误，提示需重启 daemon（既有升级流程）
3. **无断点续传**：中断后重新执行命令，已相同文件自动跳过（等价于部分续传）
4. **控制帧单帧限制**：目录条目超 `TRANSFER_MAX_CONTROL`（约 16MB 清单）→ 报错提示分批；不做分帧重组（过度工程）
5. **传输语义全量**：目录递归不过滤隐藏/忽略项（scp -r 语义），与 grep/glob 搜索的忽略清单无关
6. **并发传输**：同一 daemon 多连接可并发传输不同文件；同路径并发靠 tmp 随机后缀 + rename 原子性兜底，不做锁（与 writer 同风险等级）
7. **Python 3.8 兼容**：`os.replace` 3.3+、`os.utime` OK；无 `pathlib.walk`

---

## 8. 验收标准

- [ ] `file upload` / `file download` 支持单文件与目录递归，双向可用
- [ ] 相同文件（大小+mtime 映射命中）不重传；daemon 重启后映射仍在
- [ ] 目标存在且不同 → 默认拒绝提示 `--force`；`--force` 覆盖
- [ ] 每文件 SHA256 校验；校验失败中止并清理 tmp，不留半文件
- [ ] `--timeout` 总时限生效：超时中止并清理，默认 120s
- [ ] upload 文本文件落 history 版本链 + 状态机双刷；二进制跳过
- [ ] TTY 实时进度条 / 非 TTY 定时打印 + 最终汇总 JSON
- [ ] 全部 pytest + e2e 通过，无行为回归
