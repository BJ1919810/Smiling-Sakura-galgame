// 笑樱 —— Tauri 壳逻辑主体（lib.rs）
//
// ★ 2026-09-13 移动端改造：Android/iOS 要求 Rust 侧编译为 cdylib 动态库，
//   故逻辑主体从 main.rs 迁入 lib.rs；main.rs 退化为桌面薄入口。
//   桌面专属逻辑（窗口 16:9 锁定等）全部 #[cfg(desktop)] 门控，Android 编译时自动剔除。
//
// 静态资源（dist/）由前端构建产出；画面等比缩放（letterbox）由 Stage.fit() 处理。
//
// ★★ 窗口比例锁定 16:9 —— WM_SIZING subclass 方案（2026-09-13 定稿，桌面专属）
//   全项目素材与设计分辨率都是 16:9（设计 1920×1080），窗口客户区保持 16:9
//   才能让 letterbox 黑边不出现。
//
//   方案演进（两代翻车史，勿回退）：
//   v1 每 Resized 事件就 set_size 弹回 16:9 → 拖高度时刚拖出的高度立刻被弹回，
//      拖动全程打架 = 窗口不停抽搐（主人实测翻车）。
//   v2 静默期吸附（250ms 无新 Resized 才吸附）→ 拖动不抽了，但拖动中窗口可自由
//      变形、松手才回弹 = 「拖沓感」（主人实测翻车，还不如 v1）。
//   v3 现方案：**拦截 WM_SIZING，在拖拽 modal loop 内部实时修正窗口矩形**。
//      这是 Windows 锁定宽高比的行业标准做法（Electron 11+ setAspectRatio 的
//      Windows 官方实现同款，参考 electron/electron PR #18306）：
//      拖拽每一步都被修正成 16:9 —— 全程跟手、无抽搐、无拖沓、无黑边闪现。
//
//   细节：WM_SIZING 给的是**含标题栏边框的窗口矩形**，16:9 按**客户区**算，
//   用 AdjustWindowRectEx 求装饰增量换算。最大化兜底保留在 Resized 事件里
//   （最大化不走 WM_SIZING；显示器工作区非 16:9 时把「最大化」转成
//   「工作区内最大 16:9 窗口」，独占全屏完全交给系统）。

/// 目标画面比例 16:9
#[cfg(desktop)]
const RATIO_W: u32 = 16;
#[cfg(desktop)]
const RATIO_H: u32 = 9;

/// 吸附容差（物理像素）。吸附自身引发的 Resized 靠它自然收敛。
#[cfg(desktop)]
const TOLERANCE: u32 = 2;

/// 重入保护：吸附自身引发的 Resized 事件直接跳过（防回声循环）
#[cfg(desktop)]
static ADJUSTING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// 由宽度推出 16:9 下的高度
#[cfg(desktop)]
fn ratio_height(width: u32) -> u32 {
    let h = (width as f64) * (RATIO_H as f64) / (RATIO_W as f64);
    (h.round() as u32).max(1)
}

/// 由高度推出 16:9 下的宽度
#[cfg(desktop)]
fn ratio_width(height: u32) -> u32 {
    let w = (height as f64) * (RATIO_W as f64) / (RATIO_H as f64);
    (w.round() as u32).max(1)
}

/// Resized 兜底：只处理「最大化但比例不符」这一种情况
/// （普通拖拽由 WM_SIZING subclass 实时修正，不经过这里）。
#[cfg(desktop)]
fn on_resized(window: &tauri::Window, size: tauri::PhysicalSize<u32>) {
    use std::sync::atomic::Ordering;
    if ADJUSTING.swap(true, Ordering::SeqCst) {
        return;
    }
    handle_resized(window, size);
    ADJUSTING.store(false, Ordering::SeqCst);
}

#[cfg(desktop)]
fn handle_resized(window: &tauri::Window, size: tauri::PhysicalSize<u32>) {
    // 全屏交给系统处理：独占全屏时窗口尺寸由显示模式决定，强行改会打架。
    // 若显示器本身不是 16:9，此时的黑边是 letterbox 的正常表现。
    if window.is_fullscreen().unwrap_or(false) {
        return;
    }

    // 比例已正确（或差 1-2px）：无事可做。WM_SIZING 保证拖拽中恒为 16:9，
    // 这里基本只剩最大化/系统操作触发的 Resized 会走到下面。
    let target_h = ratio_height(size.width);
    if size.height.abs_diff(target_h) <= TOLERANCE {
        return;
    }

    // 已最大化但比例不符（显示器工作区非 16:9）：Windows 会忽略非最大化窗口的
    // 尺寸请求，所以先取消最大化，再吸附到「工作区内能放下的最大 16:9」，并居中。
    if window.is_maximized().unwrap_or(false) {
        if let Err(e) = snap_maximized(window) {
            eprintln!("[window] 最大化 16:9 兜底失败: {e}");
        }
    }
}

#[cfg(desktop)]
fn snap_maximized(window: &tauri::Window) -> tauri::Result<()> {
    if let Some(monitor) = window.current_monitor()? {
        let work = monitor.work_area();
        let (mut w, mut h) = (work.size.width, work.size.height);
        let fit_h = ratio_height(w);
        if fit_h <= h {
            h = fit_h;
        } else {
            w = ratio_width(h);
        }
        window.unmaximize()?;
        window.set_size(tauri::PhysicalSize::new(w, h))?;
        window.center()?;
    }
    Ok(())
}

// ---------- Windows 专属：WM_SIZING subclass（拖拽中实时锁定 16:9） ----------

#[cfg(all(desktop, windows))]
mod aspect {
    use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
    use windows_sys::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        AdjustWindowRectEx, GetWindowLongW, GWL_EXSTYLE, GWL_STYLE, WM_SIZING,
    };

    const SUBCLASS_ID: usize = 0x5859_4150; // "XYAP"

    pub const RATIO_W: u32 = 16;
    pub const RATIO_H: u32 = 9;

    /// 窗口创建后调用一次：给主窗口挂 subclass 拦截 WM_SIZING。
    pub unsafe fn install(hwnd: HWND) {
        SetWindowSubclass(hwnd, Some(aspect_proc), SUBCLASS_ID, 0);
    }

    /// WMSZ_* 常量：LEFT=1 RIGHT=2 TOP=3 TOPLEFT=4 TOPRIGHT=5
    ///            BOTTOM=6 BOTTOMLEFT=7 BOTTOMRIGHT=8
    ///
    /// 拖拽 modal loop 内收到 WM_SIZING：把候选窗口矩形实时修正为 16:9（按客户区比例），
    /// 返回 TRUE 表示矩形已改。被拖的边保持跟手，对侧/邻边作为锚点。
    unsafe extern "system" fn aspect_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
        _id: usize,
        _data: usize,
    ) -> LRESULT {
        if msg == WM_SIZING && lparam != 0 {
            let edge = wparam as u32;
            let rect = &mut *(lparam as *mut RECT);

            // WM_SIZING 的矩形含装饰（标题栏+边框）；16:9 约束的是客户区，
            // 用 AdjustWindowRectEx 求装饰增量做换算。
            let style = GetWindowLongW(hwnd, GWL_STYLE) as u32;
            let exstyle = GetWindowLongW(hwnd, GWL_EXSTYLE) as u32;
            let mut d = RECT { left: 0, top: 0, right: 0, bottom: 0 };
            AdjustWindowRectEx(&mut d, style, 0, exstyle);
            let deco_w = (d.right - d.left).max(0) as u32;
            let deco_h = (d.bottom - d.top).max(0) as u32;

            let ww = (rect.right - rect.left).max(0) as u32;
            let wh = (rect.bottom - rect.top).max(0) as u32;
            let cw = ww.saturating_sub(deco_w);
            let ch = wh.saturating_sub(deco_h);

            // 拖哪个轴，哪个轴跟手；另一轴按 16:9 反推（Electron 同款取向）：
            // 拖左右 → 宽跟手、高反推；拖上下/角落 → 高跟手、宽反推。
            // tauri.conf 的 min 1024×576 本身就是 16:9，故反推值不会跌破 min 约束。
            let (new_cw, new_ch) = match edge {
                1 | 2 => (cw, cw * RATIO_H / RATIO_W),
                _ => (ch * RATIO_W / RATIO_H, ch),
            };
            let new_ww = (new_cw + deco_w) as i32;
            let new_wh = (new_ch + deco_h) as i32;

            // 水平方向锚定：拖左 → 宽跟手（不动水平）；拖左角 → 锚 right；其余 → 锚 left
            match edge {
                1 => {}
                4 | 7 => rect.left = rect.right - new_ww,
                _ => rect.right = rect.left + new_ww,
            }
            // 垂直方向锚定：顶部三向锚 bottom（被拖的 top 跟手）；其余锚 top
            match edge {
                3 | 4 | 5 => rect.top = rect.bottom - new_wh,
                _ => rect.bottom = rect.top + new_wh,
            }
            return 1; // TRUE：矩形已修正
        }
        DefSubclassProc(hwnd, msg, wparam, lparam)
    }
}

/// 组装 Builder：桌面挂窗口 16:9 锁定，移动端裸壳（Activity 由系统管理）
#[cfg(desktop)]
fn builder() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
        .setup(|app| {
            // 主窗口就绪后挂 WM_SIZING subclass（拖拽实时锁定 16:9）
            #[cfg(windows)]
            {
                use tauri::Manager;
                if let Some(win) = app.get_webview_window("main") {
                    if let Ok(hwnd) = win.hwnd() {
                        // HWND 跨 crate 类型不同，统一经 isize 转换
                        unsafe { aspect::install(hwnd.0 as isize as _) };
                    }
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Resized(size) = event {
                on_resized(window, *size);
            }
        })
}

#[cfg(mobile)]
fn builder() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
}

/// 唯一入口：桌面 main.rs 与 Android Activity 都从这里启动
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    builder()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
