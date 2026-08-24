//! 伪终端引擎（portable_pty）Python 绑定
//!
//! 提供 Pty：openpty 创建伪控制台、spawn 启动子进程、read/write、
//! resize、HPCON 暴露（沙箱/外部 spawn 用）、child pid/句柄、退出码。
//!
//! 读取采用「内部 reader 线程 + 缓冲队列」模型：
//! - 阻塞 read 发生在库内部线程，Python 侧 `read(n, timeout)` 释放 GIL
//!   轮询缓冲，避免阻塞其他 Python 线程；
//! - EOF（管道关闭）后置 eof 标志，read 返回 b""。

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
#[cfg(windows)]
use pyo3::types::PyAnyMethods;
use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize, SlavePty};
use std::collections::{HashMap, VecDeque};
use std::ffi::OsString;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

/// 伪终端内部状态（master/slave 用 Option 便于 close 时取出关闭
/// 以解除 reader 阻塞；二者持同一 HPCON 的 Arc 引用，须一起释放）
struct PtyInner {
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    _slave: Mutex<Option<Box<dyn SlavePty + Send>>>,
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    child: Mutex<Option<Box<dyn Child + Send + Sync>>>,
    buf: Arc<Mutex<VecDeque<u8>>>,
    eof: Arc<AtomicBool>,
    closed: Arc<AtomicBool>,
    /// reader 线程的（复制的）原生句柄，close 时用于 CancelSynchronousIo
    /// 取消其阻塞的同步 ReadFile（仅 Windows 设置）
    reader_thread: Arc<Mutex<Option<usize>>>,
}

/// 侧载 conpty.dll + OpenConsole.exe（与模块包同目录），让 portable-pty
/// 优先使用 wezterm 自带 OpenConsole 宿主而非系统 conhost。
/// 须在模块初始化（__file__ 可用）后调用一次；Mux 建 pane 复用同一份。
pub(crate) fn ensure_conpty_dir(py: Python) {
    #[cfg(windows)]
    {
        if portable_pty::CONPTY_DIR.get().is_none() {
            if let Ok(m) = py.import("pywezterm") {
                if let Ok(fname) = m.getattr("__file__") {
                    if let Ok(s) = fname.extract::<String>() {
                        if let Some(dir) = std::path::Path::new(&s).parent() {
                            portable_pty::set_conpty_dir(dir.to_path_buf());
                        }
                    }
                }
            }
        }
    }
}

/// 复制当前线程句柄（GetCurrentThread 为伪句柄，须复制成跨线程有效句柄），
/// 供 close 时 CancelSynchronousIo 取消该线程的阻塞读。返回句柄（0 = 失败）。
#[cfg(windows)]
pub(crate) fn duplicate_current_thread_handle() -> usize {
    unsafe {
        let mut h = std::ptr::null_mut();
        let ok = winapi::um::handleapi::DuplicateHandle(
            winapi::um::processthreadsapi::GetCurrentProcess(),
            winapi::um::processthreadsapi::GetCurrentThread(),
            winapi::um::processthreadsapi::GetCurrentProcess(),
            &mut h,
            0,
            0,
            winapi::um::winnt::DUPLICATE_SAME_ACCESS,
        );
        if ok != 0 { h as usize } else { 0 }
    }
}

/// 关闭已复制的 reader 线程句柄（reader 线程退出时调用）
#[cfg(windows)]
pub(crate) fn close_thread_handle(h: usize) {
    unsafe {
        winapi::um::handleapi::CloseHandle(h as winapi::um::winnt::HANDLE);
    }
}

/// 取消 reader 线程的同步阻塞 ReadFile（按线程取消），并用 eof 标志确认
/// reader 已从 read 返回后再继续，规避「取消瞬间 reader 恰在两次 read 之间」
/// 的竞态。最多重试 200ms。
#[cfg(windows)]
pub(crate) fn cancel_reader_thread(
    reader_thread: &Arc<Mutex<Option<usize>>>,
    eof: &Arc<AtomicBool>,
) {
    let h = reader_thread
        .lock()
        .unwrap()
        .map(|h| h as winapi::um::winnt::HANDLE);
    if let Some(h) = h {
        for _ in 0..200 {
            unsafe {
                winapi::um::ioapiset::CancelSynchronousIo(h);
            }
            if eof.load(Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
    }
}

/// 伪终端实例（多线程安全：各字段 Mutex 包裹，Sync）
#[pyclass(name = "Pty")]
pub struct PyPty {
    inner: Arc<PtyInner>,
}

#[pymethods]
impl PyPty {
    /// 创建伪终端（仅创建伪控制台，不 spawn 子进程）
    #[new]
    #[pyo3(signature = (cols=80, rows=24))]
    fn new(py: Python, cols: u16, rows: u16) -> PyResult<Self> {
        // 首次创建 Pty 时解析侧载目录（模块已初始化、__file__ 可用；
        // pymodule init 阶段 __file__ 尚未设置，故不能放那里）。
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
        let buf: Arc<Mutex<VecDeque<u8>>> = Arc::new(Mutex::new(VecDeque::new()));
        let eof = Arc::new(AtomicBool::new(false));
        let closed = Arc::new(AtomicBool::new(false));
        // 先取 writer/reader（避免 master 移入结构体后无法再借用）
        let writer = pair
            .master
            .take_writer()
            .map_err(|e| PyRuntimeError::new_err(format!("take_writer 失败: {e:#}")))?;
        let mut reader = pair
            .master
            .try_clone_reader()
            .map_err(|e| PyRuntimeError::new_err(format!("try_clone_reader 失败: {e:#}")))?;
        let reader_thread: Arc<Mutex<Option<usize>>> = Arc::new(Mutex::new(None));
        let inner = Arc::new(PtyInner {
            master: Mutex::new(Some(pair.master)),
            _slave: Mutex::new(Some(pair.slave)),
            writer: Mutex::new(Some(writer)),
            child: Mutex::new(None),
            buf: buf.clone(),
            eof: eof.clone(),
            closed: closed.clone(),
            reader_thread: reader_thread.clone(),
        });
        // reader 线程：阻塞读 pty 输出 → 缓冲队列；EOF 或 closed 置标志后退出。
        // 线程内先复制自身句柄供 close 时 CancelSynchronousIo 取消阻塞读，
        // 避免 ClosePseudoConsole 等待 pending read 造成死锁。
        std::thread::spawn(move || {
            #[cfg(windows)]
            {
                let h = duplicate_current_thread_handle();
                if h != 0 {
                    *reader_thread.lock().unwrap() = Some(h);
                }
            }
            loop {
                if closed.load(Ordering::SeqCst) {
                    eof.store(true, Ordering::SeqCst);
                    break;
                }
                let mut tmp = [0u8; 8192];
                match reader.read(&mut tmp) {
                    Ok(0) => {
                        eof.store(true, Ordering::SeqCst);
                        break;
                    }
                    Ok(n) => buf.lock().unwrap().extend(tmp[..n].iter().copied()),
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => {
                        eof.store(true, Ordering::SeqCst);
                        break;
                    }
                }
            }
            #[cfg(windows)]
            {
                if let Some(h) = reader_thread.lock().unwrap().take() {
                    close_thread_handle(h);
                }
            }
        });
        Ok(Self { inner })
    }

    /// 启动子进程到伪终端，返回 (pid, 进程句柄)
    /// raw_cmdline（Windows 可选）：提供时整个命令行原样传递（绕过 argv
    /// 引号序列化），供 cmd.exe /c 等自解析命令行的程序保留引号语义。
    #[pyo3(signature = (argv, cwd=None, env=None, raw_cmdline=None))]
    fn spawn(
        &self,
        argv: Vec<String>,
        cwd: Option<String>,
        env: Option<HashMap<String, String>>,
        raw_cmdline: Option<String>,
    ) -> PyResult<(u32, usize)> {
        if self.inner.closed.load(Ordering::SeqCst) {
            return Err(PyRuntimeError::new_err("Pty 已关闭"));
        }
        let args: Vec<OsString> = argv.into_iter().map(OsString::from).collect();
        let mut builder = CommandBuilder::from_argv(args);
        #[cfg(windows)]
        if let Some(raw) = raw_cmdline {
            builder.set_raw_cmdline(raw);
        }
        if let Some(cwd) = cwd {
            builder.cwd(cwd);
        }
        if let Some(env) = env {
            for (k, v) in env {
                builder.env(k, v);
            }
        }
        let child = self
            .inner
            ._slave
            .lock()
            .unwrap()
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("Pty 已关闭"))?
            .spawn_command(builder)
            .map_err(|e| PyRuntimeError::new_err(format!("spawn 失败: {e:#}")))?;
        let pid = child.process_id().unwrap_or(0);
        #[cfg(windows)]
        let handle = child.as_raw_handle().unwrap_or(std::ptr::null_mut()) as usize;
        #[cfg(not(windows))]
        let handle = 0;
        *self.inner.child.lock().unwrap() = Some(child);
        Ok((pid, handle))
    }

    /// 从伪终端读取最多 n 字节；EOF 返回 b""。
    /// timeout=None 阻塞直到有数据或 EOF；否则最多等待 timeout 秒。
    /// 轮询等待期间释放 GIL，不阻塞其他 Python 线程。
    #[pyo3(signature = (n=65536, timeout=None))]
    fn read(&self, py: Python, n: usize, timeout: Option<f64>) -> Vec<u8> {
        let n = n.max(1);
        let deadline = timeout.map(|t| std::time::Instant::now() + std::time::Duration::from_secs_f64(t));
        loop {
            {
                let mut b = self.inner.buf.lock().unwrap();
                if !b.is_empty() {
                    let take = b.len().min(n);
                    return b.drain(..take).collect();
                }
                if self.inner.eof.load(Ordering::SeqCst)
                    || self.inner.closed.load(Ordering::SeqCst)
                {
                    return vec![];
                }
            }
            if let Some(d) = deadline {
                if std::time::Instant::now() >= d {
                    return vec![];
                }
            }
            py.detach(|| std::thread::sleep(std::time::Duration::from_millis(2)));
        }
    }

    /// 写入数据到伪终端
    fn write(&self, data: Vec<u8>) -> PyResult<()> {
        if self.inner.closed.load(Ordering::SeqCst) {
            return Ok(());
        }
        let mut w = self.inner.writer.lock().unwrap();
        match w.as_mut() {
            Some(w) => w
                .write_all(&data)
                .map_err(|e| PyRuntimeError::new_err(format!("write 失败: {e}"))),
            None => Err(PyRuntimeError::new_err("writer 已关闭")),
        }
    }

    /// 调整伪终端尺寸（列/行）
    fn resize(&self, cols: u16, rows: u16) -> PyResult<()> {
        let master = self.inner.master.lock().unwrap();
        let master = master
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("Pty 已关闭"))?;
        master
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| PyRuntimeError::new_err(format!("resize 失败: {e:#}")))?;
        Ok(())
    }

    /// 当前伪终端尺寸 (cols, rows)
    fn get_size(&self) -> (u16, u16) {
        let master = self.inner.master.lock().unwrap();
        match master.as_ref().and_then(|m| m.get_size().ok()) {
            Some(s) => (s.cols, s.rows),
            None => (0, 0),
        }
    }

    /// 底层 ConPTY HPCON 句柄（沙箱/外部 spawn 用），无则 None
    #[cfg(windows)]
    fn hpcon(&self) -> Option<usize> {
        let master = self.inner.master.lock().unwrap();
        master.as_ref().and_then(|m| m.hpcon().map(|h| h as usize))
    }

    /// 子进程 PID（未 spawn 则为 None）
    fn child_pid(&self) -> Option<u32> {
        self.inner
            .child
            .lock()
            .unwrap()
            .as_ref()
            .and_then(|c| c.process_id())
    }

    /// 子进程进程句柄（Job 注册用），未 spawn 则为 None
    #[cfg(windows)]
    fn child_handle(&self) -> Option<usize> {
        self.inner
            .child
            .lock()
            .unwrap()
            .as_ref()
            .and_then(|c| c.as_raw_handle())
            .map(|h| h as usize)
    }

    /// 非阻塞查询子进程退出码；None = 仍在运行或未 spawn
    fn try_wait(&self) -> Option<u32> {
        let mut child = self.inner.child.lock().unwrap();
        let child = child.as_mut()?;
        match child.try_wait() {
            Ok(Some(status)) => Some(status.exit_code()),
            _ => None,
        }
    }

    /// 终止子进程
    fn kill(&self) {
        if let Some(child) = self.inner.child.lock().unwrap().as_mut() {
            let _ = child.kill();
        }
    }

    /// 关闭伪终端（终止子进程 + 取消 reader 阻塞读 + 释放 master/slave
    /// 关闭 HPCON 解除 reader 阻塞，幂等）
    fn close(&self) {
        if self.inner.closed.swap(true, Ordering::SeqCst) {
            return;
        }
        if let Some(child) = self.inner.child.lock().unwrap().as_mut() {
            let _ = child.kill();
        }
        *self.inner.writer.lock().unwrap() = None;
        // 取消 reader 线程的同步阻塞 ReadFile，再关闭伪控制台，避免
        // ClosePseudoConsole 与 pending read 相互等待（ConPTY 死锁）。
        #[cfg(windows)]
        {
            cancel_reader_thread(&self.inner.reader_thread, &self.inner.eof);
        }
        // 释放 master 与 slave 持有的同一 HPCON 引用 → Inner drop →
        // ClosePseudoConsole → conhost 退出 → reader 线程退出
        *self.inner.master.lock().unwrap() = None;
        *self.inner._slave.lock().unwrap() = None;
    }
}
