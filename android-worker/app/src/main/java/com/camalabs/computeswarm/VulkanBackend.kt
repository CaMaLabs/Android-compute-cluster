package com.camalabs.computeswarm

import android.content.res.AssetManager

object VulkanBackend {
    private val loaded = runCatching {
        System.loadLibrary("swarm_vulkan")
        true
    }.getOrDefault(false)

    private external fun nativeIsAvailable(): Boolean
    private external fun nativeVectorAdd(
        assetManager: AssetManager,
        a: FloatArray,
        b: FloatArray
    ): FloatArray

    fun isAvailable(): Boolean = loaded && runCatching { nativeIsAvailable() }.getOrDefault(false)

    fun vectorAdd(assetManager: AssetManager, a: FloatArray, b: FloatArray): FloatArray {
        require(a.size == b.size) { "a and b must have identical lengths" }
        require(a.isNotEmpty()) { "vectors must not be empty" }
        check(loaded) { "Vulkan native backend failed to load" }
        return nativeVectorAdd(assetManager, a, b)
    }
}
