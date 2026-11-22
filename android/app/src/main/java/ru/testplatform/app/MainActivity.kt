package ru.testplatform.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var pendingReminder: ReminderSettings? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)
        configureWebView()
        if (savedInstanceState == null) {
            webView.loadUrl("file:///android_asset/index.html")
        } else {
            webView.restoreState(savedInstanceState)
        }
    }

    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            allowContentAccess = true
            blockNetworkLoads = true
            cacheMode = WebSettings.LOAD_NO_CACHE
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false)
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                webView.evaluateJavascript(
                    "JSON.stringify({url:location.href,ready:document.readyState,offline:window.OFFLINE_MODE,viewport:[innerWidth,innerHeight],body:[document.body.clientWidth,document.body.clientHeight],app:(()=>{const e=document.querySelector('#app');const r=e.getBoundingClientRect();return {size:[r.width,r.height],text:e.innerText.slice(0,200)}})()})",
                ) { result -> Log.d("PythonTestLabWeb", "Page state: $result") }
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (uri.scheme != REMINDER_SCHEME) return false
                if (!request.isForMainFrame || uri.host != REMINDER_HOST) return true
                handleReminderRequest(uri)
                return true
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(message: android.webkit.ConsoleMessage): Boolean {
                Log.e(
                    "PythonTestLabWeb",
                    "${message.message()} (${message.sourceId()}:${message.lineNumber()})",
                )
                return true
            }

            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams,
            ): Boolean {
                fileChooserCallback?.onReceiveValue(null)
                fileChooserCallback = filePathCallback
                val intent = fileChooserParams.createIntent().apply {
                    action = Intent.ACTION_OPEN_DOCUMENT
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "*/*"
                    putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("text/x-python", "text/plain"))
                }
                startActivityForResult(intent, REQUEST_FILE)
                return true
            }
        }
    }

    private fun handleReminderRequest(uri: Uri) {
        val enabled = when (uri.getQueryParameter("enabled")) {
            "true" -> true
            "false" -> false
            else -> null
        }
        val hour = uri.getQueryParameter("hour")?.toIntOrNull()
        val minute = uri.getQueryParameter("minute")?.toIntOrNull()
        if (enabled == null || hour == null || hour !in 0..23 || minute == null || minute !in 0..59) {
            notifyReminderResult(
                false,
                ReminderSettings(false, 19, 0),
                "Не удалось сохранить время напоминания.",
            )
            return
        }

        val settings = ReminderSettings(enabled, hour, minute)
        if (enabled && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
            && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            pendingReminder = settings
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQUEST_NOTIFICATIONS)
            return
        }
        saveReminder(settings)
        notifyReminderResult(true, settings, if (enabled) "Напоминание включено." else "Напоминания отключены.")
    }

    private fun saveReminder(settings: ReminderSettings) {
        getSharedPreferences(DailyReminderScheduler.PREFERENCES, MODE_PRIVATE).edit()
            .putBoolean(DailyReminderScheduler.KEY_ENABLED, settings.enabled)
            .putInt(DailyReminderScheduler.KEY_HOUR, settings.hour)
            .putInt(DailyReminderScheduler.KEY_MINUTE, settings.minute)
            .apply()
        if (settings.enabled) {
            DailyReminderScheduler.schedule(this, settings.hour, settings.minute)
        } else {
            DailyReminderScheduler.cancel(this)
        }
    }

    private fun notifyReminderResult(
        success: Boolean,
        settings: ReminderSettings,
        message: String,
    ) {
        val detail = JSONObject()
            .put("success", success)
            .put("enabled", settings.enabled)
            .put("hour", settings.hour)
            .put("minute", settings.minute)
            .put("message", message)
            .toString()
        webView.post {
            webView.evaluateJavascript(
                "window.dispatchEvent(new CustomEvent('android-reminder-result',{detail:$detail}));",
                null,
            )
        }
    }

    @Deprecated("Required to handle the Android 13 notification permission result")
    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_NOTIFICATIONS) return
        val settings = pendingReminder ?: return
        pendingReminder = null
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            saveReminder(settings)
            notifyReminderResult(true, settings, "Напоминание включено.")
        } else {
            notifyReminderResult(
                false,
                settings,
                "Разрешите уведомления в настройках Android, чтобы включить напоминания.",
            )
        }
    }

    @Deprecated("Required by the Android WebView file chooser callback")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_FILE) {
            val selectedFiles = if (resultCode == Activity.RESULT_OK) {
                data?.data?.let { arrayOf(it) }
            } else {
                null
            }
            fileChooserCallback?.onReceiveValue(selectedFiles)
            fileChooserCallback = null
        }
    }

    @Deprecated("Use the system back gesture on newer Android versions")
    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        webView.saveState(outState)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
        webView.destroy()
        super.onDestroy()
    }

    private companion object {
        const val REQUEST_FILE = 1001
        const val REQUEST_NOTIFICATIONS = 1002
        const val REMINDER_SCHEME = "python-test-lab"
        const val REMINDER_HOST = "reminder"
    }

    private data class ReminderSettings(val enabled: Boolean, val hour: Int, val minute: Int)
}
