package com.dafeiyu.dafeiyu

import android.app.PictureInPictureParams
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Rational
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * 原生能力桥。只做三件 Flutter 侧做不到的事：
 *
 * 1. `filesDir` —— 拿应用私有目录。pubspec 里刻意不引 path_provider，
 *    为这一行功能加一个上游依赖不划算。
 * 2. `exit` —— 真正结束进程并清掉最近任务里的卡片。
 *    `SystemNavigator.pop()` 只是 finish()，从最近任务划掉后进程还活着。
 * 3. `enterPip` —— 画中画。必须原生调，Flutter 没有对应 API。
 */
class MainActivity : FlutterActivity() {

    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val ch = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
        channel = ch
        ch.setMethodCallHandler { call, result ->
            when (call.method) {
                "filesDir" -> result.success(filesDir.absolutePath)
                "pipSupported" -> result.success(pipSupported())
                "enterPip" -> result.success(enterPip())
                "exit" -> {
                    result.success(true)
                    exitApp()
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun pipSupported(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)

    private fun enterPip(): Boolean {
        if (!pipSupported()) return false
        return try {
            // 16:9 是素材画布的比例，小窗里不会出现黑边
            val params = PictureInPictureParams.Builder()
                .setAspectRatio(Rational(16, 9))
                .build()
            enterPictureInPictureMode(params)
        } catch (e: Exception) {
            false
        }
    }

    /** 进出小窗时通知 Dart 侧：小窗里要把底栏和气泡藏掉。 */
    override fun onPictureInPictureModeChanged(
        isInPictureInPictureMode: Boolean,
        newConfig: Configuration
    ) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        channel?.invokeMethod("pipChanged", isInPictureInPictureMode)
    }

    /**
     * 真退出：先让系统把任务从最近列表摘掉，再杀进程。
     *
     * **两步必须分开**。`finishAndRemoveTask()` 只是把请求丢给
     * ActivityTaskManager，是异步的；紧接着 `killProcess` 会在系统
     * 处理完之前就把进程干掉，结果就是「App 没了，但最近任务里
     * 还留着一张黑卡片，点进去又是冷启动」。
     *
     * 所以这里不自己杀进程，交给 `isFinishing` 之后的生命周期：
     * 任务移除完系统会自行回收。超时兜底那次 kill 才真的动手。
     */
    private fun exitApp() {
        try {
            finishAndRemoveTask()
        } catch (e: Exception) {
            finish()
        }
        // 兜底：个别 ROM 在任务被其它 App 卡片挤占时不会立刻回收，
        // 拖 800ms 再补一刀。此时 finishAndRemoveTask 早已生效。
        Handler(Looper.getMainLooper()).postDelayed({
            android.os.Process.killProcess(android.os.Process.myPid())
        }, 800L)
    }

    companion object {
        private const val CHANNEL = "dafeiyu/native"
    }
}
