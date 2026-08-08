package com.camalabs.computeswarm

import android.Manifest
import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : AppCompatActivity() {
    private lateinit var status: TextView
    private lateinit var statusDetail: TextView
    private lateinit var statusDot: TextView
    private val handler = Handler(Looper.getMainLooper())
    private val probeRunning = AtomicBoolean(false)
    private var monitorActive = false

    private val statusPoll = object : Runnable {
        override fun run() {
            if (!monitorActive) return
            refreshConnectionStatus()
            handler.postDelayed(this, 4_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_main)

        val root = findViewById<View>(R.id.rootLayout)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        val prefs = getSharedPreferences("swarm", MODE_PRIVATE)
        val controller = findViewById<TextInputEditText>(R.id.controllerUrl)
        val token = findViewById<TextInputEditText>(R.id.enrollmentToken)
        val controllerLayout = findViewById<TextInputLayout>(R.id.controllerLayout)
        val tokenLayout = findViewById<TextInputLayout>(R.id.tokenLayout)
        status = findViewById(R.id.statusText)
        statusDetail = findViewById(R.id.statusDetailText)
        statusDot = findViewById(R.id.statusDot)

        controller.setText(prefs.getString("controller_url", ""))
        token.setText(prefs.getString("enrollment_token", ""))

        val activityManager = getSystemService(ActivityManager::class.java)
        val memoryInfo = ActivityManager.MemoryInfo().also(activityManager::getMemoryInfo)
        val memoryGb = memoryInfo.totalMem.toDouble() / (1024.0 * 1024.0 * 1024.0)
        val model = listOf(Build.MANUFACTURER, Build.MODEL)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }

        findViewById<TextView>(R.id.deviceSummary).text =
            "$model • Android ${Build.VERSION.RELEASE} • ${Runtime.getRuntime().availableProcessors()} cores • %.1f GB RAM".format(memoryGb)

        val capabilities = mutableListOf("CPU", "LiteRT")
        if (VulkanBackend.isAvailable()) capabilities += "Vulkan Compute"
        findViewById<TextView>(R.id.capabilitySummary).text =
            "Available: ${capabilities.joinToString(" • ")}"
        findViewById<TextView>(R.id.versionText).text =
            "Worker ${BuildConfig.VERSION_NAME} • ${Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown ABI"}"

        if (prefs.getBoolean("worker_enabled", false) || isWorkerServiceRunning()) {
            setStatus("Checking connection", "Verifying the worker and controller…", R.color.swarm_warning)
        } else {
            setStatus("Idle", "Configure a controller below, then join the swarm.", R.color.swarm_text_secondary)
        }

        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }

        findViewById<MaterialButton>(R.id.startButton).setOnClickListener {
            val url = controller.text?.toString()?.trim()?.trimEnd('/').orEmpty()
            val enrollment = token.text?.toString()?.trim().orEmpty()

            controllerLayout.error = null
            tokenLayout.error = null

            var valid = true
            if (url.isBlank()) {
                controllerLayout.error = "Enter the controller URL"
                valid = false
            } else if (!url.startsWith("http://") && !url.startsWith("https://")) {
                controllerLayout.error = "URL must start with http:// or https://"
                valid = false
            }
            if (enrollment.isBlank()) {
                tokenLayout.error = "Enter the enrollment token"
                valid = false
            }
            if (!valid) return@setOnClickListener

            prefs.edit()
                .putString("controller_url", url)
                .putString("enrollment_token", enrollment)
                .putBoolean("worker_enabled", true)
                .apply()

            hideKeyboard(controller)
            val intent = Intent(this, WorkerService::class.java).apply {
                action = WorkerService.ACTION_START
            }
            ContextCompat.startForegroundService(this, intent)
            setStatus("Connecting", "Starting worker and contacting $url", R.color.swarm_warning)
            handler.removeCallbacks(statusPoll)
            handler.postDelayed(statusPoll, 600)
        }

        findViewById<MaterialButton>(R.id.stopButton).setOnClickListener {
            prefs.edit().putBoolean("worker_enabled", false).apply()
            startService(Intent(this, WorkerService::class.java).apply { action = WorkerService.ACTION_STOP })
            setStatus("Idle", "Worker stopped. Your controller settings are saved.", R.color.swarm_text_secondary)
        }
    }

    override fun onResume() {
        super.onResume()
        monitorActive = true
        handler.removeCallbacks(statusPoll)
        handler.post(statusPoll)
    }

    override fun onPause() {
        monitorActive = false
        handler.removeCallbacks(statusPoll)
        super.onPause()
    }

    private fun refreshConnectionStatus() {
        val prefs = getSharedPreferences("swarm", MODE_PRIVATE)
        val controller = prefs.getString("controller_url", "")?.trim()?.trimEnd('/').orEmpty()
        val requested = prefs.getBoolean("worker_enabled", false)
        val serviceRunning = isWorkerServiceRunning()

        if (!requested && !serviceRunning) {
            setStatus("Idle", "Worker stopped. Your controller settings are saved.", R.color.swarm_text_secondary)
            return
        }
        if (controller.isBlank()) {
            setStatus("Needs configuration", "Enter a controller URL and enrollment token.", R.color.swarm_error)
            return
        }
        if (!probeRunning.compareAndSet(false, true)) return

        Thread {
            val reachable = controllerHealth(controller)
            val workerId = prefs.getString("worker_id", null)
            probeRunning.set(false)
            runOnUiThread {
                if (!monitorActive) return@runOnUiThread
                when {
                    reachable && serviceRunning && !workerId.isNullOrBlank() -> {
                        setStatus(
                            "Connected",
                            "Controller reachable • Worker ${workerId.take(8)} • waiting for work",
                            R.color.swarm_success
                        )
                    }
                    reachable && serviceRunning -> {
                        setStatus("Connecting", "Controller reachable • worker is enrolling/registering", R.color.swarm_warning)
                    }
                    reachable && requested -> {
                        setStatus("Starting worker", "Controller reachable • waiting for the worker service", R.color.swarm_warning)
                    }
                    reachable -> {
                        setStatus("Controller reachable", "Worker is not currently running.", R.color.swarm_text_secondary)
                    }
                    else -> {
                        setStatus("Controller unavailable", "Cannot reach $controller/health", R.color.swarm_error)
                    }
                }
            }
        }.start()
    }

    private fun controllerHealth(controller: String): Boolean {
        var conn: HttpURLConnection? = null
        return try {
            conn = URL("$controller/health").openConnection() as HttpURLConnection
            conn.requestMethod = "GET"
            conn.connectTimeout = 2_500
            conn.readTimeout = 2_500
            conn.setRequestProperty("Connection", "close")
            conn.responseCode in 200..299
        } catch (_: Exception) {
            false
        } finally {
            conn?.disconnect()
        }
    }

    @Suppress("DEPRECATION")
    private fun isWorkerServiceRunning(): Boolean {
        val manager = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        return manager.getRunningServices(Int.MAX_VALUE).any {
            it.service.className == WorkerService::class.java.name
        }
    }

    private fun setStatus(titleText: String, detailText: String, dotColor: Int) {
        status.text = titleText
        statusDetail.text = detailText
        statusDot.setTextColor(ContextCompat.getColor(this, dotColor))
    }

    private fun hideKeyboard(view: View) {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(view.windowToken, 0)
        view.clearFocus()
    }
}
