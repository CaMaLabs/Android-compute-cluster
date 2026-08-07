package com.camalabs.computeswarm

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val prefs = getSharedPreferences("swarm", MODE_PRIVATE)
        val controller = findViewById<EditText>(R.id.controllerUrl)
        val token = findViewById<EditText>(R.id.enrollmentToken)
        val status = findViewById<TextView>(R.id.statusText)
        controller.setText(prefs.getString("controller_url", "http://127.0.0.1:8765"))
        token.setText(prefs.getString("enrollment_token", ""))

        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }

        findViewById<Button>(R.id.startButton).setOnClickListener {
            val url = controller.text.toString().trim().trimEnd('/')
            val enrollment = token.text.toString().trim()
            prefs.edit().putString("controller_url", url).putString("enrollment_token", enrollment).apply()
            val intent = Intent(this, WorkerService::class.java).apply {
                action = WorkerService.ACTION_START
            }
            ContextCompat.startForegroundService(this, intent)
            status.text = "Worker started. See notification/logcat for status."
        }

        findViewById<Button>(R.id.stopButton).setOnClickListener {
            startService(Intent(this, WorkerService::class.java).apply { action = WorkerService.ACTION_STOP })
            status.text = "Worker stopped"
        }
    }
}
