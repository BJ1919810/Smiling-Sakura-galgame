// 笑樱 —— 桌面壳薄入口（2026-09-13 移动端改造后）
//
// 逻辑主体在 lib.rs（Android 打包要求 Rust 侧编译为 cdylib，桌面共享同一份逻辑）。
// 窗口 16:9 锁定的完整方案说明见 lib.rs 顶部注释。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    xiaoying_lib::run()
}
