package com.camalabs.computeswarm

import android.Manifest
import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
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

class MainActivity : AppCompatActivity() {
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
        val status = findViewById<TextView>(R.id.statusText)
        val statusDetail = findViewById<TextView>(R.id.statusDetailText)
        val statusDot = findViewById<TextView>(R.id.statusDot)

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

        setStatus(status, statusDetail, statusDot, "Idle", "Configure a controller below, then join the swarm.", R.color.swarm_text_secondary)

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
                .apply()

            hideKeyboard(controller)
            val intent = Intent(this, WorkerService::class.java).apply {
                action = WorkerService.ACTION_START
            }
            ContextCompat.startForegroundService(this, intent)
            setStatus(status, statusDetail, statusDot, "Connecting", "Starting worker and contacting $url", R.color.swarm_warning)
        }

        findViewById<MaterialButton>(R.id.stopButton).setOnClickListener {
            startService(Intent(this, WorkerService::class.java).apply { action = WorkerService.ACTION_STOP })
            setStatus(status, statusDetail, statusDot, "Idle", "Worker stopped. Your controller settings are saved.", R.color.swarm_text_secondary)
        }
    }

    private fun setStatus(
        title: TextView,
        detail: TextView,
        dot: TextView,
        titleText: String,
        detailText: String,
        dotColor: Int
    ) {
        title.text = titleText
        detail.text = detailText
        dot.setTextColor(ContextCompat.getColor(this, dotColor))
    }

    private fun hideKeyboard(view: View) {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(view.windowToken, 0)
        view.clearFocus()
    }
}
