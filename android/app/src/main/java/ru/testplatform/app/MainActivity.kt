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
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var pendingReminder: ReminderSettings? = null
    private val webStoragePreferences by lazy {
        getSharedPreferences(WEB_STORAGE_PREFERENCES, MODE_PRIVATE)
    }
    private val assetLoader by lazy {
        WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)
        configureWebView()
        val migrationComplete = webStoragePreferences.getBoolean(KEY_MIGRATION_COMPLETE, false)
        if (savedInstanceState != null && migrationComplete) {
            webView.restoreState(savedInstanceState)
        } else {
            webView.loadUrl(if (migrationComplete) APP_ASSET_URL else LEGACY_ASSET_URL)
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
        webView.addJavascriptInterface(LegacyStorageBridge(), "AndroidStorageBridge")
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false)
        webView.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(
                view: WebView?,
                request: WebResourceRequest,
            ): WebResourceResponse? {
                return assetLoader.shouldInterceptRequest(request.url)
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (uri.scheme != REMINDER_SCHEME) return false
                if (!request.isForMainFrame || uri.host != REMINDER_HOST) return true
                handleReminderRequest(uri)
                return true
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (url == LEGACY_ASSET_URL
                    && !webStoragePreferences.getBoolean(KEY_MIGRATION_COMPLETE, false)
                ) {
                    migrateLegacyWebStorage()
                }
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
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

    private fun migrateLegacyWebStorage() {
        webView.evaluateJavascript(LEGACY_STORAGE_EXPORT_SCRIPT) { encodedStorage ->
            val storage = try {
                JSONTokener(encodedStorage).nextValue() as? String
            } catch (error: JSONException) {
                Log.e(TAG, "Could not read the previous local profile.", error)
                null
            }
            if (storage == null) {
                Log.e(TAG, "Could not migrate the previous local profile.")
                webView.evaluateJavascript(
                    "document.querySelector('#app').innerHTML = '<p class=\"error\">Не удалось перенести локальные данные. Закройте приложение и запустите его снова.</p>';",
                    null,
                )
                return@evaluateJavascript
            }
            webStoragePreferences.edit()
                .putString(KEY_LEGACY_STORAGE, storage)
                .putBoolean(KEY_MIGRATION_COMPLETE, true)
                .apply()
            webView.loadUrl(APP_ASSET_URL)
        }
    }

    private inner class LegacyStorageBridge {
        @JavascriptInterface
        fun takeLegacyStorage(): String {
            val storage = webStoragePreferences.getString(KEY_LEGACY_STORAGE, "{}") ?: "{}"
            webStoragePreferences.edit().remove(KEY_LEGACY_STORAGE).apply()
            return storage
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
        const val APP_ASSET_URL = "https://appassets.androidplatform.net/assets/index.html"
        const val LEGACY_ASSET_URL = "file:///android_asset/index.html"
        const val LEGACY_STORAGE_EXPORT_SCRIPT =
            "(function(){return JSON.stringify(Object.fromEntries(Array.from({length:localStorage.length},(_,i)=>localStorage.key(i)).filter(key=>key&&key.startsWith('python-test-lab-')).map(key=>[key,localStorage.getItem(key)])));})()"
        const val WEB_STORAGE_PREFERENCES = "python_test_lab_web_storage"
        const val KEY_LEGACY_STORAGE = "legacy_local_storage"
        const val KEY_MIGRATION_COMPLETE = "legacy_storage_migration_complete"
        const val TAG = "PythonTestLab"
        const val REQUEST_FILE = 1001
        const val REQUEST_NOTIFICATIONS = 1002
        const val REMINDER_SCHEME = "python-test-lab"
        const val REMINDER_HOST = "reminder"
    }

    private data class ReminderSettings(val enabled: Boolean, val hour: Int, val minute: Int)
}
