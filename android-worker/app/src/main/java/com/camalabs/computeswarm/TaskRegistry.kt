package com.camalabs.computeswarm

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import kotlin.math.sqrt

object TaskRegistry {
    data class ContextData(
        val appContext: Context,
        val workDir: File,
        val artifactPaths: Map<String, File>
    )

    private val vulkanAvailable: Boolean by lazy { VulkanBackend.isAvailable() }

    val taskNames: Set<String>
        get() = buildSet {
            addAll(setOf("prime_count", "monte_carlo_pi", "sha256_artifact", "text_artifact", "litert_infer"))
            if (vulkanAvailable) add("vulkan_vector_add")
        }

    val extraCapabilities: Set<String>
        get() = buildSet {
            add("litert")
            add("tflite")
            if (vulkanAvailable) add("vulkan")
        }

    fun execute(kind: String, payload: JSONObject, context: ContextData): JSONObject = when (kind) {
        "prime_count" -> primeCount(payload)
        "monte_carlo_pi" -> monteCarlo(payload)
        "sha256_artifact" -> sha256Artifact(payload, context)
        "text_artifact" -> textArtifact(payload, context)
        "litert_infer" -> liteRtInfer(payload, context)
        "vulkan_vector_add" -> vulkanVectorAdd(payload, context)
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

    private fun sha256Artifact(payload: JSONObject, context: ContextData): JSONObject {
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
        return JSONObject()
            .put("sha256", digest.digest().joinToString("") { "%02x".format(it) })
            .put("size_bytes", file.length())
    }

    private fun textArtifact(payload: JSONObject, context: ContextData): JSONObject {
        val name = File(payload.optString("name", "output.txt")).name.ifBlank { "output.txt" }
        val out = File(context.workDir, name)
        out.writeText(payload.optString("text", ""))
        val outputs = JSONArray().put(
            JSONObject().put("path", name).put("name", name).put("content_type", "text/plain; charset=utf-8")
        )
        return JSONObject().put("bytes", out.length()).put("_artifact_outputs", outputs)
    }

    private fun liteRtInfer(payload: JSONObject, context: ContextData): JSONObject {
        val modelAlias = payload.optString("model_alias", "model")
        val model = context.artifactPaths[modelAlias]
            ?: throw IllegalArgumentException("LiteRT model artifact alias not found: $modelAlias")
        val threads = payload.optInt("threads", Runtime.getRuntime().availableProcessors().coerceAtMost(4)).coerceAtLeast(1)
        val options = Interpreter.Options().setNumThreads(threads)

        Interpreter(model, options).use { interpreter ->
            payload.optJSONArray("shape")?.let { jsonShape ->
                val shape = IntArray(jsonShape.length()) { jsonShape.getInt(it) }
                interpreter.resizeInput(0, shape)
                interpreter.allocateTensors()
            }

            val inputTensor = interpreter.getInputTensor(0)
            val outputTensor = interpreter.getOutputTensor(0)
            require(inputTensor.dataType() == DataType.FLOAT32) { "litert_infer currently supports FLOAT32 input models" }
            require(outputTensor.dataType() == DataType.FLOAT32) { "litert_infer currently supports FLOAT32 output models" }

            val inputElements = inputTensor.shape().fold(1L) { acc, value -> acc * value }.toInt()
            val values = payload.getJSONArray("values")
            require(values.length() == inputElements) {
                "model expects $inputElements FLOAT32 input values, received ${values.length()}"
            }

            val input = ByteBuffer.allocateDirect(inputElements * 4).order(ByteOrder.nativeOrder())
            for (i in 0 until values.length()) input.putFloat(values.getDouble(i).toFloat())
            input.rewind()

            val outputElements = outputTensor.shape().fold(1L) { acc, value -> acc * value }.toInt()
            val output = ByteBuffer.allocateDirect(outputElements * 4).order(ByteOrder.nativeOrder())
            interpreter.run(input, output)
            output.rewind()

            val maxInline = payload.optInt("max_inline_elements", 100_000)
            require(outputElements <= maxInline) {
                "LiteRT output has $outputElements elements; raise max_inline_elements or use a smaller output model"
            }
            val resultValues = JSONArray()
            repeat(outputElements) { resultValues.put(output.float.toDouble()) }
            return JSONObject()
                .put("backend", "litert")
                .put("input_shape", JSONArray(inputTensor.shape().toList()))
                .put("output_shape", JSONArray(outputTensor.shape().toList()))
                .put("values", resultValues)
        }
    }

    private fun vulkanVectorAdd(payload: JSONObject, context: ContextData): JSONObject {
        check(vulkanAvailable) { "Vulkan compute is not available on this device" }
        val aJson = payload.getJSONArray("a")
        val bJson = payload.getJSONArray("b")
        require(aJson.length() == bJson.length() && aJson.length() > 0) {
            "a and b must be non-empty vectors of equal length"
        }
        val a = FloatArray(aJson.length()) { aJson.getDouble(it).toFloat() }
        val b = FloatArray(bJson.length()) { bJson.getDouble(it).toFloat() }
        val output = VulkanBackend.vectorAdd(context.appContext.assets, a, b)
        return JSONObject()
            .put("backend", "vulkan")
            .put("count", output.size)
            .put("values", JSONArray(output.map { it.toDouble() }))
    }
}
