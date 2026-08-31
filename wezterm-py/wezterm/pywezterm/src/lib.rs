//! pywezterm —— wezterm 的 Python 绑定（pyo3 / abi3）
//!
//! 把 wezterm 库化为独立 Python 库：伪终端引擎（portable_pty）与
//! 终端模拟器（wezterm-term），供任意 Python 程序调用。

#[cfg(windows)]
mod clipboard;
#[cfg(windows)]
mod console_input;
mod mux;
mod pty;
mod render;
mod surface_render;
mod term;

use pyo3::prelude::*;

/// 返回绑定库版本
#[pyfunction]
fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// 光标定位序列（0-based → 1-based CUP + show/hide）
#[pyfunction]
fn cursor_seq(row: usize, col: usize, visible: bool) -> String {
    let mut s = format!("\x1b[{};{}H", row + 1, col + 1);
    s.push_str(if visible { "\x1b[?25h" } else { "\x1b[?25l" });
    s
}

/// 初始化 pywezterm 模块
#[pymodule]
fn pywezterm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(cursor_seq, m)?)?;
    #[cfg(windows)]
    m.add_function(wrap_pyfunction!(clipboard::clipboard_read, m)?)?;
    #[cfg(windows)]
    m.add_function(wrap_pyfunction!(clipboard::clipboard_write, m)?)?;
    m.add_class::<term::PyTerminal>()?;
    m.add_class::<pty::PyPty>()?;
    m.add_class::<surface_render::PySurface>()?;
    m.add_class::<mux::PyMux>()?;
    #[cfg(windows)]
    m.add_class::<console_input::PyConsoleInput>()?;
    Ok(())
}
