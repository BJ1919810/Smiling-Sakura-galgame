package com.qingyu.xiaoying

import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import androidx.activity.enableEdgeToEdge

/**
 * ★ 2026-09-19（主人反馈「不能全屏」）—— 进**沉浸式全屏**：隐藏状态栏与导航栏。
 *
 * 现状：模板自带的 enableEdgeToEdge() 只做到「内容铺到系统栏之下」，系统栏本身**仍然可见**
 * ⇒ 玩家看到顶部一条状态栏、底部一条手势条，画面被挤。
 *
 * 本类的做法（不引第三方依赖，按 API 分路）：
 * · API 30+ ：WindowInsetsController.hide(systemBars()) ＋ 侧滑可临时呼出
 * · API 29- ：退到老的 systemUiVisibility 沉浸式粘滞标志
 * · insets 变化后重新隐藏（键盘弹出 / 转屏 / 系统栏偷跑出来时兜住）
 * · onWindowFocusChanged 再兜一次（从后台回来时系统栏常自己冒出来）
 *
 * ⚠️ 只改 gen/android（Tauri 生成的 Android 工程），**不影响桌面端**。
 */
class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    hideSystemBars()
    installKeepHidden()
  }

  /** 隐藏系统栏（沉浸式全屏） */
  private fun hideSystemBars() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
      window.setDecorFitsSystemWindows(false)
      window.insetsController?.let { c ->
        c.hide(WindowInsets.Type.systemBars())
        c.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
      }
    } else {
      @Suppress("DEPRECATION")
      window.decorView.systemUiVisibility =
        (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
          or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
          or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
          or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
          or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
          or View.SYSTEM_UI_FLAG_FULLSCREEN)
    }
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
  }

  /** insets 每次变化后都重新隐藏，防系统栏「偷跑」 */
  private fun installKeepHidden() {
    window.decorView.setOnApplyWindowInsetsListener { v, insets ->
      hideSystemBars()
      v.onApplyWindowInsets(insets)
    }
  }

  override fun onWindowFocusChanged(hasFocus: Boolean) {
    super.onWindowFocusChanged(hasFocus)
    if (hasFocus) hideSystemBars()
  }
}
