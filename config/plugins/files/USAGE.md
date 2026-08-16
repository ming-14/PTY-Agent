# file 插件使用说明

`file` 是守护进程侧的**进程级插件**（位于 `config/plugins/files/`），提供文件工具命令：读、写、唯一匹配替换、内容搜索、文件名匹配、上传、下载。与项目内置的文件工具语义一致，使用方式与常规文件操作工具相同，但需要注意路径解析与写保护状态机两条规则。

## 命令总览

```bash
python app.py file <read|write|edit|grep|glob|upload|download> ... -s <session-id>
```

`-s/--cwd-session` **必填**：指定某个会话，取它的 cwd 作为路径解析基准（不操作该会话）。

| 子命令 | 用法 | 要点 |
| ------ | ---- | ---- |
| `file read <path> [--offset N] [--limit N]` | 读文件（带行号，默认 2000 行） | 超过 250KB / 图片拒绝；不存在时提示相似文件名；`--offset` 0-based |
| `file write <path> --content TEXT \| --content-file FILE` | 覆盖写/新建（自动建父目录） | **已存在文件必须先 `file read`**；外部修改后拒绝；内容相同拒绝；大文件用 `--content-file`（与 `--content` 互斥） |
| `file edit <path> --old TEXT \| --old-file FILE [--new TEXT \| --new-file FILE]` | 唯一匹配替换 | `--old` 空=新建（文件须不存在）；`--new` 空=删除；`--old` 须唯一匹配（未找到/重复均拒绝） |
| `file grep <pattern> [path] [--include GLOB] [--literal-text]` | 内容搜索 | rg 引擎优先，缺失自动降级纯 Python；`path` 缺省=会话 cwd |
| `file glob <pattern> [path]` | 文件名匹配 | rg 引擎优先，缺失自动降级纯 Python；支持 `**` 任意层级；`path` 缺省=会话 cwd |
| `file upload <local-path> <remote-path> [--force] [--timeout N]` | 上传本地文件/目录到会话侧（scp -r 语义） | `local-path` 为 CLI 本机路径，`remote-path` 由 daemon 按会话 cwd 解析（支持 `~`）；目标已存在且相同→跳过，不同→拒绝并提示 `--force`；`--timeout` 为整个传输总时限（默认 120s），超时中止并清理临时文件 |
| `file download <remote-path> <local-path> [--force] [--timeout N]` | 下载会话侧文件/目录到本地（scp -r 语义） | 与 upload 反向；`remote-path` 可为文件或目录；覆盖策略与 `--timeout` 同 upload |

## 路径规则

- 相对路径基于 `-s` 会话的 cwd 拼接
- `~` 按 daemon 用户展开
- 绝对路径原样使用
- cwd 是会话创建时的值，shell 内 `cd` 后不更新
- 跨机场景（CLI 与 daemon 异机）语义依然正确——路径在 daemon 所在机器上解析

## 写保护状态机（read-before-write）

`file write` / `file edit` 受读前写保护：

- 文件已存在时必须**先 `file read`**（成功后记录读时刻）
- 写/编辑时若文件 mtime 晚于读时刻（期间被外部修改）→ 拒绝并提示
- 内容与现有内容相同 → 拒绝
- 状态在守护进程进程内保存，重启守护进程即失效

## 多行/含特殊字符内容必须分两步

输入带换行、`\`、`"`、`'` 等字符的内容时，**必须**先调用本地 write 工具写中转文件，再使用 `--content-file` / `--old-file` / `--new-file` 传入——一次调用两个工具（write + file）。原因：

1. Shell 复杂转义
2. 命令行参数有长度上限

## 使用示例

```bash
# 先拉起一个会话作为 cwd 基准
python app.py exec sid_cwd -c "cmd" --cwd <path>

# 读 / 搜索 / 匹配
python app.py file read src/main.py -s sid_cwd --limit 50
python app.py file grep "def " src -s sid_cwd --include *.py
python app.py file glob "src/**/*.py" -s sid_cwd

# write 两步法
# （第一步：用本地 write 工具写好内容）
python app.py file write out.txt -s sid_cwd --content-file tempfiles/_write_temp1.txt

# edit 三步法
# （第一步：用本地 write 工具写好 old 文本）
# （第二步：用本地 write 工具写好 new 文本）
python app.py file edit src/main.py -s sid_cwd --old-file tempfiles/_editold_temp1.txt --new-file tempfiles/_editnew_temp1.txt

# 上传 / 下载
python app.py file upload ./local.txt remote_dir/ -s sid_cwd
python app.py file download remote_dir/local.txt ./local.txt --force -s sid_cwd
```
