package com.camalabs.computeswarm

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import kotlin.math.sqrt

object TaskRegistry {
    data class Context(val workDir: File, val artifactPaths: Map<String, File>)

    val taskNames = setOf("prime_count", "monte_carlo_pi", "sha256_artifact", "text_artifact")

    fun execute(kind: String, payload: JSONObject, context: Context): JSONObject = when (kind) {
        "prime_count" -> primeCount(payload)
        "monte_carlo_pi" -> monteCarlo(payload)
        "sha256_artifact" -> sha256Artifact(payload, context)
        "text_artifact" -> textArtifact(payload, context)
        else -> throw IllegalArgumentException("unsupported task kind: $kind")
    }

    private fun primeCount(payload: JSONObject): JSONObject {
        val start = payload.getLong("start")
        val end = payload.getLong("end")
        var count = 0L
        for (n in maxOf(2L, start) until end) if (isPrime(n)) count++
        return JSONObject().put("count", count)
    }

    private fun isPrime(n: Long): Boolean {
        if (n < 2) return false
        if (n % 2L == 0L) return n == 2L
        var d = 3L
        while (d <= sqrt(n.toDouble()).toLong()) {
            if (n % d == 0L) return false
            d += 2
        }
        return true
    }

    private fun mix(input: ULong): ULong {
        var x = input
        x = x xor (x shr 12)
        x = x xor (x shl 25)
        x = x xor (x shr 27)
        return x * 0x2545F4914F6CDD1DuL
    }

    private fun monteCarlo(payload: JSONObject): JSONObject {
        val start = payload.getLong("start").toULong()
        val end = payload.getLong("end").toULong()
        var inside = 0L
        var i = start
        val max = ULong.MAX_VALUE.toDouble()
        while (i < end) {
            val a = mix(i + 0x9E3779B97F4A7C15uL)
            val b = mix(a xor 0xD1B54A32D192ED03uL)
            val x = a.toDouble() / max
            val y = b.toDouble() / max
            if (x * x + y * y <= 1.0) inside++
            i++
        }
        return JSONObject().put("inside", inside).put("samples", (end - start).toLong())
    }

    private fun sha256Artifact(payload: JSONObject, context: Context): JSONObject {
        val alias = payload.optString("alias", "input")
        val file = context.artifactPaths[alias] ?: throw IllegalArgumentException("artifact alias not found: $alias")
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return JSONObject().put("sha256", digest.digest().joinToString("") { "%02x".format(it) }).put("size_bytes", file.length())
    }

    private fun textArtifact(payload: JSONObject, context: Context): JSONObject {
        val name = File(payload.optString("name", "output.txt")).name.ifBlank { "output.txt" }
        val out = File(context.workDir, name)
        out.writeText(payload.optString("text", ""))
        val outputs = JSONArray().put(
            JSONObject().put("path", name).put("name", name).put("content_type", "text/plain; charset=utf-8")
        )
        return JSONObject().put("bytes", out.length()).put("_artifact_outputs", outputs)
    }
}
