//! 系统剪贴板读写绑定（winapi 实现）
//!
//! 宿主侧剪贴板读写（选区复制 / 宿主粘贴 / OSC 52 应用剪贴板写），
//! 统一由绑定层提供：
//! - clipboard_read()：读纯文本（CF_UNICODETEXT）
//! - clipboard_write(text)：写纯文本（UTF-16LE）
//!
//! 与 ConsoleInput 同级的宿主 OS 交互；非 Windows 返回空/静默忽略。

use pyo3::prelude::*;
#[cfg(windows)]
use winapi::um::winbase::{GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE};
#[cfg(windows)]
use winapi::um::winuser::{
    CF_UNICODETEXT, CloseClipboard, EmptyClipboard, GetClipboardData, OpenClipboard,
    SetClipboardData,
};

/// 剪贴板文本写入（UTF-16LE + NUL 结尾）
#[pyfunction]
pub fn clipboard_write(text: &str) -> PyResult<()> {
    #[cfg(windows)]
    {
        if text.is_empty() {
            return Ok(());
        }
        // UTF-16LE 编码（含 NUL 结尾）
        let mut data: Vec<u8> = text
            .encode_utf16()
            .flat_map(|u| u.to_le_bytes())
            .collect();
        data.extend_from_slice(&[0u8, 0u8]);
        let size = data.len();
        // 打开剪贴板失败（被其他进程占用）静默忽略——尽力而为
        if unsafe { OpenClipboard(std::ptr::null_mut()) } == 0 {
            return Ok(());
        }
        let result = (|| -> PyResult<()> {
            unsafe { EmptyClipboard() };
            let h = unsafe { GlobalAlloc(GMEM_MOVEABLE, size) };
            if h.is_null() {
                return Ok(()); // 分配失败：尽力而为
            }
            let p = unsafe { GlobalLock(h) };
            if !p.is_null() {
                unsafe {
                    std::ptr::copy_nonoverlapping(data.as_ptr(), p as *mut u8, size);
                }
                unsafe { GlobalUnlock(h) };
            }
            unsafe { SetClipboardData(CF_UNICODETEXT, h) };
            Ok(())
        })();
        unsafe { CloseClipboard() };
        result
    }
    #[cfg(not(windows))]
    {
        let _ = text;
        Ok(())
    }
}

/// 剪贴板文本读取（UTF-16LE），无文本返回空串
#[pyfunction]
pub fn clipboard_read() -> PyResult<String> {
    #[cfg(windows)]
    {
        if unsafe { OpenClipboard(std::ptr::null_mut()) } == 0 {
            return Ok(String::new());
        }
        let result = (|| -> PyResult<String> {
            let h = unsafe { GetClipboardData(CF_UNICODETEXT) };
            if h.is_null() {
                return Ok(String::new());
            }
            let p = unsafe { GlobalLock(h) };
            if p.is_null() {
                return Ok(String::new());
            }
            // 从 NUL 截断 UTF-16LE（上限 1M 字符防恶意超长剪贴板）
            let mut units: Vec<u16> = Vec::new();
            let ptr = p as *const u16;
            let mut i = 0usize;
            loop {
                let u = unsafe { *ptr.add(i) };
                if u == 0 {
                    break;
                }
                units.push(u);
                i += 1;
                if i > 1 << 20 {
                    break;
                }
            }
            unsafe { GlobalUnlock(h) };
            Ok(String::from_utf16_lossy(&units))
        })();
        unsafe { CloseClipboard() };
        result
    }
    #[cfg(not(windows))]
    {
        Ok(String::new())
    }
}
