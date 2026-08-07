package com.camalabs.computeswarm

import android.app.ActivityManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max

class WorkerService : Service() {
    companion object {
        const val ACTION_START = "com.camalabs.computeswarm.START"
        const val ACTION_STOP = "com.camalabs.computeswarm.STOP"
        private const val CHANNEL_ID = "compute-swarm-worker"
        private const val NOTIFICATION_ID = 42
        private const val AGENT_VERSION = "0.3.0-android"
    }

    private val running = AtomicBoolean(false)
    private val executor = Executors.newSingleThreadExecutor()
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopWorker()
            else -> startWorker()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopWorker()
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Compute swarm worker", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    private fun notify(text: String) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Compute Swarm Worker")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()
        startForeground(NOTIFICATION_ID, notification)
    }

    private fun startWorker() {
        if (!running.compareAndSet(false, true)) return
        notify("Joining swarm…")
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ComputeSwarm:Worker").apply { acquire() }
        executor.submit { workerLoop() }
    }

    private fun stopWorker() {
        running.set(false)
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private data class Identity(val workerId: String, val token: String)
    private data class Telemetry(val temperatureC: Double?, val batteryPct: Double?, val charging: Boolean?)

    private fun workerLoop() {
        try {
            val prefs = getSharedPreferences("swarm", MODE_PRIVATE)
            val controller = prefs.getString("controller_url", null)?.trimEnd('/') ?: error("controller URL missing")
            val enrollmentToken = prefs.getString("enrollment_token", null) ?: error("enrollment token missing")
            validateController(controller)
            val identity = identity(controller, enrollmentToken)
            val benchmark = benchmark()
            val leaseSeconds = register(controller, identity, benchmark)
            notify("Joined as ${identity.workerId.take(8)}")
            var lastHeartbeat = 0L
            var paused = false

            while (running.get()) {
                try {
                    val telemetry = telemetry()
                    paused = shouldPause(telemetry, paused)
                    val now = System.currentTimeMillis()
                    if (now - lastHeartbeat > 10_000) {
                        heartbeat(controller, identity, telemetry)
                        lastHeartbeat = now
                    }
                    if (paused) {
                        notify("Paused: battery/temperature limit")
                        Thread.sleep(5_000)
                        continue
                    }

                    val lease = requestJson(
                        controller,
                        "/workers/${identity.workerId}/lease?wait_seconds=15",
                        identity.token,
                        JSONObject(),
                        25_000
                    )
                    if (lease.isNull("work")) continue
                    val work = lease.getJSONObject("work")
                    try {
                        val started = System.nanoTime()
                        val result = executeWork(controller, identity, work, leaseSeconds)
                        val elapsedMs = (System.nanoTime() - started) / 1_000_000.0
                        requestJson(
                            controller,
                            "/workers/${identity.workerId}/units/${work.getString("unit_id")}/result",
                            identity.token,
                            JSONObject()
                                .put("lease_id", work.getString("lease_id"))
                                .put("result", result)
                                .put("elapsed_ms", elapsedMs)
                        )
                        notify("Completed ${work.getString("kind")}")
                    } catch (e: Exception) {
                        requestJson(
                            controller,
                            "/workers/${identity.workerId}/units/${work.getString("unit_id")}/failure",
                            identity.token,
                            JSONObject()
                                .put("lease_id", work.getString("lease_id"))
                                .put("error", e.message ?: e.javaClass.simpleName)
                                .put("retry", false)
                        )
                    }
                } catch (e: Exception) {
                    notify("Controller error; retrying")
                    Thread.sleep(3_000)
                }
            }
        } catch (e: Exception) {
            notify("Worker stopped: ${e.message}")
            running.set(false)
        }
    }

    private fun validateController(controller: String) {
        val uri = URI(controller)
        if (uri.scheme.equals("https", true)) return
        val host = uri.host ?: error("controller host missing")
        if (uri.scheme.equals("http", true) && isPrivateOrLocal(host)) return
        error("Remote controller must use HTTPS. HTTP is only allowed on local/private LAN addresses.")
    }

    private fun isPrivateOrLocal(host: String): Boolean {
        if (host == "localhost" || host == "127.0.0.1" || host == "::1") return true
        if (host.startsWith("10.") || host.startsWith("192.168.")) return true
        if (host.startsWith("172.")) {
            val second = host.split('.').getOrNull(1)?.toIntOrNull() ?: return false
            if (second in 16..31) return true
        }
        return false
    }

    private fun identity(controller: String, enrollmentToken: String): Identity {
        val prefs = getSharedPreferences("swarm", MODE_PRIVATE)
        val wid = prefs.getString("worker_id", null)
        val token = prefs.getString("device_token", null)
        if (!wid.isNullOrBlank() && !token.isNullOrBlank()) return Identity(wid, token)
        val enrolled = requestJson(
            controller,
            "/workers/enroll",
            enrollmentToken,
            JSONObject().put("name", Build.MODEL)
        )
        val identity = Identity(enrolled.getString("worker_id"), enrolled.getString("device_token"))
        prefs.edit().putString("worker_id", identity.workerId).putString("device_token", identity.token).apply()
        return identity
    }

    private fun memoryMb(): Long {
        val info = ActivityManager.MemoryInfo()
        getSystemService(ActivityManager::class.java).getMemoryInfo(info)
        return info.totalMem / (1024 * 1024)
    }

    private fun capabilities(): JSONArray {
        val values = mutableListOf("cpu", "kotlin", "os:android", "arch:${Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown"}")
        values += TaskRegistry.taskNames.map { "task:$it" }
        return JSONArray(values.sorted())
    }

    private fun register(controller: String, identity: Identity, score: Double): Int {
        val t = telemetry()
        val body = JSONObject()
            .put("name", Build.MODEL)
            .put("os_name", "Android")
            .put("platform", "Android ${Build.VERSION.RELEASE} / SDK ${Build.VERSION.SDK_INT}")
            .put("arch", Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown")
            .put("cores", Runtime.getRuntime().availableProcessors())
            .put("memory_mb", memoryMb())
            .put("benchmark", score)
            .put("capabilities", capabilities())
            .put("labels", JSONObject().put("device_model", Build.MODEL))
            .put("agent_version", AGENT_VERSION)
        putTelemetry(body, t)
        val response = requestJson(controller, "/workers/${identity.workerId}/register", identity.token, body)
        return response.optInt("lease_seconds", 120)
    }

    private fun heartbeat(controller: String, identity: Identity, t: Telemetry) {
        val body = JSONObject().put("capabilities", capabilities())
        putTelemetry(body, t)
        requestJson(controller, "/workers/${identity.workerId}/heartbeat", identity.token, body)
    }

    private fun putTelemetry(json: JSONObject, t: Telemetry) {
        if (t.temperatureC != null) json.put("temperature_c", t.temperatureC)
        if (t.batteryPct != null) json.put("battery_pct", t.batteryPct)
        if (t.charging != null) json.put("charging", t.charging)
    }

    private fun telemetry(): Telemetry {
        val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return Telemetry(null, null, null)
        val level = battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = battery.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        val pct = if (level >= 0 && scale > 0) level * 100.0 / scale else null
        val temperature = battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)
        val tempC = if (temperature != Int.MIN_VALUE) temperature / 10.0 else null
        val status = battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        return Telemetry(tempC, pct, charging)
    }

    private fun shouldPause(t: Telemetry, wasPaused: Boolean): Boolean {
        if (t.batteryPct != null && t.batteryPct < 20.0 && t.charging != true) return true
        val temp = t.temperatureC ?: return false
        return if (wasPaused) temp > 42.0 else temp >= 46.0
    }

    private fun benchmark(): Double {
        val digest = MessageDigest.getInstance("SHA-256")
        var data = "universal-compute-swarm".toByteArray()
        val n = 50_000
        val start = System.nanoTime()
        repeat(n) { data = digest.digest(data) }
        val seconds = max((System.nanoTime() - start) / 1_000_000_000.0, 1e-9)
        return n / seconds
    }

    private fun executeWork(controller: String, identity: Identity, work: JSONObject, leaseSeconds: Int): JSONObject {
        val root = File(cacheDir, "swarm/${work.getString("job_id")}/${work.getString("unit_id")}")
        root.deleteRecursively()
        root.mkdirs()
        val renewer: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor()
        renewer.scheduleAtFixedRate({
            try {
                requestJson(
                    controller,
                    "/workers/${identity.workerId}/leases/${work.getString("lease_id")}/renew",
                    identity.token,
                    JSONObject(),
                    15_000
                )
            } catch (_: Exception) {
            }
        }, max(5, leaseSeconds / 3).toLong(), max(5, leaseSeconds / 3).toLong(), TimeUnit.SECONDS)

        try {
            val payload = JSONObject(work.getJSONObject("payload").toString())
            val artifactPaths = downloadArtifacts(controller, identity, payload, root)
            val pathJson = JSONObject()
            artifactPaths.forEach { (key, value) -> pathJson.put(key, value.absolutePath) }
            payload.put("_work_dir", root.absolutePath).put("_artifact_paths", pathJson)
            val context = TaskRegistry.Context(root, artifactPaths)
            val result = TaskRegistry.execute(work.getString("kind"), payload, context)
            return normalizeOutputs(controller, identity, root, result)
        } finally {
            renewer.shutdownNow()
            root.deleteRecursively()
        }
    }

    private fun downloadArtifacts(controller: String, identity: Identity, payload: JSONObject, root: File): Map<String, File> {
        val inputs = payload.optJSONArray("artifact_inputs") ?: return emptyMap()
        val paths = mutableMapOf<String, File>()
        for (i in 0 until inputs.length()) {
            val item = inputs.getJSONObject(i)
            val id = item.getString("artifact_id")
            val alias = item.optString("alias", "artifact_$i")
            val name = File(item.optString("name", id)).name.ifBlank { "artifact_$i" }
            val destination = File(root, name)
            val conn = open(controller, "/artifacts/$id", "GET", identity.token)
            conn.setRequestProperty("X-Worker-ID", identity.workerId)
            val code = conn.responseCode
            if (code !in 200..299) throw IllegalStateException("artifact download failed: HTTP $code")
            val expected = conn.getHeaderField("X-Artifact-Sha256")
            val digest = MessageDigest.getInstance("SHA-256")
            BufferedInputStream(conn.inputStream).use { input ->
                BufferedOutputStream(destination.outputStream()).use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read <= 0) break
                        digest.update(buffer, 0, read)
                        output.write(buffer, 0, read)
                    }
                }
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            if (!expected.isNullOrBlank() && !actual.equals(expected, true)) {
                destination.delete()
                error("artifact checksum mismatch")
            }
            paths[alias] = destination
            conn.disconnect()
        }
        return paths
    }

    private fun normalizeOutputs(controller: String, identity: Identity, root: File, result: JSONObject): JSONObject {
        if (!result.has("_artifact_outputs")) return result
        val outputs = result.remove("_artifact_outputs") as JSONArray
        val rootPath = root.canonicalFile
        val uploaded = JSONArray()
        for (i in 0 until outputs.length()) {
            val item = outputs.getJSONObject(i)
            val file = File(root, item.getString("path")).canonicalFile
            if (!file.path.startsWith(rootPath.path + File.separator)) error("artifact output escaped sandbox")
            val name = File(item.optString("name", file.name)).name
            val contentType = item.optString("content_type", "application/octet-stream")
            uploaded.put(uploadArtifact(controller, identity, file, name, contentType))
        }
        result.put("artifacts", uploaded)
        return result
    }

    private fun uploadArtifact(controller: String, identity: Identity, file: File, name: String, contentType: String): JSONObject {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        val sha = digest.digest().joinToString("") { "%02x".format(it) }
        val encodedName = URLEncoder.encode(name, Charsets.UTF_8.name())
        val conn = open(controller, "/workers/${identity.workerId}/artifacts?name=$encodedName", "POST", identity.token)
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", contentType)
        conn.setRequestProperty("X-Artifact-Sha256", sha)
        conn.setFixedLengthStreamingMode(file.length())
        file.inputStream().use { input -> conn.outputStream.use { output -> input.copyTo(output, 1024 * 1024) } }
        val response = readJsonResponse(conn)
        conn.disconnect()
        return response
    }

    private fun requestJson(
        controller: String,
        path: String,
        token: String,
        body: JSONObject,
        timeoutMs: Int = 30_000
    ): JSONObject {
        val conn = open(controller, path, "POST", token, timeoutMs)
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        val bytes = body.toString().toByteArray(Charsets.UTF_8)
        conn.setFixedLengthStreamingMode(bytes.size)
        conn.outputStream.use { it.write(bytes) }
        val result = readJsonResponse(conn)
        conn.disconnect()
        return result
    }

    private fun open(controller: String, path: String, method: String, token: String, timeoutMs: Int = 30_000): HttpURLConnection {
        return (URI(controller + path).toURL().openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 20_000
            readTimeout = timeoutMs
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("User-Agent", "ComputeSwarmAndroid/$AGENT_VERSION")
        }
    }

    private fun readJsonResponse(conn: HttpURLConnection): JSONObject {
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
        if (code !in 200..299) throw IllegalStateException("HTTP $code: $text")
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }
}
