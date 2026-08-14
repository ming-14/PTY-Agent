//! pywezterm —— wezterm 的 Python 绑定（pyo3 / abi3）
//!
//! 把 wezterm 库化为独立 Python 库：伪终端引擎（portable_pty）与
//! 终端模拟器（wezterm-term），供任意 Python 程序调用。

mod pty;
mod term;

use pyo3::prelude::*;

/// 返回绑定库版本
#[pyfunction]
fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// 初始化 pywezterm 模块
#[pymodule]
fn pywezterm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_class::<term::PyTerminal>()?;
    m.add_class::<pty::PyPty>()?;
    Ok(())
}
