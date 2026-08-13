# 文件工具设计（File Tools）

> 状态：全部完成（Phase 1-6 + 6.1 `--content-file` + 6.2 `--cwd-session`，见 §6）
> 适用范围：`src/files/` 新包、`config/files.toml`、CLI 子命令、`daemon/handlers/`、`client/transport.py`
> 关联文档：[ARCHITECTURE.md](../ARCHITECTURE.md)、[gaps-analysis.md](../gaps-analysis.md)、[files-transfer.md](files-transfer.md)（file upload/download 传输设计）
> 参考实现：opencode Go 版（`internal/llm/tools/write.go` / `edit.go` / `view.go` / `grep.go` / `glob.go` / `file.go`）

---

## 1. 背景与目标

pty-agent 目前只有 PTY 会话能力，缺乏文件读写/搜索工具。目标是在既有 CLI 架构（CLI → Client → Listener → Dispatcher → Handler）上新增一组文件工具命令，核心机制参考 opencode Go 版。**大文件双向传输（`file upload` / `file download`）不属于本文范围**，见 [files-transfer.md](files-transfer.md)：两者基于独立二进制帧协议，不受 JSON 消息长度限制。

| 机制 | opencode 出处 | 本项目取舍 |
|------|--------------|-----------|
| read-before-write 状态机 | `tools/file.go`（readTime/writeTime + modTime 冲突检测） | **保留** |
| rg 双引擎 + 纯 Python 降级 | `grep.go:186-193`、`glob.go:129-141` | **保留**（bin/rg 已就位） |
| 历史版本（SQLite） | `history/`（每写一次插一行，initial→v1→v2） | **保留，后台实现，暂不呈现 CLI** |
| diff 展示 | `diff/diff.go:850`（unified + 行统计） | **保留，后台实现，暂不呈现 CLI** |
| 权限确认 | `permission/`（弹窗 + 会话级记住） | **保留机制，暂不呈现 CLI**（自动策略代替弹窗，见 §4.6） |

**"暂不呈现 CLI"含义**：机制照常实现（写操作内部生成 diff、落库历史、过权限检查），但 CLI 响应 JSON 不携带 diff 字段、不弹权限确认窗、不提供历史查询命令——后续阶段再加呈现层。

---

## 2. 命令设计

`read` 已被会话输出读取占用，文件工具采用 **`file` 父子命令 + 子命令**形式（D1 已定）：

```
pty-agent file    <read|write|edit|grep|glob|upload|download> ... -s <session-id>   # -s 必填，取会话 cwd 为路径基准
pty-agent file read  <path> [--offset N] [--limit N] -s SID
pty-agent file write <path> --content TEXT | --content-file FILE -s SID
pty-agent file edit  <path> [--old TEXT | --old-file FILE] [--new TEXT | --new-file FILE] -s SID
pty-agent file grep  <pattern> [path] [--include GLOB] [--literal-text] -s SID
pty-agent file glob  <pattern> [path] -s SID
pty-agent file upload   <local-path> <remote-path> [--force] [--timeout N] -s SID   # 见 files-transfer.md
pty-agent file download <remote-path> <local-path> [--force] [--timeout N] -s SID   # 见 files-transfer.md
```

| 子命令 | 用途 | 对应 opencode |
|--------|------|--------------|
| `file read` | 读文件内容（带行号） | view |
| `file write` | 覆盖写/新建文件（自动建父目录） | write |
| `file edit` | 唯一匹配替换；`--old` 空=新建；`--new` 空=删除 | edit |
| `file grep` | 内容搜索（rg 优先） | grep |
| `file glob` | 文件名匹配（rg --files 优先） | glob |
| `file upload` | 上传本地文件/目录到会话侧（scp -r 语义，二进制帧） | —（详见 files-transfer.md） |
| `file download` | 下载会话侧文件/目录到本地（scp -r 语义，二进制帧） | —（详见 files-transfer.md） |

TCP type 字段与子命令一一对应：`file_read` / `file_write` / `file_edit` / `file_grep` / `file_glob`；`file upload` / `file download` 握手走 `file_upload_start` / `file_download_start`（JSON），传输阶段切换二进制帧。`__main__.py` 中注册 `file` 父 parser（`add_subparsers(dest="file_subcmd")`），子命令 parser 复用 `_add_common_args`。

**路径约定**：
- 相对路径在 **CLI 侧**解析为绝对路径再传输（D2 已定：用户心智与所在目录一致，daemon 侧只处理绝对路径）
- 支持 `~` 展开（`os.path.expanduser`，参照 `auth/keys.py` 惯例）
- Git-Bash 风格路径防护复用 `handlers/utils.py:44-60` 的 `has_git_bash_style_path` 提示

---

## 3. 目标架构

```
src/files/                        # ═══════ 文件工具用例层（按工具域分组） ═══════
├── __init__.py                   # 聚合导出工具函数集合
├── paths.py                      # 路径工具：会话 cwd 解析（resolve_session_path）/边界判定/git-bash 检测
├── state.py                      # 读写状态机：FileRecordStore（readTime/writeTime）
├── diff.py                       # unified diff 生成 + additions/removals 统计
├── history.py                    # FileHistoryStore（SQLite 版本链）
├── permission.py                 # 权限检查器（后台策略，暂不呈现 CLI）
├── errors.py                     # 工具异常类型（冲突/权限/未读等）
├── read/
│   └── reader.py                 # file read 用例：大小/行数限制、行号输出、图片检测
├── write/
│   └── writer.py                 # file write/file edit 用例：状态机检查→diff→权限→落盘→history
├── search/
│   ├── grep.py                   # file grep 用例：rg 引擎 + 纯 Python 降级
│   ├── glob_.py                  # file glob 用例：rg --files + fnmatch 降级
│   └── ignore.py                 # SkipHidden 过滤（隐藏文件 + 忽略目录清单）
└── transfer/                     # ═══ file upload/download 传输业务（详见 files-transfer.md） ═══
    ├── common.py                 # 帧协议常量/错误类型
    ├── client_upload.py          # CLI 侧上传驱动（握手→清单→逐文件→进度）
    ├── client_download.py        # CLI 侧下载驱动
    ├── daemon_upload.py          # daemon 侧上传接收（落盘→校验→rename→history→映射）
    └── daemon_download.py        # daemon 侧下载发送（扫描→逐文件发送）
```

依赖方向（外层 → 内层，内层不依赖框架）：

```
daemon/handlers/ ──> files/（read/、write/、search/、transfer/ 用例）
client/ ──> transfer/ 传输场景放开（files-transfer.md §1），其余仍不 import files/
files/ 不依赖 daemon/、client/、session/、web/（仅 config/common.py 的 IS_WINDOWS/DATA_DIR）
```

handler 只做：参数校验（`validate_request`）、组装响应 dict；业务全部在 `src/files/`。

---

## 4. 核心机制设计

### 4.1 读写状态机（state.py）——仿 opencode file.go

```python
class FileRecordStore:
    """进程内记录每个文件的 readTime/writeTime（daemon 侧，内存 map + 锁）"""
    def record_read(self, path: str) -> None ...
    def record_write(self, path: str) -> None ...
    def last_read(self, path: str) -> datetime | None ...   # None = 从未读过
```

流转表：

| 操作 | 前置检查 | 成功后更新 |
|------|---------|-----------|
| file read | 存在/非目录/≤MAX_READ_SIZE/非图片 | readTime=now（分段读也刷新） |
| file write（已存在） | `modTime > lastRead` 拒绝；内容相同拒绝 | write+read 双刷 |
| file write（新文件） | 无检查（自动建父目录） | write+read 双刷 |
| file edit replace/delete | 未读过拒绝；`modTime > lastRead` 拒绝；old 唯一（Index==LastIndex） | write+read 双刷 |
| file edit create | 文件必须不存在 | write+read 双刷 |

判定逻辑：`last_read is None or modTime > last_read` → 拒绝，提示先 `file read`。
`writeTime` 本期只记录不消费（与 opencode 同款，预留检测"写后未读再写"）。

### 4.2 file read（reader.py）

- 输出带行号（`%6d|` 格式，仿 view.go:199-222），默认 2000 行，超长行截断（2000 字符）
- 限制：MAX_READ_SIZE（250KB）、图片检测（png/jpg/gif/bmp/svg/webp）
- 响应：`{commandType:"file_read", path, content(带行号), size, truncated}`
- 文件不存在时给出同目录相似文件名建议（仿 view.go:120-141）

### 4.3 file write / file edit（writer.py）

- 内部顺序：状态机检查 → `diff.GenerateDiff` → `permission.check` → `os.makedirs+write` → `FileHistoryStore` 落版本 → 记录状态机
- 权限被拒 / 状态机冲突 → 返回 `Response.error`，不落盘
- 历史时序（仿 opencode write.go:191-211）：`GetLatest(path)` 失败则 `Create(旧内容)`；历史最新内容 ≠ 磁盘内容则 `CreateVersion(旧内容)`（用户手改的中间版本）；再 `CreateVersion(新内容)`
- **修正 opencode 已知 bug**（opencode write.go:200 首次写入会冗余存两份相同旧内容）：`GetLatest` 失败路径跳过中间版本判定
- file edit 三分支与 opencode edit.go 相同；old_string 必须唯一（`str.find` == `str.rfind`）

### 4.4 file grep（grep.py）——rg 双引擎

```
引擎1: bin/rg/rg.exe -H -n [--glob include] pattern path
       → 解析 "file:line:content"，os.stat 取 modTime 排序（最新优先）
引擎2（降级）: os.walk + 逐行 regex，SkipHidden 过滤，收集满 200 条提前停
```

- `literal_text`：标准库转义（`re.escape`，修正 opencode 手写转义不严谨问题）
- 结果限 `MAX_GREP_MATCHES`（100），超限打 truncated 标记
- rg 缺失或退出码非 0/1 时降级（rg 退出码 1 = 无匹配，合法空结果）

### 4.5 file glob（glob_.py）

```
引擎1: bin/rg/rg.exe --files -L --null [--glob pattern]（cwd=搜索根）
引擎2（降级）: pathlib.walk + fnmatch 路径匹配（** 用递归实现），SkipHidden 过滤
```

- 结果限 `MAX_GLOB_FILES`（100）
- **修正 opencode 不一致**（glob.go rg 引擎按路径长度排序、fallback 按 modTime 排序）：两引擎统一按 modTime 排序

### 4.6 权限（permission.py）——后台机制，暂不呈现 CLI

```python
class PermissionPolicy:
    """D3 已定：当前仅保留判断接口，直接放行（return True）。

    后续呈现层（前端弹窗/会话级记住）只需替换 check 实现，
    writer 的调用点不变。
    """
    def check(self, action: str, path: str) -> bool:
        return True
```

- 接口与 `daemon/handlers/`、Web 层解耦；后续呈现层只需替换 `check` 实现
- 路径边界判定（`path == root or path.startswith(root + os.sep)`）与状态机检查同样保留在 writer 内（opencode 用裸 `HasPrefix` 会把 `proj2` 误判进 `proj`，本期不引入该逻辑，待呈现层落地时实现）

### 4.7 历史（history.py）——SQLite

- 复用 `~/.pty-agent/history.db`（`config/common.py:DATA_DIR`），新表：

```sql
CREATE TABLE IF NOT EXISTS files_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(path, version)
);
CREATE INDEX IF NOT EXISTS idx_files_history_path ON files_history(path);
```

- 版本链：`initial` → `v1` → `v2`（按 path 取 MAX(version) 递增，同 opencode 语义）
- 线程安全（`threading.Lock`，仿 `web/.../history_store.py` 连接模式）；`:memory:` 支持测试
- 不提供查询命令（暂不呈现 CLI），但表结构为后续 `file-history` 命令预留

### 4.8 diff（diff.py）

- 标准库 `difflib.unified_diff`（header 仿 udiff 格式 `--- a/...` / `+++ b/...`）
- `generate_diff(before, after, path) -> (text, additions, removals)`：逐行数 `+`/`-`（排除 header 行）
- 本期产物只供 permission 记录与内部日志，不进 CLI 响应

---

## 5. 配置（files.toml）

仿 `config/sandbox.py` 模式新建 `config/files.py`：

```toml
[files]
MAX_READ_SIZE = 262144        # 250KB
DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_PATH_LEN = 4096
MAX_CONTENT_LEN = 1048576     # write/edit 内容上限
MAX_GREP_MATCHES = 100
MAX_GLOB_FILES = 100
RG_EXE = ""                   # 空 = 自动探测 bin/rg/rg.exe
IGNORED_DIRS = [".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__"]
```

- 冲突注意：`_loader.py:38-46` 跨 TOML 同名 key 冲突会抛 ValueError，键名带唯一性
- `RG_EXE` 为空时自动探测 `PROJECT_ROOT/bin/rg/rg.exe` + PATH
- 忽略目录清单与 opencode fileutil.go:83-104 对齐

---

## 6. 分 Phase 实现计划

> 每个 Phase 独立可验证（pytest 通过后才进入下一 Phase）。测试跟随代码迁移。

### Phase 1：基础设施
- 新建 `src/files/` 包：`paths.py`、`state.py`（FileRecordStore）、`errors.py`、`__init__.py`（ignored filter 后在 search/ 包落位）
- 新建 `config/files.toml` + `config/files.py`（加载模板）
- 测试：`tests/unit/files/test_state.py`、`test_paths.py`（ignore 测试随 search/ 迁移至 `tests/unit/files/search/`）
- 验收：pytest 全量通过

### Phase 2：file read ✅
- `src/files/read/reader.py`（行号/分段/图片检测/相似名建议）✅
- CLI 链路：`__main__.py` 注册 `file read` → `transport.py:cmd_file_read` → `daemon/handlers/file_read_handler.py` → dispatcher 注册 ✅
- 测试：`tests/unit/files/read/test_reader.py`（23）、`tests/unit/daemon/test_file_read_handler.py`（8）、`tests/unit/test_file_cli.py`（6）全过 ✅
- 验收：pytest 通过 ✅ + e2e 冒烟通过 ✅（注意旧 daemon 需先 stop 再启动才会加载新 dispatcher）

### Phase 3：file write（含 diff/权限/历史后台机制）✅
- `src/files/diff.py` ✅（SequenceMatcher opcodes 统计，规避 difflib 无换行行粘连标记误统计）
- `src/files/history.py` ✅（SQLite 版本链；version 整数文本递增，`CAST(version AS INTEGER)` 取 MAX，规避字符串排序 v9>v10 问题；`:memory:` 持久连接）
- `src/files/permission.py` ✅（D3 放行）
- `src/files/write/writer.py` ✅：write 路径（状态机 → diff → 权限 → 落盘 → 历史 → 双刷）
- CLI 链路：`file write` 命令 + handler + client 方法 ✅
- 测试：`test_diff.py`（4）、`test_history.py`（6）、`test_permission.py`（2）、`tests/unit/files/write/test_writer.py`（11）、`test_file_write_handler.py`（7）、CLI parser/transport 补充 ✅
- 验收：pytest 通过 ✅ + e2e 通过 ✅（新文件/自动建父目录/覆盖成功/未读拒绝/外部修改拒绝/history.db 版本链落库验证）

### Phase 4：file edit ✅
- `write/writer.py` 重构出公共提交路径 `_commit_write`（diff→权限→落盘→历史→双刷），write/edit 共用 ✅
- `write/writer.py` 增加 `edit_file` 三分支（create/replace/delete，唯一匹配校验）✅
- CLI 链路：`file edit` 命令 + handler + client 方法 ✅
- 测试：`tests/unit/files/write/test_edit.py`（13，含唯一匹配/未读拒绝/外部修改/权限/版本链）、`test_file_edit_handler.py`（4）、CLI parser/transport 补充 ✅
- 状态机加固：write/edit 成功后 readTime 改用文件自身 mtime 为基准（state.record_read 加 `at` 参数），消除 python 时钟与文件系统时钟微秒偏差导致的自写误判 flaky ✅
- 验收：pytest 通过 ✅ + e2e 通过 ✅（替换/删除/新建/已存在拒绝/非唯一拒绝）

### Phase 5：file grep / file glob ✅
- `src/files/search/grep.py` ✅：rg 引擎（`-H -n --no-heading` + `--glob`，退出码 0/1 合法，其余降级；rg 输出从右侧解析冒号，Windows 盘符不干扰）+ 降级引擎（os.walk + 逐行 regex + SkipHidden + include 过滤，满上限提前停）；literal_text 用 `re.escape`；两引擎统一按 modTime 最新优先
- `src/files/search/glob_.py` ✅：rg 引擎（`--files -L --null`，cwd=搜索根，`\x00` 分隔解析）+ 降级引擎（os.walk + 逐段递归 glob 匹配：`**` 支持 0 层、`*` 不跨 /、无 / 的 pattern 前置 `**/` 对齐 rg gitignore 语义；修正 opencode 两引擎排序不一致——统一按 modTime）
- CLI 链路：`file grep` / `file glob` 命令 + handler ×2 + client 方法（当时 path 缺省按 CLI 当前目录解析，6.2 起改为会话 cwd）+ dispatcher 注册 ✅
- 测试：`tests/unit/files/search/test_grep.py`（14，含模组解析/降级/截断/排序/引擎降级）、`test_glob.py`（12）、handler（5）、CLI parser/transport（8）✅
- 验收：pytest 通过 ✅ + e2e 通过 ✅（rg 引擎真实运行：grep 匹配带行号、glob `**/` 全深度、literal-text；降级路径由 mock 覆盖）

### Phase 6：回归 + 文档 ✅
- 更新 `docs/ARCHITECTURE.md`（§3 src/ 树加 files/ 包、config 树加 files.py/files.toml、src 树加 handlers 树 file_* 5 个、daemon 模块表加 file_* handler 行）✅
- 更新 `docs/filestree/src.md`（handlers/ 段 + 新增 files/ 包段 + config 段补 files.py/files.toml）✅
- 更新 `SKILL.md`（命令速查表加 file 行 + 新增 `file 用法` 小节：子命令表格/示例/写保护说明）✅
- 更新 `docs/CLI.md`（目录 + 子命令一览表 + 新增 §4.15 file 小节：5 子命令用法/选项/示例/注意事项）✅
- 更新 `README.md` 命令概览表加 file 行 ✅
- 全量回归：pytest 258 passed / 7 failed（pre-existing，见下）✅ + e2e 冒烟五命令全过（read→edit→glob→grep→write 真实链路）✅
- python 3.8 兼容：全仓 259 处 `pathlib.walk` 相关代码为 pre-existing `os.walk` 用法，新增代码均为 3.8 兼容 ✅
- 验收：文档与真实文件系统/命令行为一致 ✅
- **注意**：`tests/unit/daemon/test_handler.py` 7 个失败为 pre-existing（test_unknown_command / test_auth_failure / test_build_result_with_session / TestSnapshotReadLines×4，git checkout HEAD~1 复现），与 file 工具无关

### Phase 6.1：--content-file / --old-file / --new-file 增强 ✅
- 背景：Windows 命令行长度受限（cmd 8191 / CreateProcess 32767），`--content` 无法承载大文件内容
- 设计（与用户对齐）：对称命名——write 的 `--content` 与 `--content-file` 互斥；edit 的 `--old-file`/`--new-file` 与 `--old`/`--new` 各自二选一
- 实现：`__main__.py:_resolve_cli_content`（inline 与 file 互斥校验、空串视为未提供、UTF-8 读取失败报错）→ 内容读取后走既有 cmd_file_write/cmd_file_edit 链路，状态机/唯一匹配/版本链机制零改动 ✅
- **CRLF 规范化决策**：`--*-file` 读取后 `\r\n` → `\n`，对齐 daemon universal newlines 视图（file read 显示 / edit 匹配均不含 `\r`）；e2e 首次验证发现 `--old-file` 携带 `\r\n` 与内部 `\n` 视图不匹配报 "Old string not found"——LOCAL 修复后通过 ✅
- 测试：`test_file_cli.py` 追加 TestResolveCliContent 7 用例（互斥/空串取文件/缺失文件/GBK 报错/CRLF 规范化/None 保留）+ parser 2 用例 ✅；全量回归 254 passed（+7 pre-existing）✅；e2e 通过（write --content-file → read → edit --old-file/--new-file 全链路 + 互斥报错）✅
- 文档：CLI.md §4.15、SKILL.md file 用法、README 无改动（命令面不变）✅
- 边界注意：`--old ""` 与 `--old-file f` 同时给视为"未提供 inline"取文件；write 未给任何内容保持 daemon 侧 "content is required" 报错

### Phase 6.2：--cwd-session 会话路径基准（跨机语义修复）✅
- 背景：CLI 侧 `resolve_path`（本机 abspath/expanduser）在跨机场景（CLI 与 daemon 异机，pubkey 认证）会把 Linux 机器上的 `/etc/passwd` 解析成 Windows 盘符路径，语义错位
- 设计（用户指定）：所有 file 子命令新增必填 `-s/--cwd-session <session-id>`，取该会话 cwd 作为路径解析基准（不操作该会话）
- 实现：CLI path 原样传输（不再 resolve_path）；daemon 侧 `utils.get_session_cwd`（会话不存在/无 cwd 报错）→ `paths.resolve_session_path`（`~` 按 daemon 用户展开、绝对路径原样、相对路径 join 会话 cwd 后 normpath）；grep/glob 的 path 缺省 = 会话 cwd，不再缺省报错 ✅
- 注意：会话 cwd 为创建时值（session.cwd 属性），shell 内 cd 后不更新（Windows 无 /proc/cwd 追踪），文档已注明 ✅
- 测试：handler ×4 文件换 FakeManager mock（无 cwd_session / 未知会话 / 相对路径按会话 cwd 解析 / grep 缺省会话 cwd），test_file_cli.py parser（-s 必填/short flag）+ transport（path 原样 + cwd_session 字段）✅；全量回归 259 passed / 7 pre-existing ✅
- e2e 限制：本环境沙箱 PTY 起不了真实会话（"等待 ProcessStarted 超时"），会话链路降级验证——CLI→daemon 错误路径（-s 必填报错、session not found）通过，完整解析语义由 handler 单测覆盖
- 文档：CLI.md §4.15、SKILL.md file 用法全部加 -s 说明与路径规则 ✅

---

## 7. 风险与注意

1. **状态机是进程内的**：daemon 重启即失效（与 opencode 同款，可接受；不落盘避免过度工程）
2. **daemon 侧绝对路径**：CLI 已解析为绝对路径，daemon 不做相対路径猜测；`~` 展开在 CLI 侧完成
3. **权限自动策略**（D3 未定前）：默认项目根内放行；若需目录外写入，Phase 3 前必须先定策略
4. **`PYTHON 3.8` 兼容**：type hints 用 `Optional`/`from __future__ import annotations`（有需要则加）；`pathlib.walk` 不存在——降级 glob 用 `os.walk`
5. **rg 降级行为差异**：grep 降级按行 regex 匹配（无多行模式），与 rg 语义基本一致；glob 降级不支持 `**` 透传 fnmatch 语义，用递归模拟
6. **非 UTF-8 文件**：file read 尝试 UTF-8 解码失败时返回错误提示（不自动猜测编码，避免与会话编码探测机制耦合；可后续扩展）
7. **并发写**：状态机检查与落盘非原子（检查→写之间另有进程改动文件时 modTime 仍为旧值），与 opencode 同等风险，本期不引入文件锁

---

## 8. 验收标准

- [x] `src/files/` 成为完整文件工具用例层，无 daemon/client/session 反向依赖
- [x] `file read`/`file write`/`file edit`/`file grep`/`file glob` 五命令可用，e2e 冒烟通过
- [x] 已存在文件未经 `file read` 时 `file write`/`file edit` 拒绝，提示先读；读后写入成功
- [x] 外部修改文件（modTime 更新）后写入被拒
- [x] 每次写入在 `files_history` 落版本链（initial→v1→v2），用户手改后有中间版本
- [x] rg 缺失时 grep/glob 降级路径可用（pytest 覆盖）
- [x] diff/permission 后台机制实现且不在 CLI 响应中呈现（无 diff 字段、无弹窗）
- [x] 全部 pytest + e2e 通过，无行为回归（258 passed；7 failed 为 pre-existing test_handler.py）