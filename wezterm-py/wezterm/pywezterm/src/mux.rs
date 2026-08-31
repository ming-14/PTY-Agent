//! Mux 复用器编排单元
//!
//! 在 pywezterm 绑定层用 wezterm 底层模块搭建多窗格复用器。
//! 每个 Pane = 一个真实伪终端（pend子进程）+ 一个 wezterm-term 终端模型。
//!
//! - Pane 内部自建 **reader 线程**：阻塞读 pty 输出 → feed 终端模型，并把
//!   终端应答（键盘编码捕获缓冲，如 DSR 光标应答）自动回写 pty writer。
//! - 键盘/鼠标事件经终端模型编码（模式感知）写入捕获缓冲，再下发 pty
//!   （即 doc 所述的 capture writer 键盘路径）。
//!
//! 渲染合成（脏行驱动 → Surface get_changes 增量字节 + run 风格合并）、
//! 键盘/鼠标命中路由、视图滚动。多窗格布局沿用左右二分。

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet, VecDeque};
use std::ffi::OsString;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize, SlavePty};
use wezterm_surface::{Change, CursorVisibility, Position, Surface};
use wezterm_term::input::{KeyModifiers, MouseEvent};
use wezterm_term::{CellAttributes, Clipboard, Intensity, Terminal, TerminalSize, Underline};

use crate::pty::ensure_conpty_dir;
#[cfg(windows)]
use crate::pty::{
    cancel_reader_thread, close_thread_handle, duplicate_current_thread_handle,
};
use crate::surface_render::{parse_color, render_changes_bytes};
use crate::term::{
    cells_of_line, parse_keycode, parse_mouse_button, parse_mouse_kind, CaptureWriter,
    CellTuple, EmbeddedConfig, PyClipboard, SelectionState,
};
use wezterm_char_props::widechar_width::WcLookupTable;

/// 通知"某 pane 有新输出"（reader 线程 feed 数据后调用）。
/// 回调只应做轻量操作（如 set event），不得反查终端状态——否则与
/// reader 线程持 terminal 锁互等死锁。
fn notify_output(cb: &Option<Py<PyAny>>) {
    if let Some(cb) = cb {
        Python::attach(|py| {
            if let Err(e) = cb.bind(py).call0() {
                log::error!("output callback 异常: {e}");
            }
        });
    }
}

// ---- 矩形与布局树（左右/上下二分）---------------------------------------
#[derive(Clone, Copy)]
struct Rect {
    x: usize,
    y: usize,
    w: usize,
    h: usize,
}

enum SplitDir {
    LR,
}

enum Layout {
    Leaf { pane_id: usize },
    Split { dir: SplitDir, a: Box<Layout>, b: Box<Layout> },
}

/// 递归计算布局 → 各 pane 的矩形（填到 rects）
fn compute_rects_rec(layout: &Layout, rects: &mut Vec<Rect>, x: usize, y: usize, w: usize, h: usize) {
    match layout {
        Layout::Leaf { pane_id } => {
            if rects.len() <= *pane_id {
                rects.resize(*pane_id + 1, Rect { x: 0, y: 0, w: 0, h: 0 });
            }
            rects[*pane_id] = Rect { x, y, w, h };
        }
        Layout::Split { dir, a, b } => match dir {
            SplitDir::LR => {
                let wa = w / 2;
                compute_rects_rec(a, rects, x, y, wa, h);
                compute_rects_rec(b, rects, x + wa, y, w - wa, h);
            }
        },
    }
}

/// 计算当前布局下各 pane 的矩形（统一入口，供 add/resize/set_split/set_status 用）。
///
/// sep=true 且布局为左右二分（左右分屏形态）：pane 间预留一列分隔线——
/// 左 pane [0, split)，分隔线 x=split，右 pane [split+1, cols)；
/// 底部预留 status_rows 行。
/// 其余情况沿用递归二分（pane 紧贴，无分隔线）。
/// 注：按布局树形状判断（add_pane 先建树后 push pane，不能依赖 panes.len()）。
fn recompute_rects(st: &mut MuxState) {
    st.rects.clear();
    let avail_rows = st.rows.saturating_sub(st.status_rows).max(1);
    match &st.layout {
        Layout::Leaf { .. } => {
            st.rects.push(Rect { x: 0, y: 0, w: st.cols, h: avail_rows });
        }
        Layout::Split { dir: SplitDir::LR, .. } if st.sep => {
            let split = st
                .split
                .unwrap_or(st.cols / 2)
                .max(1)
                .min(st.cols.saturating_sub(2).max(1));
            let right_x = split + 1;
            let right_w = st.cols.saturating_sub(right_x).max(1);
            st.rects.push(Rect { x: 0, y: 0, w: split, h: avail_rows });
            st.rects.push(Rect { x: right_x, y: 0, w: right_w, h: avail_rows });
        }
        _ => compute_rects_rec(&st.layout, &mut st.rects, 0, 0, st.cols, avail_rows),
    }
    for r in st.rects.iter_mut() {
        r.w = r.w.max(1);
        r.h = r.h.max(1);
    }
}

// ---- Pane（真实 Pty + 终端模型）-----------------------------------------
/// 单个 pane 的运行时状态（Arc 包裹供 reader 线程持有子集引用）
struct PaneInner {
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    /// 保存 slave 以持有 HPCON 引用，close 时与 master 一起释放，
    /// 与 Pty（pty.rs）保持一致的关闭语义（避免 conpty 提前退出）。
    _slave: Mutex<Option<Box<dyn SlavePty + Send>>>,
    writer: Arc<Mutex<Option<Box<dyn Write + Send>>>>,
    child: Mutex<Option<Box<dyn Child + Send + Sync>>>,
    terminal: Arc<Mutex<Terminal>>,
    capture: Arc<Mutex<Vec<u8>>>,
    /// 子进程原始输出缓冲（reader 线程写入，Python 侧 drain 用于录制 asciicast）
    output_buf: Arc<Mutex<VecDeque<u8>>>,
    /// 视图滚动偏移（scrollback 行）；滚动查看历史时 snapshot/text/render 反映
    view_offset: Arc<Mutex<usize>>,
    /// 合成渲染基线：上次 compose 时终端 seqno / 视图偏移（判定脏行与视图平移）
    last_seqno: Mutex<Option<usize>>,
    last_view: Mutex<usize>,
    /// 选区状态（stable 行 + 列；与视图滚动解耦）
    selection: Mutex<SelectionState>,
    eof: Arc<AtomicBool>,
    closed: Arc<AtomicBool>,
    reader_thread: Arc<Mutex<Option<usize>>>,
    /// resize 后 pending 的 ConPTY repaint（纯重绘 \x1b[?25l\x1b[H），
    /// 读者跳过直到遇到第一个非 repaint 内容，防止窄化 wrap 段污染
    repaint_pending: Arc<AtomicBool>,
}

type Pane = Arc<PaneInner>;

/// 构造一个 wezterm-term 终端模型（capture writer 供键盘编码捕获）
fn build_terminal(cols: usize, rows: usize, capture: Arc<Mutex<Vec<u8>>>) -> Terminal {
    let size = TerminalSize {
        rows,
        cols,
        pixel_width: 0,
        pixel_height: 0,
        dpi: 0,
    };
    let mut term = Terminal::new(
        size,
        Arc::new(EmbeddedConfig { scrollback: 10000 }),
        "pywezterm",
        env!("CARGO_PKG_VERSION"),
        Box::new(CaptureWriter::new(capture.clone())),
    );
    term.enable_conpty_quirks();
    term
}

/// 从源码取走捕获缓冲的应答/编码字节
fn take_capture(capture: &Arc<Mutex<Vec<u8>>>) -> Vec<u8> {
    std::mem::take(&mut *capture.lock().unwrap())
}

/// 把编码后的输入/应答字节写入 pty writer
fn write_to_writer(writer: &Arc<Mutex<Option<Box<dyn Write + Send>>>>, data: &[u8]) -> PyResult<()> {
    if data.is_empty() {
        return Ok(());
    }
    let mut w = writer.lock().unwrap();
    match w.as_mut() {
        Some(w) => w
            .write_all(data)
            .map_err(|e| PyRuntimeError::new_err(format!("write 失败: {e}"))),
        None => Ok(()),
    }
}

// ---- Pane 视图查询/滚动（与 PyTerminal 一致的语义）------------------------

/// 当前视图滚动偏移（clamp 到可用历史区）
fn pane_view_offset(pane: &Pane, total: usize, rows: usize) -> usize {
    pane.view_offset
        .lock()
        .unwrap()
        .min(total.saturating_sub(rows))
}

/// scrollback 历史行数
fn pane_scrollback_count(pane: &Pane) -> usize {
    let term = pane.terminal.lock().unwrap();
    let screen = term.screen();
    screen
        .scrollback_rows()
        .saturating_sub(screen.physical_rows)
}

/// 视图滚动：delta>0 上滚（查看更早），delta<0 回落；clamp 到合法范围
fn pane_scroll_view(pane: &Pane, delta: i64) {
    let max_hist = pane_scrollback_count(pane);
    let mut off = pane.view_offset.lock().unwrap();
    *off = (*off as i64 + delta).clamp(0, max_hist as i64) as usize;
}

/// 回落到底部，恢复跟随最新输出
fn pane_scroll_view_bottom(pane: &Pane) {
    *pane.view_offset.lock().unwrap() = 0;
}

// ---- 合成渲染：往 Surface 写 cell（run 合并，同 surface_render.put_cell 语义）-----

/// CellTuple → CellAttributes
fn cell_attrs(c: &CellTuple) -> CellAttributes {
    let mut a = CellAttributes::default();
    a.set_foreground(parse_color(&c.2));
    a.set_background(parse_color(&c.3));
    if c.4 {
        a.set_intensity(Intensity::Bold);
    }
    if c.5 {
        a.set_italic(true);
    }
    if c.6 {
        a.set_underline(Underline::Single);
    }
    if c.7 {
        a.set_reverse(true);
    }
    if c.8 {
        a.set_strikethrough(true);
    }
    a
}

/// 一个连续同风格的文本段（渲染时只发一次 CUP + 属性 + 文本，避免逐格定位）
struct Run {
    start: usize,
    end: usize,
    text: String,
    attrs: CellAttributes,
}

/// 把一行 cells 合成进 Surface，合并相邻同风格格为文本段，并在行内空白与
/// 行尾补默认空格清掉过期内容。宽字符按其 width 占列，续列不单独发。
fn emit_row(surface: &mut Surface, x0: usize, y: usize, rw: usize, cells: &[CellTuple]) {
    // 按列生成 (col, text, attrs, width) 条目（含内容间/行尾的空白段）
    let default_attrs = CellAttributes::default();
    let mut items: Vec<(usize, String, CellAttributes, usize)> = Vec::new();
    let mut prev_end = 0usize;
    for cell in cells {
        let (lc, cw) = (cell.0, cell.9);
        if lc >= rw {
            break;
        }
        let text = if cell.1.is_empty() { " ".to_string() } else { cell.1.clone() };
        if lc > prev_end {
            items.push((prev_end, " ".repeat(lc - prev_end), default_attrs.clone(), lc - prev_end));
        }
        items.push((lc, text, cell_attrs(cell), cw));
        prev_end = lc + cw;
    }
    if prev_end < rw {
        items.push((prev_end, " ".repeat(rw - prev_end), default_attrs, rw - prev_end));
    }

    // 合并连续同列位、同风格 → 一个文本段
    let mut runs: Vec<Run> = Vec::new();
    for (col, text, attrs, width) in items {
        if let Some(last) = runs.last_mut() {
            if last.end == col && last.attrs == attrs {
                last.text.push_str(&text);
                last.end = col + width;
                continue;
            }
        }
        runs.push(Run { start: col, end: col + width, text, attrs });
    }
    for run in runs {
        surface.add_change(Change::CursorPosition {
            x: Position::Absolute(x0 + run.start),
            y: Position::Absolute(y),
        });
        surface.add_change(Change::AllAttributes(run.attrs));
        surface.add_change(Change::Text(run.text));
    }
}

/// 状态栏整行（默认样式）合成进 Surface：按**显示宽度**截断/补白到全宽。
/// 仅在状态文本变化时调用（参与增量 diff）。
///
/// 必须按宽度而非字符数截断：CJK/emoji 等双宽字符会让字符数 < 列数，
/// 若按字符数补满，print_text 写入时 xpos 超宽会触发 Surface 的滚动
/// （终端语义），把整屏内容顶上消失。
fn draw_status_text(surface: &mut Surface, row: usize, cols: usize, text: &str) {
    let classifier = WcLookupTable::new();
    let mut line = String::with_capacity(cols);
    let mut width = 0usize;
    for ch in text.chars() {
        let w = classifier.classify(ch).width_unicode_9_or_later() as usize;
        if width + w > cols {
            break;
        }
        line.push(ch);
        width += w;
    }
    while width < cols {
        line.push(' ');
        width += 1;
    }
    surface.add_change(Change::CursorPosition {
        x: Position::Absolute(0),
        y: Position::Absolute(row),
    });
    surface.add_change(Change::AllAttributes(CellAttributes::default()));
    surface.add_change(Change::Text(line));
}

/// 把单个 pane 的可见内容合成进 Mux Surface 的矩形区域。
///
/// 增量策略：只对「确实变化」的矩形行重写格子，避免每帧把所有格子 set_cell
/// （Line::set_cell 无条件上推 last_change_seqno，全写会令 Surface 每帧全量重绘）。
/// - 首帧、或该 pane 视图滚动偏移变化（视图平移）→ 整 pane 全量重写；
/// - 否则用终端脏行（changed_stable_rows）判定哪些物理行要重写；
/// - 重写的行用 emit_row 连风格合并写出，未变化行保持原 Surface。
fn compose_pane(st: &mut MuxState, id: usize) {
    let rect = st.rects[id];
    let (rw, rh) = (rect.w.max(0), rect.h.max(0));
    let pane = &st.panes[id];
    let term = pane.terminal.lock().unwrap();
    let screen = term.screen();
    let total = screen.scrollback_rows();
    let rows = screen.physical_rows;
    let offset = pane_view_offset(pane, total, rows);
    let end = total.saturating_sub(offset);
    let start = end.saturating_sub(rows);
    let cur_seqno = term.current_seqno();
    let lines = screen.lines_in_phys_range(start..end);

    // 首帧 / 视图平移 → 全 pane 重写；否则只写脏行
    let mut first = false;
    {
        let mut ls = pane.last_seqno.lock().unwrap();
        if ls.is_none() {
            *ls = Some(cur_seqno);
            first = true;
        }
    }
    let view_moved = *pane.last_view.lock().unwrap() != offset;
    let dirty: Option<HashSet<isize>> = if first || view_moved {
        None // 全行重写
    } else {
        let since = pane.last_seqno.lock().unwrap().unwrap();
        let changed = screen.get_changed_stable_rows(0..total as isize, since);
        Some(changed.into_iter().collect())
    };

    for li in 0..rh {
        let phys = start + li;
        let must = match &dirty {
            None => true,
            Some(set) => {
                if phys < total {
                    set.contains(&screen.phys_to_stable_row_index(phys))
                } else {
                    true
                }
            }
        };
        if !must {
            continue;
        }
        let cells: Vec<CellTuple> = if li < lines.len() {
            cells_of_line(&lines[li])
        } else {
            Vec::new()
        };
        emit_row(&mut st.surface, rect.x, rect.y + li, rw, &cells);
    }
    *pane.last_seqno.lock().unwrap() = Some(cur_seqno);
    *pane.last_view.lock().unwrap() = offset;
}

/// 命中测试：整屏坐标 (x,y) → 命中 pane id；未命中返回 None
fn hit_test(st: &MuxState, x: usize, y: usize) -> Option<usize> {
    for (id, r) in st.rects.iter().enumerate() {
        if x >= r.x && x < r.x.saturating_add(r.w) && y >= r.y && y < r.y.saturating_add(r.h) {
            return Some(id);
        }
    }
    None
}

/// 创建 Pane：openpty + spawn 子进程 + 起 reader 线程自动喂终端
fn build_pane(
    py: Python,
    argv: Vec<String>,
    cwd: Option<String>,
    env: Option<HashMap<String, String>>,
    cols: u16,
    rows: u16,
    output_cb: Option<Py<PyAny>>,
) -> PyResult<Pane> {
    ensure_conpty_dir(py);
    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| PyRuntimeError::new_err(format!("openpty 失败: {e:#}")))?;

    let writer: Arc<Mutex<Option<Box<dyn Write + Send>>>> = Arc::new(Mutex::new(Some(
        pair.master
            .take_writer()
            .map_err(|e| PyRuntimeError::new_err(format!("take_writer 失败: {e:#}")))?,
    )));
    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|e| PyRuntimeError::new_err(format!("try_clone_reader 失败: {e:#}")))?;

    let capture = Arc::new(Mutex::new(Vec::new()));
    let terminal = Arc::new(Mutex::new(build_terminal(cols as usize, rows as usize, capture.clone())));

    // spawn 子进程
    let args: Vec<OsString> = argv.into_iter().map(OsString::from).collect();
    let mut builder = CommandBuilder::from_argv(args);
    if let Some(cwd) = cwd {
        builder.cwd(cwd);
    }
    if let Some(env) = env {
        for (k, v) in env {
            builder.env(k, v);
        }
    }
    let child = pair
        .slave
        .spawn_command(builder)
        .map_err(|e| PyRuntimeError::new_err(format!("spawn 失败: {e:#}")))?;

    let eof = Arc::new(AtomicBool::new(false));
    let closed = Arc::new(AtomicBool::new(false));
    let repaint_pending = Arc::new(AtomicBool::new(false));
    let reader_thread: Arc<Mutex<Option<usize>>> = Arc::new(Mutex::new(None));
    let view_offset = Arc::new(Mutex::new(0));
    let output_buf: Arc<Mutex<VecDeque<u8>>> = Arc::new(Mutex::new(VecDeque::with_capacity(8192)));
    let inner = Arc::new(PaneInner {
        master: Mutex::new(Some(pair.master)),
        _slave: Mutex::new(Some(pair.slave)),
        writer: writer.clone(),
        child: Mutex::new(Some(child)),
        terminal: terminal.clone(),
        capture: capture.clone(),
        output_buf: output_buf.clone(),
        view_offset: view_offset.clone(),
        last_seqno: Mutex::new(None),
        last_view: Mutex::new(0),
        selection: Mutex::new(SelectionState::default()),
        eof: eof.clone(),
        closed: closed.clone(),
        reader_thread: reader_thread.clone(),
        repaint_pending: repaint_pending.clone(),
    });

    // reader 线程：读 pty 输出 → 喂终端 → 把终端应答自动回写 pty writer。
    // 阻塞读发生在库内部线程；close 时经 CancelSynchronousIo 取消（Windows）。
    let terminal_c = terminal.clone();
    let capture_c = capture.clone();
    let writer_c = writer.clone();
    let reader_thread_h = reader_thread.clone();
    let repaint_pending_c = repaint_pending.clone();
    let output_buf_c = output_buf.clone();
    std::thread::spawn(move || {
        #[cfg(windows)]
        {
            // GetCurrentThread 为伪句柄，须复制成跨线程有效句柄
            #[cfg(windows)]
            {
                let h = duplicate_current_thread_handle();
                if h != 0 {
                    *reader_thread_h.lock().unwrap() = Some(h);
                }
            }
        }
        // ConPTY resize 后发送多次 repaint（\x1b[?25l\x1b[8;...t\x1b[H + 可见区
        // 或 \x1b[?25l\x1b[H + 可见区），是 ConPTY 修剪后的可见区，直接 feed
        // 会污染 terminal 的 scrollback（如 age.json 孤立）。整段跳过。
        // ConPTY resize 后发送多次 repaint（\x1b[?25l\x1b[8;...t\x1b[H + 可见区
        // 或 \x1b[?25l\x1b[H + 可见区），是 ConPTY 修剪后的可见区，直接 feed
        // 会污染 terminal 的 scrollback（如窄化时孤立 wrap 段）。跳过。
        // 带窗口尺寸的（\x1b[8;）总是跳过；纯重绘（\x1b[?25l\x1b[H，无窗口尺寸）
        // 在 resize 后（repaint_pending 标志）跳过，遇到第一个非 repaint 内容
        // 即清除标志，全屏 TUI 的正常全屏重绘不受影响。
        loop {
            if closed.load(Ordering::SeqCst) {
                eof.store(true, Ordering::SeqCst);
                break;
            }
            let mut tmp = [0u8; 8192];
            match reader.read(&mut tmp) {
                Ok(0) => {
                    eof.store(true, Ordering::SeqCst);
                    notify_output(&output_cb); // EOF 也通知一次，宿主据此做最终渲染
                    break;
                }
                Ok(n) => {
                    if tmp[..n].windows(10).any(|w| w == b"\x1b[?25l\x1b[8;") {
                        continue;
                    }
                    if repaint_pending_c.load(Ordering::SeqCst) {
                        let is_pure_repaint = tmp[..n].windows(9).any(|w| w == b"\x1b[?25l\x1b[H");
                        if is_pure_repaint {
                            continue;
                        }
                        repaint_pending_c.store(false, Ordering::SeqCst);
                    }
                    // 子进程原始输出 → 录制缓冲（供 Python 侧 drain 写 asciicast）
                    let mut ob = output_buf_c.lock().unwrap();
                    if ob.len() < 16 * 1024 * 1024 {
                        ob.extend(&tmp[..n]);
                    }
                    drop(ob);
                    {
                        let mut t = terminal_c.lock().unwrap();
                        t.advance_bytes(&tmp[..n]);
                        // 应答（DSR/键盘序列集合）自动回写 pty，避免子进程等应答卡死
                        let resp = take_capture(&capture_c);
                        if !resp.is_empty() {
                            let _ = write_to_writer(&writer_c, &resp);
                        }
                    }
                    // 新输出通知：必须在 terminal 锁释放后调用（回调若反查终端会死锁）
                    notify_output(&output_cb);
                }
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => {
                    eof.store(true, Ordering::SeqCst);
                    notify_output(&output_cb);
                    break;
                }
            }
        }
        #[cfg(windows)]
        {
            if let Some(h) = reader_thread_h.lock().unwrap().take() {
                close_thread_handle(h);
            }
        }
    });
    Ok(inner)
}

// ---- Mux -----------------------------------------------------------------
struct MuxState {
    cols: usize,
    rows: usize,
    panes: Vec<Pane>,
    focused: usize,
    layout: Layout,
    rects: Vec<Rect>,
    /// 合成用的整屏表面（增量渲染作图面）
    surface: Surface,
    seqno: usize,
    /// 左右分屏是否在 pane 之间预留一列分隔线；否则 pane 紧贴
    sep: bool,
    /// 分隔线所在列（= 左 pane 宽度）；None 用中点
    split: Option<usize>,
    /// 底部预留的状态栏行数（0 = 无）
    status_rows: usize,
    /// 状态栏文本（由宿主设置，Mux 合成进 Surface 参与增量 diff 与光标避让）
    status: String,
    /// 上次绘制的分隔线 (列, 高) 与状态栏文本，用于增量（变化才重画）
    last_sep: Option<(usize, usize)>,
    last_status: String,
    last_status_rows: usize,
}

#[pyclass(name = "Mux")]
pub struct PyMux {
    inner: Mutex<MuxState>,
    /// 新输出通知回调（任一 pane 有新输出时调用，None=不通知）
    output_cb: Mutex<Option<Py<PyAny>>>,
}

/// pty writer 写字节；对 closed 的 pane 静默忽略
fn pane_write(pane: &Pane, data: Vec<u8>) -> PyResult<()> {
    if pane.closed.load(Ordering::SeqCst) {
        return Ok(());
    }
    write_to_writer(&pane.writer, &data)
}

impl PyMux {
    /// 取 pane 句柄（越界报错）
    fn get_pane(st: &std::sync::MutexGuard<'_, MuxState>, id: usize) -> PyResult<Pane> {
        st.panes
            .get(id)
            .cloned()
            .ok_or_else(|| PyRuntimeError::new_err(format!("pane {id} 不存在")))
    }

    /// 整屏坐标 → 该 pane 内 stable 行 + 列（计入视图滚动偏移，clamp 到合法范围）
    fn pane_screen_to_stable(&self, pane_id: usize, x: usize, y: i64) -> PyResult<(isize, usize)> {
        let (pane, rect) = {
            let st = self.inner.lock().unwrap();
            (Self::get_pane(&st, pane_id)?, st.rects[pane_id])
        };
        let lx = x.saturating_sub(rect.x);
        let ly = (y as i64 - rect.y as i64).max(0) as usize;
        let term = pane.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = pane_view_offset(&pane, total, rows);
        let end = total.saturating_sub(offset);
        let start = end.saturating_sub(rows);
        let phys = (start + ly.min(rows.saturating_sub(1))).min(total.saturating_sub(1));
        Ok((screen.phys_to_stable_row_index(phys), lx))
    }
}

#[pymethods]
impl PyMux {
    #[new]
    #[pyo3(signature = (cols=80, rows=24))]
    fn new(cols: usize, rows: usize) -> Self {
        Self {
            inner: Mutex::new(MuxState {
                cols,
                rows,
                panes: vec![],
                focused: 0,
                layout: Layout::Leaf { pane_id: 0 },
                rects: vec![],
                surface: Surface::new(cols, rows),
                seqno: 0,
                sep: false,
                split: None,
                status_rows: 0,
                status: String::new(),
                last_sep: None,
                last_status: String::new(),
                last_status_rows: 0,
            }),
            output_cb: Mutex::new(None),
        }
    }

    fn dimensions(&self) -> (usize, usize) {
        let st = self.inner.lock().unwrap();
        (st.cols, st.rows)
    }

    fn pane_count(&self) -> usize {
        self.inner.lock().unwrap().panes.len()
    }

    fn focused(&self) -> usize {
        self.inner.lock().unwrap().focused
    }

    /// 新建 pane：spawn 一个真实子进程到该 pane 的 pty，并起 reader 线程喂终端。
    /// 第 1 个 pane 填满整屏，第 2 个起左右各半。
    /// 当前布局树仅支持左右二分（最多 2 个 pane），第 3 个起显式报错，
    /// 避免静默得到退化矩形（(0,0,1,1) 与首 pane 重叠）。
    /// 返回 pane id。
    #[pyo3(signature = (argv, cwd=None, env=None))]
    fn add_pane(
        &self,
        py: Python,
        argv: Vec<String>,
        cwd: Option<String>,
        env: Option<HashMap<String, String>>,
    ) -> PyResult<usize> {
        let (cols, rows, id);
        {
            let mut st = self.inner.lock().unwrap();
            id = st.panes.len();
            // 更新布局树
            if id == 0 {
                st.layout = Layout::Leaf { pane_id: 0 };
            } else if id == 1 {
                let l0 = Layout::Leaf { pane_id: 0 };
                let l1 = Layout::Leaf { pane_id: 1 };
                st.layout = Layout::Split {
                    dir: SplitDir::LR,
                    a: Box::new(l0),
                    b: Box::new(l1),
                };
            } else {
                return Err(PyRuntimeError::new_err(
                    "Mux 布局当前仅支持最多 2 个 pane",
                ));
            }
            recompute_rects(&mut st);
            cols = st.rects.get(id).map(|r| r.w).unwrap_or(st.cols).max(1);
            rows = st.rects.get(id).map(|r| r.h).unwrap_or(st.rows).max(1);
            st.focused = id;
        }
        let pane = build_pane(
            py, argv, cwd, env, cols as u16, rows as u16,
            self.output_cb.lock().unwrap().as_ref().map(|cb| cb.clone_ref(py)),
        )?;
        self.inner.lock().unwrap().panes.push(pane);
        Ok(id)
    }

    /// 设置"新输出"回调：任一 pane 的 reader 线程读到新数据并喂入终端后
    /// 调用（无参数）。供宿主事件驱动渲染（替代定时轮询）。None 清除。
    #[pyo3(signature = (callback=None))]
    fn set_output_callback(&self, py: Python, callback: Option<Py<PyAny>>) -> PyResult<()> {
        *self.output_cb.lock().unwrap() = callback.map(|c| c.clone_ref(py));
        Ok(())
    }

    /// 各 pane 布局矩形 (x, y, w, h)
    fn pane_rects(&self) -> Vec<(usize, usize, usize, usize)> {
        let st = self.inner.lock().unwrap();
        st.rects.iter().map(|r| (r.x, r.y, r.w, r.h)).collect()
    }

    /// 往指定 pane 的 pty 写原始字节（测试 / 宿主命令用）
    fn pane_write(&self, pane_id: usize, data: Vec<u8>) -> PyResult<()> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        pane_write(&pane, data)
    }

    /// 键盘按下编码（模式感知）并下发对应 pty，返回编码字节
    fn pane_key_down(&self, pane_id: usize, key: &str, mods: u16) -> PyResult<Vec<u8>> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let code = parse_keycode(key)?;
        let mods = KeyModifiers::from_bits_truncate(mods);
        let _ = take_capture(&pane.capture);
        {
            let mut term = pane.terminal.lock().unwrap();
            term.key_down(code, mods)
                .map_err(|e| PyRuntimeError::new_err(format!("key_down 编码失败: {e:#}")))?;
            term.flush_sync();
        }
        let bytes = take_capture(&pane.capture);
        write_to_writer(&pane.writer, &bytes)?;
        Ok(bytes)
    }

    /// 键盘抬起编码（模式感知）并下发对应 pty，返回编码字节
    fn pane_key_up(&self, pane_id: usize, key: &str, mods: u16) -> PyResult<Vec<u8>> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let code = parse_keycode(key)?;
        let mods = KeyModifiers::from_bits_truncate(mods);
        let _ = take_capture(&pane.capture);
        {
            let mut term = pane.terminal.lock().unwrap();
            term.key_up(code, mods)
                .map_err(|e| PyRuntimeError::new_err(format!("key_up 编码失败: {e:#}")))?;
            term.flush_sync();
        }
        let bytes = take_capture(&pane.capture);
        write_to_writer(&pane.writer, &bytes)?;
        Ok(bytes)
    }

    /// 鼠标事件编码（模式感知）并下发对应 pty，返回编码字节
    #[pyo3(signature = (pane_id, x, y, kind="press", button="left", mods=0))]
    fn pane_mouse(
        &self,
        pane_id: usize,
        x: usize,
        y: i64,
        kind: &str,
        button: &str,
        mods: u16,
    ) -> PyResult<Vec<u8>> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let ev = MouseEvent {
            kind: parse_mouse_kind(kind)?,
            x,
            y,
            x_pixel_offset: 0,
            y_pixel_offset: 0,
            button: parse_mouse_button(button)?,
            modifiers: KeyModifiers::from_bits_truncate(mods),
        };
        let _ = take_capture(&pane.capture);
        {
            let mut term = pane.terminal.lock().unwrap();
            term.mouse_event(ev)
                .map_err(|e| PyRuntimeError::new_err(format!("mouse 编码失败: {e:#}")))?;
            term.flush_sync();
        }
        let bytes = take_capture(&pane.capture);
        write_to_writer(&pane.writer, &bytes)?;
        Ok(bytes)
    }

    /// 取走指定 pane 的原始输出缓冲（drain，供录制 asciicast）；未启用录制时
    /// 缓冲为空（Python 侧应定时 drain 防增长）
    fn pane_take_output(&self, pane_id: usize) -> PyResult<Vec<u8>> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let mut buf = pane.output_buf.lock().unwrap();
        Ok(buf.drain(..).collect())
    }

    /// 指定 pane 原始输出缓冲当前字节数（非阻塞查询用）
    fn pane_output_len(&self, pane_id: usize) -> PyResult<usize> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let len = pane.output_buf.lock().unwrap().len();
        Ok(len)
    }

    /// 当前可见屏幕纯文本（去尾空白，去掉末尾空行；计入视图滚动偏移）
    fn pane_text(&self, pane_id: usize) -> PyResult<String> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let term = pane.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = pane_view_offset(&pane, total, rows);
        let end = total.saturating_sub(offset);
        let start = end.saturating_sub(rows);
        let mut lines: Vec<String> = Vec::new();
        for line in screen.lines_in_phys_range(start..end) {
            let mut s = String::new();
            for cell in line.visible_cells() {
                s.push_str(cell.str());
            }
            while s.ends_with(' ') {
                s.pop();
            }
            lines.push(s);
        }
        while lines.last().map_or(false, |l| l.is_empty()) {
            lines.pop();
        }
        Ok(lines.join("\n"))
    }

    /// 光标位置 (row, col, visible)，0-based；计入视图滚动偏移（同 render 语义）
    fn pane_cursor(&self, pane_id: usize) -> PyResult<(usize, usize, bool)> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let term = pane.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let c = term.cursor_pos();
        let offset = pane_view_offset(&pane, total, rows);
        let crow_screen = (c.y.max(0) as usize).saturating_add(offset);
        let vis = matches!(c.visibility, wezterm_surface::CursorVisibility::Visible)
            && crow_screen < rows;
        // 列号 clamp 到物理列宽：光标写满整行后 c.x = 列宽（边界外）
        let col = c.x.min(screen.physical_cols.saturating_sub(1));
        Ok((crow_screen, col, vis))
    }

    /// 应用是否接管该 pane 的鼠标（DECSET 1000/1002/1003）
    fn pane_is_mouse_grabbed(&self, pane_id: usize) -> PyResult<bool> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let term = pane.terminal.lock().unwrap();
        Ok(term.is_mouse_grabbed())
    }

    /// 调整单个 pane 的 pty + 终端尺寸；同步布局矩形（与宿主屏 resize 一致），
    /// 避免后续合成用旧矩形画新终端内容导致行列错位。
    fn pane_resize(&self, pane_id: usize, cols: usize, rows: usize) -> PyResult<()> {
        if cols > u16::MAX as usize || rows > u16::MAX as usize {
            return Err(PyRuntimeError::new_err(format!(
                "pane 尺寸超出 u16 上限: {cols}x{rows}"
            )));
        }
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        // 与 resize 一致：全程持有 terminal 锁（master.resize 之前就锁），
        // 避免 reader 在间隙把 repaint feed 进旧尺寸 terminal。
        let mut term = pane.terminal.lock().unwrap();
        let master = pane.master.lock().unwrap();
        if let Some(m) = master.as_ref() {
            m.resize(PtySize {
                rows: rows as u16,
                cols: cols as u16,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| PyRuntimeError::new_err(format!("resize 失败: {e:#}")))?;
        }
        drop(master);
        term.resize(TerminalSize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
            dpi: 0,
        });
        Ok(())
    }

    /// 滚动指定 pane 的视图（查看历史）：delta>0 上滚，delta<0 回落
    fn pane_scroll(&self, pane_id: usize, delta: i64) -> PyResult<()> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        pane_scroll_view(&pane, delta);
        Ok(())
    }

    /// 指定 pane 回落到底部，恢复跟随最新输出
    fn pane_scroll_to_bottom(&self, pane_id: usize) -> PyResult<()> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        pane_scroll_view_bottom(&pane);
        Ok(())
    }

    /// 切换焦点 pane；后续 key_down/key_up/mouse/scroll 路由到它
    fn set_focus(&self, pane_id: usize) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        if !(pane_id < st.panes.len()) {
            return Err(PyRuntimeError::new_err(format!("pane {pane_id} 不存在")));
        }
        st.focused = pane_id;
        Ok(())
    }

    /// 是否在 pane 之间预留一列分隔线；重算矩形并标记各 pane 重写
    #[pyo3(signature = (sep=true))]
    fn set_sep(&self, sep: bool) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        st.sep = sep;
        recompute_rects(&mut st);
        st.last_sep = None;
        for p in &st.panes {
            *p.last_seqno.lock().unwrap() = None;
        }
        Ok(())
    }

    /// 设置左右分屏分隔线位置（列）；None 回退中点。
    /// 只重算矩形并标志 pane 重写（预览），不实时 resize ConPTY——
    /// 宿主在拖拽结束后调用 resize() 才一次到位（避免拖动中反复窄化裂行）。
    fn set_split_col(&self, split: Option<usize>) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        st.split = split;
        recompute_rects(&mut st);
        st.last_sep = None;
        st.seqno = 0;
        for p in &st.panes {
            *p.last_seqno.lock().unwrap() = None;
        }
        Ok(())
    }

    /// 底部预留状态栏行数（0 = 无）；重算矩形（pane 高度相应缩减）
    fn set_status_rows(&self, rows_count: usize) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        st.status_rows = rows_count;
        recompute_rects(&mut st);
        st.last_status = String::new();
        for p in &st.panes {
            *p.last_seqno.lock().unwrap() = None;
        }
        Ok(())
    }

    /// 设置状态栏文本（每次 render 时与上次比较，变化才重画）
    fn set_status(&self, text: String) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        st.status = text;
        Ok(())
    }

    /// 宿主屏尺寸变化：重算各 pane 矩形并 resize 终端的 pty + 模型，表面归零触发全量
    fn resize(&self, cols: usize, rows: usize) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        st.cols = cols;
        st.rows = rows;
        recompute_rects(&mut st);
        st.surface.resize(cols, rows);
        st.seqno = 0;
        st.last_sep = None;
        st.last_status = String::new();
        for id in 0..st.panes.len() {
            let rect = st.rects[id];
            let w = rect.w.max(1);
            let h = rect.h.max(1);
            // 全程持有 terminal 锁（master.resize 之前就锁）：
            // master.resize（ConPTY）触发 repaint 后，reader 线程读到的
            // repaint 字节因 terminal 锁被持有而无法 feed，阻塞到
            // terminal.resize（rewrap）完成之后——此时 terminal 已 rewrap
            // 完毕，reader 再 feed 的 repaint（被跳过）不会与 rewrap
            // 结果混合。若先 master.resize 再锁 terminal，reader 会在
            // 间隙把 repaint feed 进旧尺寸 terminal，污染 rewrap 结果。
            let mut term = st.panes[id].terminal.lock().unwrap();
            // resize 前记录视口顶部稳定行（保持滚动位置）
            let screen = term.screen();
            let prev_total = screen.scrollback_rows();
            let prev_rows = screen.physical_rows;
            let offset = *st.panes[id].view_offset.lock().unwrap();
            let prev_top_phys = prev_total.saturating_sub(offset).saturating_sub(prev_rows);
            let prev_top_stable = screen.phys_to_stable_row_index(prev_top_phys);
            let _ = screen;
            if let Some(m) = st.panes[id].master.lock().unwrap().as_ref() {
                m.resize(PtySize {
                    rows: h as u16,
                    cols: w as u16,
                    pixel_width: 0,
                    pixel_height: 0,
                })
                .map_err(|e| PyRuntimeError::new_err(format!("resize 失败: {e:#}")))?;
            }
            // 标记 ConPTY repaint pending：resize 后的纯重绘（无窗口尺寸）将被
            // reader 跳过，防止窄化 wrap 段污染 scrollback；遇到第一个非
            // repaint 内容自动清除（reader 线程逻辑）。
            st.panes[id]
                .repaint_pending
                .store(true, Ordering::SeqCst);
            term.resize(TerminalSize {
                rows: h,
                cols: w,
                pixel_width: 0,
                pixel_height: 0,
                dpi: 0,
            });
            // 用稳定行恢复滚动位置：rewrap 后物理行数变化，但稳定行不变，
            // 找到原视口顶部稳定行在新布局中的物理位置，计算新 view_offset。
            let new_screen = term.screen();
            let new_total = new_screen.scrollback_rows();
            let new_rows = new_screen.physical_rows;
            let new_offset = new_screen
                .stable_row_to_phys(prev_top_stable)
                .map(|p| {
                    new_total
                        .saturating_sub(p)
                        .saturating_sub(new_rows)
                        .min(new_total.saturating_sub(new_rows))
                })
                .unwrap_or(0);
            *st.panes[id].view_offset.lock().unwrap() = new_offset;
            // 内容 reflow 后旧 Surface 格子失效：重置基线下帧全量重写；
            *st.panes[id].last_seqno.lock().unwrap() = None;
            *st.panes[id].last_view.lock().unwrap() = 0;
            // 释放 terminal 锁（ConPTY repaint 字节将在锁释放后被
            // reader 线程读取并跳过，terminal 已 rewrap 完毕）
            drop(term);
        }
        Ok(())
    }

    /// 增量渲染：把各 pane 合成进整屏 Surface → 增量 ANSI 字节 + 焦点光标整屏坐标。
    /// 返回 (bytes, cursor_row, cursor_col, cursor_visible)：
    /// - bytes：增量 ANSI 序列（含 CUP 定位，坐标 1-based 终端语义）；
    /// - cursor_row/cursor_col：焦点光标整屏坐标，**0-based**（与 bytes 内
    ///   1-based CUP 不同，二者并存，调用方注意区分）；计入视图滚动偏移，
    ///   滚出可见区时 visible=False；
    /// - 未变化帧 bytes 为空（但光标可能仍需要重绘）。
    fn render(&self) -> PyResult<(Vec<u8>, usize, usize, bool)> {
        let mut st = self.inner.lock().unwrap();
        if st.surface.dimensions() != (st.cols, st.rows) {
            let sc = st.cols;
            let sr = st.rows;
            st.surface.resize(sc, sr);
            st.seqno = 0;
        }
        let n = st.panes.len();
        for id in 0..n {
            compose_pane(&mut st, id);
        }
        // 分隔线（左右分屏预留列）——仅布局变化时重画
        if st.sep && n == 2 {
            let sep_col = st.rects[0].w; // 左 pane 宽 = 分隔线列
            let avail = st.rects[0].h;
            if st.last_sep != Some((sep_col, avail)) {
                for y in 0..avail {
                    st.surface.add_change(Change::CursorPosition {
                        x: Position::Absolute(sep_col),
                        y: Position::Absolute(y),
                    });
                    st.surface.add_change(Change::AllAttributes(CellAttributes::default()));
                    st.surface.add_change(Change::Text("│".to_string()));
                }
                st.last_sep = Some((sep_col, avail));
            }
        } else {
            st.last_sep = None;
        }
        // 状态栏（底部预留行）——仅文本/行高变化时重画
        if st.status_rows > 0 {
            let row = st.rows.saturating_sub(st.status_rows);
            if st.last_status != st.status || st.last_status_rows != st.status_rows {
                let cols = st.cols;
                let status = st.status.clone();
                draw_status_text(&mut st.surface, row, cols, &status);
                st.last_status = status;
                st.last_status_rows = st.status_rows;
            }
        } else {
            st.last_status_rows = 0;
        }
        let (newseq, changes) = st.surface.get_changes(st.seqno);
        // Cow 借用了 surface；先转 owned，断开借用再更新 seqno/flush
        let changes = changes.into_owned();
        st.seqno = newseq;
        let bytes = render_changes_bytes(&changes, st.cols, st.rows)?;
        st.surface.flush_changes_older_than(newseq);
        // 焦点 pane 光标 → 整屏坐标（计入视图滚动偏移：滚动查看历史时
        // 可见区上移 offset 行，光标随内容平移；滚出可见区则隐藏）
        let rect = st.rects[st.focused];
        let pane = &st.panes[st.focused];
        let term = pane.terminal.lock().unwrap();
        let c = term.cursor_pos();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = pane_view_offset(pane, total, rows);
        let crow_screen = (c.y.max(0) as usize).saturating_add(offset);
        let vis = matches!(c.visibility, CursorVisibility::Visible) && crow_screen < rows;
        let crow = rect.y.saturating_add(crow_screen);
        // 列号 clamp 到 pane 矩形内：光标写满整行后 c.x = pane 宽（边界外），
        // 直接定位会超界被宿主 clamp 到行尾（表现为光标跳到最末列）
        let ccol = rect
            .x
            .saturating_add(c.x as usize)
            .min(rect.x.saturating_add(rect.w).saturating_sub(1));
        Ok((bytes, crow, ccol, vis))
    }

    /// 键盘按下：编码到焦点 pane 并下发其 pty，返回编码字节。
    /// key 合法取值：Up/Down/Left/Right/Home/End/Insert/Delete/PageUp/PageDown/
    /// Backspace/Tab/Enter/Esc/Space/F1-F24/单字符；mods 为 KeyModifiers 位
    /// （SHIFT=2 ALT=4 CTRL=8）。
    fn key_down(&self, key: &str, mods: u16) -> PyResult<Vec<u8>> {
        let pane_id = self.inner.lock().unwrap().focused;
        self.pane_key_down(pane_id, key, mods)
    }

    /// 键盘抬起：编码到焦点 pane 并下发其 pty，返回编码字节
    fn key_up(&self, key: &str, mods: u16) -> PyResult<Vec<u8>> {
        let pane_id = self.inner.lock().unwrap().focused;
        self.pane_key_up(pane_id, key, mods)
    }

    /// 鼠标事件（整屏坐标）：命中 pane → 坐标换算到 pane 内 → 编码下发，返回编码字节。
    /// kind: "press" | "release" | "move"；button: "left" | "middle" | "right" |
    /// "wheel_up" | "wheel_down" | "none"；mods 为 KeyModifiers 位（SHIFT=2 ALT=4 CTRL=8）。
    /// 坐标未命中任何 pane（分隔线/状态栏/屏幕外）抛 RuntimeError——调用方应先用
    /// pane_at() 判定命中（pane_at 同场景返回 None）。
    #[pyo3(signature = (x, y, kind="press", button="left", mods=0))]
    fn mouse(&self, x: usize, y: i64, kind: &str, button: &str, mods: u16) -> PyResult<Vec<u8>> {
        let (pane_id, lx, ly) = {
            let st = self.inner.lock().unwrap();
            let id = hit_test(&st, x, y.max(0) as usize).ok_or_else(|| {
                PyRuntimeError::new_err(format!("坐标 ({x},{y}) 未命中任何 pane"))
            })?;
            let r = st.rects[id];
            (id, x.saturating_sub(r.x), y.saturating_sub(r.y as i64))
        };
        self.pane_mouse(pane_id, lx, ly, kind, button, mods)
    }

    /// 滚动焦点 pane 的视图
    fn scroll(&self, delta: i64) -> PyResult<()> {
        let pane_id = self.inner.lock().unwrap().focused;
        self.pane_scroll(pane_id, delta)
    }

    /// 焦点 pane 回落到底部
    fn scroll_to_bottom(&self) -> PyResult<()> {
        let pane_id = self.inner.lock().unwrap().focused;
        self.pane_scroll_to_bottom(pane_id)
    }

    /// 命中测试：整屏坐标 (x,y) → 命中的 pane id；未命中（含分隔线/状态栏）返回 None
    fn pane_at(&self, x: usize, y: i64) -> Option<usize> {
        let st = self.inner.lock().unwrap();
        hit_test(&st, x, y.max(0) as usize)
    }

    /// 非阻塞查询子进程退出码；None = 仍在运行或未 spawn
    fn pane_try_wait(&self, pane_id: usize) -> PyResult<Option<u32>> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let mut child = pane.child.lock().unwrap();
        Ok(match child.as_mut().and_then(|c| c.try_wait().ok()?) {
            Some(status) => Some(status.exit_code()),
            None => None,
        })
    }

    // ---- 选区（整屏坐标入口 → 命中 pane → pane 内坐标 → stable）-----------

    /// 区域选择（整屏坐标）：anchor → end，矩形内全部文本
    #[pyo3(signature = (pane_id, anchor_x, anchor_y, end_x, end_y))]
    fn pane_selection_set(
        &self,
        pane_id: usize,
        anchor_x: usize,
        anchor_y: i64,
        end_x: usize,
        end_y: i64,
    ) -> PyResult<()> {
        let (a, e) = (
            self.pane_screen_to_stable(pane_id, anchor_x, anchor_y)?,
            self.pane_screen_to_stable(pane_id, end_x, end_y)?,
        );
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        pane.selection.lock().unwrap().set_region(a, e);
        Ok(())
    }

    /// 双击选词（整屏坐标）
    fn pane_selection_select_word(&self, pane_id: usize, x: usize, y: i64) -> PyResult<()> {
        let (row, col) = self.pane_screen_to_stable(pane_id, x, y)?;
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let term = pane.terminal.lock().unwrap();
        let screen = term.screen();
        pane.selection.lock().unwrap().select_word(&screen, row, col);
        Ok(())
    }

    /// 三击选行（整屏坐标）
    fn pane_selection_select_line(&self, pane_id: usize, x: usize, y: i64) -> PyResult<()> {
        let (row, _col) = self.pane_screen_to_stable(pane_id, x, y)?;
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        pane.selection.lock().unwrap().select_line(row, 0);
        Ok(())
    }

    /// 当前选区纯文本（无选区返回空串）
    fn pane_selection_text(&self, pane_id: usize) -> PyResult<String> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let term = pane.terminal.lock().unwrap();
        let screen = term.screen();
        let text = pane.selection.lock().unwrap().text(&screen);
        Ok(text)
    }

    /// 是否有活动选区
    fn pane_selection_active(&self, pane_id: usize) -> PyResult<bool> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let active = pane.selection.lock().unwrap().is_active();
        Ok(active)
    }

    /// 清除指定 pane 的选区
    fn pane_selection_clear(&self, pane_id: usize) -> PyResult<()> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        pane.selection.lock().unwrap().clear();
        Ok(())
    }

    /// 设置焦点 pane 的剪贴板回调（OSC 52）：回调对象会保持存活直至替换。
    /// 实际设置到**所有** pane 的终端（焦点切换后回调不丢失）。
    fn set_focus_selection_callback(&self, py: Python, callback: Py<PyAny>) -> PyResult<()> {
        let panes = self.inner.lock().unwrap().panes.clone();
        for p in &panes {
            let clip: std::sync::Arc<dyn Clipboard> =
                std::sync::Arc::new(PyClipboard(callback.clone_ref(py)));
            p.terminal.lock().unwrap().set_clipboard(&clip);
        }
        Ok(())
    }

    /// 模式感知粘贴下发到指定 pane（bracketed paste 自动包裹）
    fn pane_send_paste(&self, pane_id: usize, text: &str) -> PyResult<()> {
        let pane = {
            let st = self.inner.lock().unwrap();
            Self::get_pane(&st, pane_id)?
        };
        let _ = take_capture(&pane.capture);
        {
            let mut term = pane.terminal.lock().unwrap();
            term.send_paste(text)
                .map_err(|e| PyRuntimeError::new_err(format!("send_paste 失败: {e}")))?;
            term.flush_sync();
        }
        let bytes = take_capture(&pane.capture);
        write_to_writer(&pane.writer, &bytes)?;
        Ok(())
    }

    /// 模式感知粘贴下发到焦点 pane（bracketed paste 自动包裹）
    fn send_paste(&self, text: &str) -> PyResult<()> {
        let pane_id = self.inner.lock().unwrap().focused;
        self.pane_send_paste(pane_id, text)
    }

    /// 强制下一帧全量重绘（录制暂停恢复/收敛帧用）：重置各 pane 渲染基线
    fn force_repaint(&self) -> PyResult<()> {
        let mut st = self.inner.lock().unwrap();
        st.seqno = 0;
        st.last_sep = None;
        st.last_status = String::new();
        for p in &st.panes {
            *p.last_seqno.lock().unwrap() = None;
            *p.last_view.lock().unwrap() = 0;
        }
        Ok(())
    }

    /// 关闭单个 pane：终止子进程 + 释放 HPCON，并从布局中移除（重算
    /// 矩形、修正焦点），避免关闭后仍渲染冻结区域。
    fn close_pane(&self, pane_id: usize) -> PyResult<()> {
        let pane = {
            let st = self.inner.lock().unwrap();
            match st.panes.get(pane_id) {
                Some(p) => p.clone(),
                // 已关闭/不存在：幂等 no-op
                None => return Ok(()),
            }
        };
        close_pane_inner(&pane);
        let mut st = self.inner.lock().unwrap();
        st.panes.remove(pane_id);
        // 重建布局树（按剩余 pane 数）
        st.layout = match st.panes.len() {
            0 | 1 => Layout::Leaf { pane_id: 0 },
            _ => {
                let l0 = Layout::Leaf { pane_id: 0 };
                let l1 = Layout::Leaf { pane_id: 1 };
                Layout::Split { dir: SplitDir::LR, a: Box::new(l0), b: Box::new(l1) }
            }
        };
        recompute_rects(&mut st);
        st.last_sep = None;
        st.last_status = String::new();
        if st.focused >= st.panes.len() {
            st.focused = st.panes.len().saturating_sub(1);
        }
        Ok(())
    }

    /// 关闭所有 pane
    fn close(&self) {
        let panes = self.inner.lock().unwrap().panes.clone();
        for p in panes {
            close_pane_inner(&p);
        }
    }
}

/// 关闭单个 pane 的内部实现（幂等）
fn close_pane_inner(pane: &Pane) {
    if pane.closed.swap(true, Ordering::SeqCst) {
        return;
    }
    if let Some(child) = pane.child.lock().unwrap().as_mut() {
        let _ = child.kill();
    }
    *pane.writer.lock().unwrap() = None;
    // 取消 reader 线程的同步阻塞 ReadFile，再释放 HPCON，避免死锁
    #[cfg(windows)]
    {
        cancel_reader_thread(&pane.reader_thread, &pane.eof);
    }
    *pane.master.lock().unwrap() = None;
    *pane._slave.lock().unwrap() = None;
}


