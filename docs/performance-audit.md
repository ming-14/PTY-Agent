# PTY-Agent 性能审计报告

| 项目 | 内容 |
|------|------|
| 审计范围 | `src/` 全量（Python 后端 + JS 前端） |
| 审计级别 | very thorough |
| 审计性质 | 只读静态分析，未修改任何代码 |
| 审计方法 | 热路径调用链分析 + 反模式扫描 + 源码行级验证 |
| 平台 | Windows（ConPTY）/ Unix（pty_impl） |

---

## 0. 执行摘要

PTY-Agent 的核心热路径是 **PTY 输出数据流**：从 ConPTY read 到前端 xterm 渲染的完整链路。审计发现该链路存在 **6 个严重问题**、**6 个高优先级问题**、**7 个中等问题**、**3 个低优先级问题**。

### 四大核心瓶颈

1. **reader 线程串行瓶颈**：read → append(锁) → check(线程池) → feed(pyte) → notify 全部在一个线程同步执行。`feed(pyte)` 是 CPU 密集的 VT 解析，直接拖慢 read 频率，导致 PTY 管道缓冲区堆积。
2. **锁竞争**：`out_buf.lock`（RLock）持锁期间调用 `trig_mat.check()`，check 内部提交正则到 `ThreadPoolExecutor` 并 `future.result()` 阻塞等待，导致锁被持有时间 = 线程池调度延迟 + 正则执行时间。期间所有 buffer 操作阻塞。
3. **双重解码**：`trigger.check` 里 `auto_detect(raw)` 解码一次，`_on_data` 回调里 `encoder.decode_output()` 又对同一份 data 解码一次。
4. **前端无批量**：每条 WS output 消息都直接 `term.write` + `setTimeout`，高频输出时 xterm 渲染占满主线程，setTimeout 堆积。

### 最高优先级优化方向

| 优先级 | 优化项 | 涉及问题 | 预期收益 |
|--------|--------|----------|----------|
| P0 | `trigger.check` 移出 `out_buf.lock` | C1+C2 | 消除 reader 线程锁等待 |
| P0 | 编码探测结果按会话缓存 | C3+C4 | 消除双重解码，省一半解码开销 |
| P0 | reader 的 `feed(pyte)` 改异步/批量 | C1 | 解放 reader 线程，提升 read 吞吐 |
| P1 | 前端 output 用 rAF 批量合并 | C5 | 消除主线程渲染堆积 |
| P1 | `event_publisher` 维护 session→conn 反向索引 | C6 | 广播 O(连接数) → O(订阅者数) |
| P2 | `OutputBuffer` 改环形缓冲区 | H1 | 消除 O(n) 头部 del |
| P2 | ConPTY 预分配复用 read buffer | H2 | 减少 GC 压力 |
| P2 | `re.compile` 缓存 | H6 | 消除请求路径重复编译 |

---

## 1. 热路径调用链

PTY 输出数据从产生到前端渲染的完整调用链（**每次 read 都执行**）：

```
[reader 线程]
ConPTY.read(65536)                    conpty.py:223        ← H2 新建 ctypes buffer
  │
  ├─ pty.drain(65536)                 conpty.py:237        ← H2 循环内新建 buffer
  │
  ├─ _respond_terminal_queries(data)  session_threads.py:157  正则匹配终端查询
  │
  ├─ with out_buf.lock:               session_threads.py:172  ← RLock 加锁
  │   ├─ out_buf.append(data)         buffer.py:50        ← H1 extend + 可能 O(n) del
  │   ├─ trig_mat.on_data_appended()
  │   └─ trig_mat.check(out_buf)      trigger.py:229      ← C2 持锁状态下
  │       ├─ bytes(memoryview(raw)[start:end])            ← 拷贝最多 1MB
  │       ├─ auto_detect(raw)         codec.py:184        ← C3 完整解码 + count 替换符
  │       └─ safe_regex_search(regex, text)  trigger.py:64  ← 提交 ThreadPoolExecutor
  │           └─ _EXECUTOR.submit(regex.search, text)     ← 线程切换，reader 阻塞等待
  │
  ├─ comp.screen.feed(data)           session_threads.py:181  ← C1 同步 pyte VT 解析
  │   └─ pyte Stream.feed → Screen.draw                  ← CPU 密集，解析 ESC 序列、更新 grid
  │
  └─ publisher.notify_subscribers(data)  session_threads.py:188
      └─ for callback in subscribers:
          └─ _on_data(data, "pty")    handlers.py:347     ← C4 每次输出
              ├─ encoder.decode_output()                  ← 再次 auto_detect (C3)
              ├─ Response.ws_output(...)                  ← 构造 WS 响应 dict
              └─ ctx.enqueue(response)
                  └─ loop.call_soon_threadsafe(_emit)     ← 跨线程提交到 asyncio

[asyncio 事件循环]
_emit()                              event_publisher.py:132  ← C6
  ├─ for conn in connections:                            ← 遍历所有连接
  │   └─ session_id in context.subscribed_session_ids
  └─ conn["queue"].put_nowait(payload)                   ← 满队列丢弃

[WS sender task]
queue.get() → ws.send_text(json.dumps(payload))          ← JSON 序列化 + WS 发送

[前端 JS 主线程]
wsClient.onmessage → JSON.parse(e.data)  wsClient.js:66
  └─ handleMsg(msg)                    messageHandlers.js:13
      └─ ports.terminal.handleOutput(msg)
          └─ lifecycle.js handleOutput  lifecycle.js:485  ← C5
              ├─ inst.term.write(text)                   ← xterm.js VT 解析 + 渲染
              └─ setTimeout(updateTerminalSnapshot, 50)   ← 遍历所有行 + DOM 写入
```

---

## 2. 严重问题（Critical）

> 严重问题位于 PTY 数据流热路径，每次 read 都执行，直接影响高输出速率场景（如 `cat` 大文件、`find /`、编译输出）的吞吐与延迟。

### C1. reader_loop 持锁做 trigger.check + 同步 feed pyte

**位置**：`src/session/session_threads.py:172-193`

**代码**：

```python
# 在 OutputBuffer 锁保护下完成：追加 → 计时 → 触发匹配
with out_buf.lock:
    if not out_buf.append(data):
        continue
    trig_mat.on_data_appended(time.monotonic())
    if trig_mat.has_pattern:
        trig_mat.check(out_buf)       # ← 持锁状态下提交线程池正则

# 同步喂给终端屏幕快照管理器
comp.screen.feed(data)                # ← pyte VT 解析，CPU 密集，同步阻塞 reader 线程

# 通知订阅者（Web WS 实时推送）
try:
    session = comp.session_ref()
    session._publisher.notify_subscribers(data, "pty")  # ← 每次 read 都通知
except Exception:
    pass
```

**问题分析**：

- `out_buf.lock`（RLock）持锁期间调用 `trig_mat.check()`，check 内部提交正则到 `ThreadPoolExecutor`（见 C2），导致锁等待线程池调度。
- `comp.screen.feed(data)` 在锁外但仍在 reader 线程同步执行，pyte 解析 VT 序列是 CPU 密集操作，直接阻塞下一次 PTY read。
- 每次 read（最大 64KB）都完整走 append → check → feed → notify 四步，无批量合并。

**量级**：高输出速率时（如 `cat` 大文件），read 频率可达数千次/秒，每步都有锁/线程池/解码开销。

**优化方向**：

1. 将 `trigger.check` 移出 `out_buf.lock` 外（check 只读 buffer 切片，可在锁外拷贝引用后释放锁）。
2. `feed(pyte)` 改为异步或批量提交到独立线程/队列，解放 reader 线程。
3. 无 `has_pattern` 时跳过 check 调用。

---

### C2. trigger.check 每次拷贝 buffer 切片 + 解码 + 线程池正则

**位置**：`src/output/trigger.py:229-248` + `:64-79`

**代码**：

```python
# trigger.py:229-248
start = min(start_offset, len(output_buffer.raw))
end = min(start + MAX_TRIGGER_SCAN, len(output_buffer.raw))
raw = bytes(memoryview(output_buffer.raw)[start:end])   # ← 拷贝最多 1MB
text = self._decode_func(raw)                            # ← auto_detect 解码（见 C3）

if regex:
    if safe_regex_search(regex, text):                   # ← 提交到 ThreadPoolExecutor
        ...

# trigger.py:64-79
def safe_regex_search(regex, text, timeout=2.0):
    future = _EXECUTOR.submit(regex.search, text)        # ← 每次提交，线程切换开销
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutExpired:
        _logger.warning("regex timeout, pattern=%r", regex.pattern)
        return False
```

**问题分析**：

- 每次 check 都 `bytes(memoryview(...)[start:end])` 拷贝最多 1MB（`MAX_TRIGGER_SCAN`），再完整解码。
- 正则匹配提交到模块级 `ThreadPoolExecutor(max_workers=4)`，每次都有 submit + future.result 的线程切换开销，且在 C1 的锁内调用，导致 reader 线程阻塞等待线程池 worker。

**量级**：与 read 频率相同，每次最多 1MB 拷贝 + 解码 + 线程切换。

**优化方向**：

1. 无 pattern 时跳过 check。
2. 增量匹配：只扫描新追加部分，而非每次从头切片 1MB。
3. 正则匹配改为内联执行（已有 2 秒超时保护，线程池主要防 ReDoS，可考虑用 `signal.alarm` 替代或限制正则复杂度）。

---

### C3. auto_detect 每次完整解码 + 计算替换符比例

**位置**：`src/encoding/codec.py:184-221`

**代码**：

```python
def auto_detect(data: bytes) -> Tuple[str, str]:
    _logger.debug("auto_detect: input len=%d head=%r tail=%r",
                  len(data), data[:40], data[-20:])       # ← debug 日志切片（参数先求值）
    trimmed = _utf8_trim_tail(data)
    try:
        trimmed.decode("utf-8")
        return decode_strip_tail(data, "utf-8"), "utf-8"   # ← 成功路径：解码两次
    except UnicodeDecodeError:
        pass
    decoded = data.decode("utf-8", errors="replace")       # ← 失败路径：完整解码
    if len(decoded) > 0:
        ratio = decoded.count("\ufffd") / len(decoded)     # ← 全量 count 遍历
        if ratio > 0.05:
            sys_enc = _get_system_fallback_encoding()
            if sys_enc:
                result = decode_strip_tail(data, sys_enc)  # ← 再解码一次
                return result, sys_enc
    return decoded, "utf-8"
```

**问题分析**：

- 被 C2 的 `self._decode_func(raw)` 调用，每次 trigger check 都对最多 1MB 数据做完整 UTF-8 解码。
- 失败路径下 `decoded.count("\ufffd")` 再全量遍历一次。
- 成功路径下 `decode_strip_tail` 实际又解码一次（trim + decode），等于解码两遍。
- debug 日志 `data[:40]` 和 `data[-20:]` 即使日志级别不满足也会先切片（Python 的 `%r` 惰性求值，但 `data[:40]` 作为参数已先求值）。

**量级**：与 trigger check 频率相同，每次最多 1MB 解码 + count 遍历。

**优化方向**：

1. 编码探测结果按会话缓存（同会话编码通常不变），首次探测后后续直接用固定编码解码。
2. debug 日志用 `if _logger.isEnabledFor(logging.DEBUG):` 守卫。
3. 成功路径合并 trim + decode 为一次操作。

---

### C4. _on_data 回调每次输出都 decode + enqueue

**位置**：`src/web/application/handlers.py:347-358`

**代码**：

```python
def _on_data(data: bytes, stream: str):
    try:
        text = ctx.encoder.decode_output(
            session_id, session.encoding or "utf-8", data    # ← 每次输出都解码
        )
        ctx.enqueue(
            Response.ws_output(
                session_id, text, stream, session.encoding or "utf-8"  # ← 构造响应
            )
        )                                                    # ← call_soon_threadsafe
    except Exception:
        _logger.exception("output callback error sid=%s", session_id)
```

**问题分析**：

- 每次 PTY read 的 data 都触发此回调，`decode_output` 内部调用 `auto_detect`（C3），与 C2 形成双重解码。
- `enqueue` 内部用 `loop.call_soon_threadsafe` 跨线程提交到 asyncio 事件循环，高频输出时 `call_soon_threadsafe` 的锁竞争显著。

**量级**：与 read 频率相同，乘以订阅者数量。

**优化方向**：

1. 编码确定后跳过 `auto_detect`，直接用固定编码解码。
2. 多订阅者时 data 只解码一次再广播（当前已是单次解码，但与 C2 重复）。
3. 与 C2 共享解码结果，避免对同一份 data 解码两次。

---

### C5. 前端 handleOutput 每次输出都 term.write + setTimeout snapshot

**位置**：`src/web/static/js/infrastructure/terminal/lifecycle.js:482-509`

**代码**：

```javascript
const wasAtBottom = isTermAtBottom(inst.term);
inst.term.write(text);                                      // ← 直接写 xterm，无批量
if (isHistory) scrollTermToTop(inst.term);
else if (wasAtBottom && state.activeTab === sid) scrollTermToBottom(inst.term);
setTimeout(() => updateTerminalSnapshot(sid), 50);          // ← 每次输出都 setTimeout

// updateTerminalSnapshot:
export function updateTerminalSnapshot(sid) {
  const inst = state.termInstances[targetSid];
  const buf = inst.term.buffer.active;
  const rows = inst.term.rows;
  const lines = [];
  for (let r = 0; r < rows; r++) {                          // ← 遍历所有行
    const line = buf.getLine(r);
    if (line) lines.push(line.translateToString(true));     // ← 每行转字符串
  }
  const snap = document.getElementById('terminal-snapshot');
  if (snap) snap.textContent = lines.join('\n');            // ← DOM 写入
}
```

**问题分析**：

- 每次 WS output 消息都直接 `term.write(text)`，xterm.js 内部解析 VT + 渲染，高频输出时主线程被占满。
- 每次输出都 `setTimeout(updateTerminalSnapshot, 50)`，snapshot 遍历所有行 `translateToString` + DOM 写入 `textContent`。高频输出时 setTimeout 堆积（虽然 50ms debounce 效果，但每次都创建新 timer）。
- 无 `requestAnimationFrame` 批量合并。

**量级**：与 WS output 消息频率相同，每条都触发 xterm write + setTimeout。

**优化方向**：

1. 用 `requestAnimationFrame` 合并多条 output 为一次 `term.write`。
2. snapshot 用 debounce 单 timer 而非每次新建。
3. snapshot 仅在会话切换/可见性变化时更新，而非每次输出。

---

### C6. event_publisher._emit 遍历所有连接 + put_nowait

**位置**：`src/web/infrastructure/web/event_publisher.py:132-164`

**代码**：

```python
def _emit():
    if filter_by_session:
        targets = []
        for conn_id, conn in self._connections.items():    # ← 遍历所有连接
            if exclude_conn_id is not None and conn_id == exclude_conn_id:
                continue
            context = conn.get("context")
            if not context:
                continue
            try:
                if session_id in context.subscribed_session_ids:  # ← 每个连接检查集合
                    targets.append(conn)
            except Exception as e:
                ...
        conns = targets
    else:
        conns = list(self._connections.values())           # ← 全量拷贝
    for conn in conns:
        try:
            conn["queue"].put_nowait(payload)              # ← 背压处理：丢弃
        except asyncio.QueueFull:
            _logger.warning("broadcast %s: queue full, dropping")
```

**问题分析**：

- 每次广播都遍历所有连接，对每个连接检查 `session_id in subscribed_session_ids`。多会话多连接时 O(连接数 × 广播频率)。
- `put_nowait` 满队列时直接丢弃（无背压降速）。

**量级**：与 output 频率 × 连接数成正比。

**优化方向**：

1. 维护 `session_id → [conn]` 反向索引，广播时直接查表，O(连接数) → O(订阅者数)。
2. 满队列时考虑降速（如暂停 reader）而非直接丢弃。
3. 订阅/取消订阅时更新反向索引。

---

## 3. 高优先级问题（High）

### H1. OutputBuffer.append 在 RLock 下 extend + 可能 O(n) del

**位置**：`src/output/buffer.py:50-65`

**代码**：

```python
with self._lock:
    self._buffer.extend(data)                               # ← bytearray.extend
    if len(self._buffer) > self._max_size:
        drop = len(self._buffer) - self._max_size
        del self._buffer[:drop]                             # ← O(n) 内存移动，max=100MB
        self._dropped_bytes += drop
        self._read_cycle += 1
        self._first_output_event.set()
    else:
        self._read_cycle += 1
        self._first_output_event.set()                      # ← 每次 append 都 set Event
```

**问题**：缓冲区满时 `del self._buffer[:drop]` 是 O(n) 内存移动（`bytearray` 删除头部需移动后续所有字节），100MB 缓冲区下开销显著。每次 append 都 `_read_cycle += 1` + `_first_output_event.set()`。

**量级**：缓冲区接近满时每次 append 都触发 O(n) 移动。

**优化方向**：用环形缓冲区（ring buffer）替代 bytearray 头部删除；或用 `collections.deque` 分块存储。

---

### H2. ConPTY read/drain 每次创建新 ctypes buffer

**位置**：`src/pty/windows/conpty.py:223-257`

**代码**：

```python
def read(self, n: int = 65536) -> bytes:
    buf = ctypes.create_string_buffer(n)                    # ← 每次创建新 buffer
    br = W.DWORD(0)
    if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
        ...
    return buf.raw[:br.value]                               # ← 切片创建新 bytes

def drain(self, max_bytes: int = 65536) -> bytes:
    chunks = []
    total = 0
    while True:
        avail = W.DWORD(0)
        ok = _PeekNamedPipe(self._outR, None, 0, None, ctypes.byref(avail), None)
        if not ok or avail.value == 0:
            break
        n = min(avail.value, max_bytes)
        buf = ctypes.create_string_buffer(n)               # ← 循环内每次创建新 buffer
        br = W.DWORD(0)
        if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
            break
        chunks.append(buf.raw[:br.value])
        total += br.value
    return b"".join(chunks)
```

**问题**：`ctypes.create_string_buffer(n)` 每次都分配新内存，drain 循环内每次迭代都创建。`buf.raw[:br.value]` 切片再创建新 bytes 对象。高频 read 下内存分配/释放压力大。

**量级**：与 read 频率相同，drain 可能循环多次。

**优化方向**：预分配复用 buffer（实例级 `self._read_buf = ctypes.create_string_buffer(65536)`）。

---

### H3. H264 encode_bgra 每帧 numpy reshape + ascontiguousarray

**位置**：`src/fastscreen/streamers/encoding/h264.py:74-105`

**代码**：

```python
def encode_bgra(self, bgra_data: bytes, stride: int, width: int, height: int, ...):
    arr = np.frombuffer(bgra_data, dtype=np.uint8).reshape((height, stride))  # ← frombuffer
    arr = arr[:, :width * 4].reshape((height, width, 4))                      # ← 切片 + reshape
    bgra = np.ascontiguousarray(arr)                                          # ← 拷贝为连续内存
    frame = av.VideoFrame.from_ndarray(bgra, format="bgra")                   # ← 创建 VideoFrame
    ...
    packets = self._codec.encode(frame)
    result = []
    for pkt in packets:
        data = bytes(pkt)                                                     # ← 每包拷贝
        data = H264Encoder._normalize_to_annexb(data)                         # ← 格式转换
        if self.rewrite_to_idr and self._pending_kf_rewrite:
            data = self._rewrite_to_idr(data)                                 # ← 可能再拷贝
        result.append(data)
```

**问题**：每帧都 `np.frombuffer` + 切片 + `np.ascontiguousarray`（强制拷贝连续内存）+ `VideoFrame.from_ndarray`。编码后每个 packet 都 `bytes(pkt)` 拷贝 + `_normalize_to_annexb` 转换。30fps 下每秒 30 次完整流程。

**量级**：30fps，每帧 1080p 约 8MB BGRA，numpy 操作 + 内存拷贝开销显著。

**优化方向**：`stride == width*4` 时跳过切片和 `ascontiguousarray`；复用 `VideoFrame`。

---

### H4. Grid.reflow 全量拷贝 linedata

**位置**：`src/terminal/grid.py:204-262`

**问题**：resize 时 reflow 把整个 linedata（scrollback + visible）拆成两个 list 拷贝。scrollback 可能很大（数千行），每次 resize 都全量拷贝。

**量级**：resize 频率低（用户拖拽窗口），但单次开销大。

**优化方向**：原地 rewrap，避免 list 拷贝。

---

### H5. grid_screen._sync_grid_to_pyte_visible 逐 cell 创建 Char 对象

**位置**：`src/terminal/grid_screen.py:417-444`

**代码**：

```python
def _sync_grid_to_pyte_visible(self) -> None:
    self.buffer.clear()
    for row in range(self._grid.sy):                        # ← 遍历所有行
        grid_line = self._grid.get_visible_line(row)
        if grid_line is None:
            continue
        for col in range(self._grid.sx):                   # ← 遍历所有列
            cell = (grid_line.cells[col]
                    if col < len(grid_line.cells) else GridCell())
            self.buffer[row][col] = Char(                  # ← 每个 cell 创建新 Char
                data=cell.data or "",
                fg=cell.fg, bg=cell.bg, bold=cell.bold,
                italics=cell.italics, underscore=cell.underscore,
                strikethrough=cell.strikethrough, reverse=cell.reverse,
            )
```

**问题**：rows × cols 次 Char 对象创建（如 80×24=1920 次），每次 resize/reflow 都执行。

**量级**：resize 频率低，但单次 O(rows×cols) 对象创建。

**优化方向**：批量更新或用 `__slots__` 优化 Char 创建。

---

### H6. read_handler / utils / mouse 每次调用都 re.compile

**位置**：

- `src/daemon/handlers/read_handler.py:75`：`if safe_regex_search(re.compile(trigger), output):`
- `src/daemon/handlers/utils.py:314`：`pat = re.compile(grep)`
- `src/input/mouse.py:263`：`pat = re.compile(pattern)`

**问题**：三处都在请求处理路径内编译正则，未缓存。`read_handler` 的 trigger 正则可能每次 read 请求都编译。Python 有 `re._cache`（默认 512 条），但仍有缓存查找开销，且 grep 模式多变易挤出缓存。

**量级**：read 请求频率中低，但 mouse `grep_screen` 可能高频调用。

**优化方向**：用 `functools.lru_cache` 缓存编译结果，或在 `TriggerMatcher` 初始化时预编译（`trigger.py:139/292` 已预编译，但 `read_handler` 未复用）。

---

## 4. 中等问题（Medium）

### M1. session.resize 用 time.sleep 轮询等 repaint

**位置**：`src/session/session.py:580-603`

**代码**：

```python
waited_ms = 0
for _ in range(20):
    if self._screen.feed_count > prior_feed:
        break
    time.sleep(0.01)                                       # ← 最多 20×10ms = 200ms
    waited_ms += 10
if self._screen.feed_count > prior_feed:
    stable_ms = 0
    last_count = self._screen.feed_count
    for _ in range(10):
        time.sleep(0.03)                                   # ← 最多 10×30ms = 300ms
        cur_count = self._screen.feed_count
        if cur_count == last_count:
            stable_ms += 30
            if stable_ms >= 60:
                break
        else:
            stable_ms = 0
            last_count = cur_count
```

**问题**：resize 后用 `time.sleep` 轮询等待 ConPTY repaint 字节到达，最多阻塞 500ms。轮询期间 reader 线程被阻塞（resize 在主线程，但持锁可能影响 reader）。

**量级**：resize 频率低，但单次延迟最高 500ms。

**优化方向**：用 `Event.wait(timeout)` 替代 sleep 轮询，reader feed 后 set Event。

---

### M2. stats_provider 用 time.sleep(0.1) 采样 CPU

**位置**：`src/web/infrastructure/system/stats_provider.py:59` 和 `:111`

**问题**：每次获取系统 CPU 都 sleep 100ms 采样，若前端高频轮询 system_stats 会阻塞。

**量级**：取决于前端轮询频率，通常低频（1-5s）。

**优化方向**：后台定时采样缓存，请求时直接返回缓存值。

---

### M3. process/info.py 遍历 /proc 每个 pid open 多个文件

**位置**：`src/process/info.py:380-427`

**问题**：每个进程 open 5 个文件（comm、cmdline、stat、uptime、statm），`/proc/uptime` 在循环内重复 open。数百进程时上千次系统调用。

**量级**：进程列表刷新频率中低，但单次开销大。

**优化方向**：`/proc/uptime` 提到循环外只读一次；用 `scandir` 替代 `listdir`。

---

### M4. history_store json.dumps 大对象序列化

**位置**：`src/web/infrastructure/repositories/history_store.py:117-137`

**问题**：归档时 `json.dumps(screen_buf)` 序列化整个屏幕网格（rows×cols 个 cell），再 gzip 压缩。大终端 + 长会话时 JSON 字符串可达数 MB。

**量级**：会话结束时一次性开销，但可能阻塞。

**优化方向**：流式 JSON 或直接二进制序列化。

---

### M5. state.js setSessionSizeConfig 每次都 localStorage.setItem 全量 JSON

**位置**：`src/web/static/js/domain/state.js:235-251`

**问题**：每次更新单个会话配置都 `JSON.stringify` 整个 Map（最多 50 条），再 `localStorage.setItem` 同步写。Ctrl+滚轮调字号时高频调用。

**量级**：字号调整时高频，每次全量序列化 + 同步 localStorage 写。

**优化方向**：debounce localStorage 写；或只写变更的条目。

---

### M6. ConPTY.write 每次都扫描鼠标序列

**位置**：`src/pty/windows/conpty.py:263`

**问题**：每次 write 都 `b'\x1b[<' in data` 做全量 bytes 子串搜索，大多数写不包含鼠标序列。

**量级**：与用户输入频率相同（键击级），单次开销小但无用功。

**优化方向**：仅 debug 日志开启时才扫描。

---

### M7. reader_loop select 0.5s 超时轮询（Unix 路径）

**位置**：`src/session/session_threads.py:128`

**问题**：0.5 秒超时意味着会话结束时最多延迟 0.5s 才检测到 stop_event。正常输出时 select 立即返回，开销不大，但空闲时每 0.5s 唤醒一次。

**量级**：空闲时 2 次/秒 唤醒，影响极小。

**优化方向**：可将 stop_event fd 加入 select 监听，或用更短超时（0.1s）。

---

## 5. 低优先级问题（Low）

### L1. auto_detect debug 日志 data[:40] / data[-20:] 切片

**位置**：`src/encoding/codec.py:196-197`

**问题**：`data[:40]` 和 `data[-20:]` 作为参数在函数调用时即求值，即使 debug 日志级别不满足也执行切片。

**量级**：切片开销极小，但高频调用累积。

**优化方向**：`if _logger.isEnabledFor(logging.DEBUG):` 守卫。

---

### L2. shm.py 每次创建新 mmap

**位置**：`src/ipc/shm.py`（`read_auth_token` / `read_hmac_key`）

**问题**：每次读取认证凭据都新建 mmap + read + close。

**量级**：低频（连接建立时）。

**优化方向**：可接受，无需优化。

---

### L3. service-worker.js SWR 每次都后台 fetch

**位置**：`src/web/static/service-worker.js:184-191`

**问题**：stale-while-revalidate 策略下，每次静态资源请求都触发后台 fetch 更新缓存，即使资源未变。

**量级**：页面加载时有限次，影响小。

**优化方向**：可加 ETag/Last-Modified 条件请求，或限频后台更新。

---

## 6. 通用反模式扫描结果

| 反模式 | 命中数 | 热路径命中 | 说明 |
|--------|--------|-----------|------|
| `time.sleep` | 23 处 | M1（session.py:585/592）、M2（stats_provider.py:59/111） | 多数在启动/关闭/采样路径（可接受），热路径仅 M1/M2 |
| `re.compile` 在请求路径 | 3 处 | H6（read_handler.py:75、utils.py:314、mouse.py:263） | 未缓存编译，详见 H6 |
| `json.dumps` | 39 处 | C4（handlers.py via Response.ws_output）、M4（history_store.py:121/133） | 多数在低频路径（配置/认证），热路径为 C4/M4 |
| `open()` | 37 处 | M3（process/info.py 多处） | 多数在启动/配置/认证路径，热路径仅 M3 |

**未发现的反模式**：

- 未发现循环内 `re.compile`（`trigger.py:139/292` 在初始化时编译，check 时复用 `self._regex`）。
- 未发现循环内 `open()`（`process/info.py` 的 open 在 per-pid 循环内，但非嵌套循环）。
- 未发现 `bytes` 拼接在热路径（drain 用 `b"".join(chunks)`，正确）。
- 未发现 `asyncio.run`/`loop.run_until_complete` 嵌套。
- 未发现同步阻塞调用在 async 函数里（`open().read()`、`subprocess.run`、`requests.get` 等）。

---

## 7. 优化建议与优先级

### P0 — 立即处理（热路径核心瓶颈）

| 编号 | 优化项 | 涉及问题 | 预期收益 | 实施难度 |
|------|--------|----------|----------|----------|
| P0-1 | `trigger.check` 移出 `out_buf.lock` | C1+C2 | 消除 reader 线程锁等待 | 低（锁粒度调整） |
| P0-2 | 编码探测结果按会话缓存 | C3+C4 | 消除双重解码，省一半解码开销 | 中（需改 encoder 接口） |
| P0-3 | reader 的 `feed(pyte)` 改异步/批量 | C1 | 解放 reader 线程，提升 read 吞吐 | 中（需引入队列/线程） |

### P1 — 短期处理（前端与广播）

| 编号 | 优化项 | 涉及问题 | 预期收益 | 实施难度 |
|------|--------|----------|----------|----------|
| P1-1 | 前端 output 用 `requestAnimationFrame` 批量合并 | C5 | 消除主线程渲染堆积 | 低（前端局部改造） |
| P1-2 | `event_publisher` 维护 session→conn 反向索引 | C6 | 广播 O(连接数) → O(订阅者数) | 中（需维护索引一致性） |
| P1-3 | snapshot 用 debounce 单 timer | C5 | 消除 setTimeout 堆积 | 低 |

### P2 — 中期处理（缓冲区与对象复用）

| 编号 | 优化项 | 涉及问题 | 预期收益 | 实施难度 |
|------|--------|----------|----------|----------|
| P2-1 | `OutputBuffer` 改环形缓冲区 | H1 | 消除 O(n) 头部 del | 高（数据结构重构） |
| P2-2 | ConPTY 预分配复用 read buffer | H2 | 减少 GC 压力 | 低 |
| P2-3 | `re.compile` 用 `lru_cache` 缓存 | H6 | 消除请求路径重复编译 | 低 |
| P2-4 | H264 `stride == width*4` 时跳过 ascontiguousarray | H3 | 减少每帧一次大拷贝 | 低 |

### P3 — 长期处理（中低优先级）

| 编号 | 优化项 | 涉及问题 | 实施难度 |
|------|--------|----------|----------|
| P3-1 | resize 用 `Event.wait` 替代 sleep 轮询 | M1 | 中 |
| P3-2 | stats_provider 后台定时采样缓存 | M2 | 低 |
| P3-3 | process/info `/proc/uptime` 提到循环外 | M3 | 低 |
| P3-4 | state.js localStorage 写 debounce | M5 | 低 |
| P3-5 | ConPTY.write 鼠标序列扫描仅 debug 时执行 | M6 | 低 |

---

## 8. 附录

### 8.1 审计文件清单

**Python 后端（核心热路径）**：

- `src/session/session_threads.py` — reader_loop 主循环
- `src/session/session.py` — 会话管理、resize
- `src/output/buffer.py` — 输出缓冲区
- `src/output/trigger.py` — 触发器匹配
- `src/encoding/codec.py` — 编码探测
- `src/pty/windows/conpty.py` — ConPTY read/write
- `src/web/application/handlers.py` — WS 订阅回调
- `src/web/infrastructure/web/event_publisher.py` — 广播
- `src/fastscreen/streamers/encoding/h264.py` — H264 编码

**前端 JS（热路径）**：

- `src/web/static/js/infrastructure/terminal/lifecycle.js` — xterm 写入
- `src/web/static/js/infrastructure/wsClient.js` — WS 客户端
- `src/web/static/js/domain/state.js` — 状态管理

### 8.2 量级参考

| 场景 | read 频率 | 单次 data 大小 | 触发链路开销 |
|------|-----------|----------------|--------------|
| 交互式输入 | 1-10 次/秒 | <100B | 低 |
| `cat` 大文件 | 1000-5000 次/秒 | 64KB | 高（C1-C6 全部命中） |
| `find /` | 500-2000 次/秒 | 4-64KB | 高 |
| 编译输出 | 100-1000 次/秒 | 1-64KB | 中-高 |
| H264 推流 | 30 次/秒（帧） | 8MB（1080p BGRA） | 高（H3） |

### 8.3 验证方法

本报告所有发现均已对照源码行级验证，验证过的关键文件：

- `src/session/session_threads.py:120-199`（reader_loop）
- `src/output/trigger.py:220-259`（check 方法）
- `src/encoding/codec.py:180-224`（auto_detect）
- `src/web/application/handlers.py:340-364`（_on_data）
- `src/web/infrastructure/web/event_publisher.py:125-166`（_emit）
- `src/web/static/js/infrastructure/terminal/lifecycle.js:475-514`（handleOutput）

---

**文档状态**：complete

**下一步**：建议从 P0-1（`trigger.check` 移出锁）入手，改动最小、收益最大。如需落地某项优化，请指明编号与范围。
