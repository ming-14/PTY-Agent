//! Windows 控制台输入采集（winapi 实现）Python 绑定
//!
//! 把宿主持有控制台的输入采集交由绑定层实现，替代调用方手写 ctypes Win32：
//! - 构造时设置控制台模式：输出侧启用 VT 处理/禁用换行自动回车，输入侧
//!   原始按键事件（不吃 VT 输入转换、忽略快速编辑），并保存原模式供恢复；
//! - wait_input 阻塞等待输入事件；read_inputs 批量读出全部待处理记录，
//!   归一化为 (kind, ...) tuple 列表返回（key/mouse/resize），调用方无需
//!   接触任何 Win32 结构；
//! - 鼠标抬键按钮补全（last_pressed）状态持有在绑定内部，跨批次保持。
//!
//! 归一化语义：键名走 pywezterm 键名（Backspace/Tab/Enter/.../F1-F24），普通键取 Unicode
//! 字符，Ctrl+字母归一成字母 + CTRL 位，修饰键自身（uChar=0）忽略，抬键
//! 事件保留 down=False 形状；鼠标 press/move/release + wheel_up/down。

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::sync::Mutex;

use winapi::shared::minwindef::{DWORD, WORD};
use winapi::um::consoleapi::{
    GetConsoleMode, GetConsoleOutputCP, GetNumberOfConsoleInputEvents, ReadConsoleInputW,
    SetConsoleMode,
};
use winapi::um::wincon::SetConsoleOutputCP;
use winapi::um::handleapi::INVALID_HANDLE_VALUE;
use winapi::um::processenv::GetStdHandle;
use winapi::um::synchapi::WaitForSingleObject;
use winapi::um::winbase::{STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, WAIT_OBJECT_0};
use winapi::um::wincon::{
    CONSOLE_SCREEN_BUFFER_INFO, DISABLE_NEWLINE_AUTO_RETURN, DOUBLE_CLICK, ENABLE_EXTENDED_FLAGS,
    ENABLE_MOUSE_INPUT, ENABLE_PROCESSED_OUTPUT, ENABLE_VIRTUAL_TERMINAL_PROCESSING,
    ENABLE_WINDOW_INPUT, FROM_LEFT_1ST_BUTTON_PRESSED, FROM_LEFT_2ND_BUTTON_PRESSED, INPUT_RECORD,
    KEY_EVENT, KEY_EVENT_RECORD, LEFT_ALT_PRESSED, LEFT_CTRL_PRESSED, MOUSE_EVENT,
    MOUSE_EVENT_RECORD, MOUSE_MOVED, MOUSE_WHEELED, RIGHT_ALT_PRESSED, RIGHT_CTRL_PRESSED,
    SHIFT_PRESSED, RIGHTMOST_BUTTON_PRESSED, WINDOW_BUFFER_SIZE_EVENT,
};
use winapi::um::winnt::HANDLE;
use winapi::um::winuser::{
    VK_BACK, VK_DELETE, VK_DOWN, VK_END, VK_ESCAPE, VK_F1, VK_F24, VK_HOME, VK_INSERT, VK_LEFT,
    VK_NEXT, VK_PRIOR, VK_RETURN, VK_RIGHT, VK_TAB, VK_UP,
};

// pywezterm KeyModifiers 位（wezterm-input-types）：SHIFT=2 ALT=4 CTRL=8
const MOD_SHIFT: u16 = 2;
const MOD_ALT: u16 = 4;
const MOD_CTRL: u16 = 8;

/// 输入侧模式：原始按键事件（不启用 VT 输入转换，避免按键被吞）+ 忽略快速编辑
const INPUT_MODE: DWORD = ENABLE_EXTENDED_FLAGS | ENABLE_WINDOW_INPUT | ENABLE_MOUSE_INPUT;
/// 输出侧模式：VT 处理 + 禁用换行自动回车（ANSI 重绘用）
const OUTPUT_MODE: DWORD =
    ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING | DISABLE_NEWLINE_AUTO_RETURN;
/// UTF-8 代码页：渲染字节统一 UTF-8（宿主 ConPTY 按 936 解析会把多字节字符变乱码）
const CP_UTF8: u32 = 65001;

// ---- 归一化纯函数（可单测，不依赖句柄）----------------------------------

/// Win32 dwControlKeyState → pywezterm KeyModifiers 位
fn mods_from_control_state(state: DWORD) -> u16 {
    let mut mods = 0;
    if state & SHIFT_PRESSED != 0 {
        mods |= MOD_SHIFT;
    }
    if state & (LEFT_ALT_PRESSED | RIGHT_ALT_PRESSED) != 0 {
        mods |= MOD_ALT;
    }
    if state & (LEFT_CTRL_PRESSED | RIGHT_CTRL_PRESSED) != 0 {
        mods |= MOD_CTRL;
    }
    mods
}

/// 虚拟键码 → pywezterm 键名（Backspace/Up/F1...）；None 表示走字符通道
fn key_name_from_vk(vk: WORD) -> Option<String> {
    let name = match vk as i32 {
        VK_BACK => "Backspace",
        VK_TAB => "Tab",
        VK_RETURN => "Enter",
        VK_ESCAPE => "Esc",
        VK_PRIOR => "PageUp",
        VK_NEXT => "PageDown",
        VK_END => "End",
        VK_HOME => "Home",
        VK_LEFT => "Left",
        VK_UP => "Up",
        VK_RIGHT => "Right",
        VK_DOWN => "Down",
        VK_INSERT => "Insert",
        VK_DELETE => "Delete",
        _ if (VK_F1..=VK_F24).contains(&(vk as i32)) => {
            return Some(format!("F{}", vk as i32 - VK_F1 + 1));
        }
        _ => return None,
    };
    Some(name.to_string())
}

/// KEY_EVENT_RECORD → (key, mods, down)；无法表达的按键返回 None。
///
/// 特殊键（方向/功能/编辑）按 VK 码走；普通键取 Unicode 字符；修饰键自身
/// （VK_SHIFT/CONTROL/MENU 等）的 uChar 是 0，必须显式排除防止 NUL 下发；
/// Ctrl+字母 在 Windows 上 uChar 是控制码（0x01-0x1A），归一成对应字母并
/// 保留 CTRL 位，保证后续模式感知编码正确。
fn normalize_key(rec: &KEY_EVENT_RECORD) -> Option<(String, u16, bool)> {
    let mods = mods_from_control_state(rec.dwControlKeyState);
    let down = rec.bKeyDown != 0;
    if let Some(name) = key_name_from_vk(rec.wVirtualKeyCode) {
        return Some((name, mods, down));
    }
    let ch = char::from_u32(*unsafe { rec.uChar.UnicodeChar() } as u32)?;
    if ch == '\0' {
        return None;
    }
    if mods & MOD_CTRL != 0 && ('\x01'..='\x1a').contains(&ch) {
        let letter = char::from_u32(ch as u32 + 0x60)?;
        return Some((letter.to_string(), mods, down));
    }
    Some((ch.to_string(), mods, down))
}

/// 按钮状态位 → 鼠标按钮名；无按钮返回 "none"
fn mouse_button(state: DWORD) -> &'static str {
    if state & FROM_LEFT_1ST_BUTTON_PRESSED != 0 {
        "left"
    } else if state & RIGHTMOST_BUTTON_PRESSED != 0 {
        "right"
    } else if state & FROM_LEFT_2ND_BUTTON_PRESSED != 0 {
        "middle"
    } else {
        "none"
    }
}

/// MOUSE_EVENT_RECORD → ((x, y, kind, button, mods, click_count), 新的 last_pressed)。
///
/// 滚动：dwButtonState 高 16 位是带符号滚轮增量（±120），正上负下；
/// 双击不更新 last_pressed（保持按钮记忆供后续抬键补全）；
/// last_pressed：最后一次按下的按钮——Windows 抬键事件的 dwButtonState=0
/// 不携带被抬起的按钮号，而编码 release 必须带按钮（wezterm 要求该按钮
/// 处于按下记录中），否则应用收不到抬键、后续移动被误判为持续拖动。
///
/// click_count：连续点击次数（1=单击，2=双击，3=三击）。Windows 双击/三击
/// 序列中后续 press 带 DOUBLE_CLICK 标志，绑定层据此累加计数（替代宿主
/// 用时间窗口模拟），供双击选词/三击选行；普通 press 重置为 1。
fn normalize_mouse(
    rec: &MOUSE_EVENT_RECORD,
    last_pressed: &str,
    last_click_count: u32,
) -> ((usize, usize, String, String, u16, u32), String, u32) {
    let x = rec.dwMousePosition.X.max(0) as usize;
    let y = rec.dwMousePosition.Y.max(0) as usize;
    let mods = mods_from_control_state(rec.dwControlKeyState);
    let flags = rec.dwEventFlags;
    let state = rec.dwButtonState;
    let lp = last_pressed.to_string();
    if flags & MOUSE_WHEELED != 0 {
        let delta = (state >> 16) as i16;
        let button = if delta > 0 { "wheel_up" } else { "wheel_down" };
        return (
            (x, y, "press".to_string(), button.to_string(), mods, 1),
            lp,
            last_click_count,
        );
    }
    if flags & MOUSE_MOVED != 0 {
        // 移动事件带按钮状态：悬停为 none，按住按钮移动（拖动）为对应按钮
        return (
            (x, y, "move".to_string(), mouse_button(state).to_string(), mods, 1),
            lp,
            last_click_count,
        );
    }
    if flags & DOUBLE_CLICK != 0 {
        // 双击/三击序列：继续累加计数（系统已按时间/位置判定为连续点击）
        let count = if last_click_count >= 1 {
            last_click_count + 1
        } else {
            2
        };
        return (
            (x, y, "press".to_string(), mouse_button(state).to_string(), mods, count),
            lp,
            count,
        );
    }
    if state & 0x7 != 0 {
        let button = mouse_button(state);
        return (
            (x, y, "press".to_string(), button.to_string(), mods, 1),
            button.to_string(),
            1,
        );
    }
    let button = if last_pressed == "none" { "none" } else { last_pressed };
    (
        (x, y, "release".to_string(), button.to_string(), mods, 1),
        "none".to_string(),
        1,
    )
}

// ---- 控制台输入对象 -------------------------------------------------------

/// Windows 宿主控制台输入采集实例（pywezterm.ConsoleInput）
///
/// 构造即设置控制台模式（保存原模式）+ 输出代码页切到 UTF-8（保存原代码页）；
/// restore()/Drop 恢复。
/// 事件读取非阻塞（wait_input 先行等待），归一化结果不依赖调用方线程。
#[pyclass(name = "ConsoleInput")]
pub struct PyConsoleInput {
    hin: HANDLE,
    hout: HANDLE,
    orig_in: DWORD,
    orig_out: DWORD,
    orig_cp: u32,
    restored: Mutex<bool>,
    /// 最近按下的鼠标按钮（补全抬键事件缺失的按钮号），跨批次保持
    last_pressed: Mutex<String>,
    /// 连续点击计数（双击=2/三击=3），跨批次保持（Windows 双击序列跨事件批次）
    last_click_count: Mutex<u32>,
}

// 句柄是进程级资源（不随线程释放），可安全跨线程
unsafe impl Send for PyConsoleInput {}
unsafe impl Sync for PyConsoleInput {}

impl Drop for PyConsoleInput {
    fn drop(&mut self) {
        let _ = self.restore();
    }
}

/// 设置控制台模式，失败返回错误描述
fn set_mode(handle: HANDLE, mode: DWORD) -> PyResult<()> {
    let ok = unsafe { SetConsoleMode(handle, mode) };
    if ok == 0 {
        return Err(PyRuntimeError::new_err(format!(
            "SetConsoleMode 失败: {}",
            std::io::Error::last_os_error()
        )));
    }
    Ok(())
}

#[pymethods]
impl PyConsoleInput {
    /// 打开宿主控制台输入/输出句柄并设置模式（保存原模式供恢复）。
    /// 非控制台环境（stdio 被重定向）构造失败。
    #[new]
    fn new() -> PyResult<Self> {
        let hin = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
        let hout = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
        if hin.is_null() || hin == INVALID_HANDLE_VALUE || hout.is_null() || hout == INVALID_HANDLE_VALUE
        {
            return Err(PyRuntimeError::new_err("宿主控制台句柄不可用"));
        }
        let mut orig_in: DWORD = 0;
        let mut orig_out: DWORD = 0;
        if unsafe { GetConsoleMode(hin, &mut orig_in) } == 0 {
            return Err(PyRuntimeError::new_err(format!(
                "GetConsoleMode(stdin) 失败: {}",
                std::io::Error::last_os_error()
            )));
        }
        if unsafe { GetConsoleMode(hout, &mut orig_out) } == 0 {
            return Err(PyRuntimeError::new_err(format!(
                "GetConsoleMode(stdout) 失败: {}",
                std::io::Error::last_os_error()
            )));
        }
        set_mode(hout, OUTPUT_MODE)?;
        set_mode(hin, INPUT_MODE)?;
        // 渲染字节统一 UTF-8：把宿主控制台输出代码页切到 65001（保存原代码页供恢复）
        let orig_cp = unsafe { GetConsoleOutputCP() };
        unsafe { SetConsoleOutputCP(CP_UTF8) };
        Ok(Self {
            hin,
            hout,
            orig_in,
            orig_out,
            orig_cp,
            restored: Mutex::new(false),
            last_pressed: Mutex::new("none".to_string()),
            last_click_count: Mutex::new(1),
        })
    }

    /// 等待输入事件可用，返回是否有事件（False = 超时）
    fn wait_input(&self, ms: u32) -> bool {
        let r = unsafe { WaitForSingleObject(self.hin, ms) };
        r == WAIT_OBJECT_0
    }

    /// 读取全部待处理输入事件并归一化返回。
    /// 每项为 (kind, ...) tuple：
    /// - ("key", key, mods, down)
    /// - ("mouse", x, y, kind, button, mods)
    /// - ("resize",)
    fn read_inputs(&self, py: Python) -> PyResult<Vec<Py<PyAny>>> {
        let mut pending: DWORD = 0;
        if unsafe { GetNumberOfConsoleInputEvents(self.hin, &mut pending) } == 0 {
            return Err(PyRuntimeError::new_err(format!(
                "GetNumberOfConsoleInputEvents 失败: {}",
                std::io::Error::last_os_error()
            )));
        }
        if pending == 0 {
            return Ok(Vec::new());
        }
        let mut records: Vec<INPUT_RECORD> = Vec::with_capacity(pending as usize);
        records.resize(pending as usize, unsafe { std::mem::zeroed() });
        let mut read: DWORD = 0;
        if unsafe {
            ReadConsoleInputW(
                self.hin,
                records.as_mut_ptr(),
                pending,
                &mut read,
            )
        } == 0
        {
            return Err(PyRuntimeError::new_err(format!(
                "ReadConsoleInputW 失败: {}",
                std::io::Error::last_os_error()
            )));
        }
        records.truncate(read as usize);

        let mut last = self.last_pressed.lock().unwrap().clone();
        let mut last_count = *self.last_click_count.lock().unwrap();
        let mut out: Vec<Py<PyAny>> = Vec::with_capacity(records.len());
        for rec in &records {
            let ev: Py<PyAny> = match rec.EventType {
                KEY_EVENT => {
                    let k = unsafe { rec.Event.KeyEvent() };
                    if let Some((key, mods, down)) = normalize_key(k) {
                        ("key", key, mods, down).into_pyobject(py)?.into_any().unbind()
                    } else {
                        continue;
                    }
                }
                MOUSE_EVENT => {
                    let m = unsafe { rec.Event.MouseEvent() };
                    let ((x, y, kind, button, mods, count), next_lp, next_count) =
                        normalize_mouse(m, &last, last_count);
                    last = next_lp;
                    last_count = next_count;
                    ("mouse", x, y, kind, button, mods, count)
                        .into_pyobject(py)?
                        .into_any()
                        .unbind()
                }
                WINDOW_BUFFER_SIZE_EVENT => ("resize",).into_pyobject(py)?.into_any().unbind(),
                _ => continue, // MENU / FOCUS 不处理
            };
            out.push(ev);
        }
        *self.last_pressed.lock().unwrap() = last;
        *self.last_click_count.lock().unwrap() = last_count;
        Ok(out)
    }

    /// 当前窗口逻辑尺寸 (cols, rows)
    fn size(&self) -> PyResult<(usize, usize)> {
        let mut info: CONSOLE_SCREEN_BUFFER_INFO = unsafe { std::mem::zeroed() };
        if unsafe { winapi::um::wincon::GetConsoleScreenBufferInfo(self.hout, &mut info) } == 0 {
            return Err(PyRuntimeError::new_err(format!(
                "GetConsoleScreenBufferInfo 失败: {}",
                std::io::Error::last_os_error()
            )));
        }
        Ok((
            (info.srWindow.Right - info.srWindow.Left + 1) as usize,
            (info.srWindow.Bottom - info.srWindow.Top + 1) as usize,
        ))
    }

    /// 恢复控制台原始模式与代码页（退出时调用；重复调用幂等）
    fn restore(&self) -> PyResult<()> {
        let mut restored = self.restored.lock().unwrap();
        if *restored {
            return Ok(());
        }
        let r1 = set_mode(self.hout, self.orig_out);
        let r2 = set_mode(self.hin, self.orig_in);
        unsafe { SetConsoleOutputCP(self.orig_cp) };
        *restored = true;
        r1?;
        r2?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use winapi::shared::minwindef::BOOL;
    use winapi::um::wincon::{MOUSE_EVENT_RECORD};

    fn key_rec(down: bool, vk: i32, ch: char, state: DWORD) -> KEY_EVENT_RECORD {
        let mut rec: KEY_EVENT_RECORD = unsafe { std::mem::zeroed() };
        rec.bKeyDown = down as BOOL;
        rec.wVirtualKeyCode = vk as WORD;
        rec.dwControlKeyState = state;
        *unsafe { rec.uChar.UnicodeChar_mut() } = ch as u16;
        rec
    }

    fn mouse_rec(x: i16, y: i16, state: DWORD, flags: DWORD) -> MOUSE_EVENT_RECORD {
        let mut rec: MOUSE_EVENT_RECORD = unsafe { std::mem::zeroed() };
        rec.dwMousePosition.X = x;
        rec.dwMousePosition.Y = y;
        rec.dwButtonState = state;
        rec.dwEventFlags = flags;
        rec
    }

    #[test]
    fn test_mods_combination() {
        assert_eq!(mods_from_control_state(0), 0);
        assert_eq!(mods_from_control_state(SHIFT_PRESSED), MOD_SHIFT);
        assert_eq!(
            mods_from_control_state(LEFT_CTRL_PRESSED | LEFT_ALT_PRESSED),
            MOD_CTRL | MOD_ALT
        );
        assert_eq!(
            mods_from_control_state(RIGHT_CTRL_PRESSED | RIGHT_ALT_PRESSED),
            MOD_CTRL | MOD_ALT
        );
    }

    #[test]
    fn test_key_name_mapping() {
        assert_eq!(key_name_from_vk(VK_UP as WORD).as_deref(), Some("Up"));
        assert_eq!(key_name_from_vk(VK_PRIOR as WORD).as_deref(), Some("PageUp"));
        assert_eq!(key_name_from_vk(VK_F1 as WORD).as_deref(), Some("F1"));
        assert_eq!(key_name_from_vk(VK_F24 as WORD).as_deref(), Some("F24"));
        assert_eq!(key_name_from_vk(0x41 as WORD), None); // 'A' 走字符通道
    }

    #[test]
    fn test_key_char() {
        let rec = key_rec(true, 0x41, 'a', 0);
        assert_eq!(normalize_key(&rec), Some(("a".to_string(), 0, true)));
    }

    #[test]
    fn test_key_ctrl_letter() {
        // Ctrl+A：Windows uChar 为 0x01，应归一成 "a" + CTRL
        let rec = key_rec(true, 0x41, '\x01', LEFT_CTRL_PRESSED);
        assert_eq!(normalize_key(&rec), Some(("a".to_string(), MOD_CTRL, true)));
    }

    #[test]
    fn test_key_special_with_shift() {
        let rec = key_rec(true, VK_UP, '\0', SHIFT_PRESSED);
        assert_eq!(normalize_key(&rec), Some(("Up".to_string(), MOD_SHIFT, true)));
    }

    #[test]
    fn test_key_up_shape() {
        let rec = key_rec(false, 0x41, 'a', 0);
        assert_eq!(normalize_key(&rec), Some(("a".to_string(), 0, false)));
    }

    #[test]
    fn test_key_modifier_alone_ignored() {
        // Shift/Ctrl 自身的记录（uChar=0）必须忽略，否则 NUL 会编码下发
        let rec = key_rec(true, 0x10, '\0', SHIFT_PRESSED); // VK_SHIFT
        assert_eq!(normalize_key(&rec), None);
        let rec = key_rec(true, 0x11, '\0', LEFT_CTRL_PRESSED); // VK_CONTROL
        assert_eq!(normalize_key(&rec), None);
    }

    #[test]
    fn test_mouse_press_left() {
        let (ev, last, count) =
            normalize_mouse(&mouse_rec(10, 5, FROM_LEFT_1ST_BUTTON_PRESSED, 0), "none", 1);
        assert_eq!(ev, (10, 5, "press".to_string(), "left".to_string(), 0, 1));
        assert_eq!(last, "left"); // 记录最后按下按钮
        assert_eq!(count, 1); // 普通 press 计数重置为 1
    }

    #[test]
    fn test_mouse_release_carries_last_pressed() {
        // Windows 抬键事件按钮状态为 0；必须用 last_pressed 补全按钮号
        let (ev, last, _count) = normalize_mouse(&mouse_rec(10, 5, 0, 0), "left", 1);
        assert_eq!(ev, (10, 5, "release".to_string(), "left".to_string(), 0, 1));
        assert_eq!(last, "none"); // 补全后清空
    }

    #[test]
    fn test_mouse_release_without_pressed_is_none() {
        let (ev, last, _count) = normalize_mouse(&mouse_rec(10, 5, 0, 0), "none", 1);
        assert_eq!(ev, (10, 5, "release".to_string(), "none".to_string(), 0, 1));
        assert_eq!(last, "none");
    }

    #[test]
    fn test_mouse_wheel() {
        let (ev, _, _) = normalize_mouse(&mouse_rec(10, 5, 120 << 16, MOUSE_WHEELED), "none", 1);
        assert_eq!(ev.3, "wheel_up");
        let (ev, _, _) =
            normalize_mouse(&mouse_rec(10, 5, ((-120i64) & 0xFFFFFFFF) as u32, MOUSE_WHEELED), "none", 1);
        assert_eq!(ev.3, "wheel_down");
    }

    #[test]
    fn test_mouse_move() {
        let (ev, last, _count) = normalize_mouse(&mouse_rec(3, 4, 0, MOUSE_MOVED), "left", 1);
        assert_eq!(ev, (3, 4, "move".to_string(), "none".to_string(), 0, 1));
        assert_eq!(last, "left"); // 悬停移动不清按钮记忆
        let (ev, _, _) = normalize_mouse(
            &mouse_rec(3, 4, FROM_LEFT_1ST_BUTTON_PRESSED, MOUSE_MOVED),
            "left",
            1,
        );
        assert_eq!(ev.3, "left"); // 按住移动（拖动）为对应按钮
    }

    #[test]
    fn test_mouse_double_click_count() {
        // 双击序列：第一次普通 press（count=1），第二次带 DOUBLE_CLICK（count=2）
        let (ev, _, count) = normalize_mouse(
            &mouse_rec(10, 5, FROM_LEFT_1ST_BUTTON_PRESSED, 0),
            "none",
            1,
        );
        assert_eq!(ev, (10, 5, "press".to_string(), "left".to_string(), 0, 1));
        let (ev, _, count) = normalize_mouse(
            &mouse_rec(10, 5, FROM_LEFT_1ST_BUTTON_PRESSED, DOUBLE_CLICK),
            "left",
            count,
        );
        assert_eq!(ev.2, "press");
        assert_eq!(ev.5, 2, "双击第二次 press 应计数 2");
        // 三击：第三次仍带 DOUBLE_CLICK → 计数 3
        let (ev, _, _) = normalize_mouse(
            &mouse_rec(10, 5, FROM_LEFT_1ST_BUTTON_PRESSED, DOUBLE_CLICK),
            "left",
            count,
        );
        assert_eq!(ev.5, 3, "三击第三次 press 应计数 3");
    }

    #[test]
    fn test_mouse_click_count_reset_on_new_press() {
        // 双击后新位置单击：普通 press 重置计数为 1
        let (_, _, count) = normalize_mouse(
            &mouse_rec(10, 5, FROM_LEFT_1ST_BUTTON_PRESSED, DOUBLE_CLICK),
            "left",
            2,
        );
        let (ev, _, _) =
            normalize_mouse(&mouse_rec(20, 8, FROM_LEFT_1ST_BUTTON_PRESSED, 0), "none", count);
        assert_eq!(ev.5, 1, "新位置普通 press 应重置计数");
    }

    #[test]
    fn test_mouse_button_right_middle() {
        assert_eq!(mouse_button(RIGHTMOST_BUTTON_PRESSED), "right");
        assert_eq!(mouse_button(FROM_LEFT_2ND_BUTTON_PRESSED), "middle");
        assert_eq!(mouse_button(0), "none");
    }
}
