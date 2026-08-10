package com.manishm.mobilespec.llama

import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.BackendDeviceInfo
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.ModelConfig
import com.manishm.mobilespec.engine.VulkanDeviceInfo

fun interface NativeTokenCallback {
    fun onToken(text: String, tokenIndex: Int, elapsedMicros: Long)
}

/**
 * Narrow boundary implemented by the llama.cpp JNI library.
 *
 * Generation returns [prompt tokens, generated tokens, TTFT microseconds, total microseconds,
 * decode microseconds].
 * All timing values originate inside native code, avoiding Kotlin scheduling skew.
 */
interface NativeBindings {
    val available: Boolean
    val unavailableReason: String?

    fun capabilities(): EngineCapabilities
    fun createSession(config: ModelConfig): Long
    fun modelLayerCount(handle: Long): Int
    fun generate(
        handle: Long,
        prompt: String,
        config: GenerationConfig,
        callback: NativeTokenCallback,
    ): LongArray
    fun cancel(handle: Long)
    fun destroySession(handle: Long)
}

class NativeUnavailableException(message: String) : IllegalStateException(message)

class JniNativeBindings(
    private val runtime: NativeRuntime = NativeRuntime.instance,
) : NativeBindings {
    override val available: Boolean get() = runtime.available
    override val unavailableReason: String? get() = runtime.error
    private val cachedCapabilities: EngineCapabilities by lazy {
        if (!available) {
            unavailableCapabilities(unavailableReason)
        } else {
            nativeCall { parseNativeCapabilities(nativeCapabilities()) }
        }
    }

    override fun capabilities(): EngineCapabilities = cachedCapabilities

    override fun createSession(config: ModelConfig): Long = nativeCall {
        nativeCreateSession(
            modelPath = config.path,
            contextSize = config.contextSize,
            baselineDecodeThreads = config.phasePolicy?.baselineDecodeThreads ?: config.threads,
            baselinePrefillThreads = config.phasePolicy?.baselinePrefillThreads ?: config.threads,
            optimizedDecodeThreads = config.phasePolicy?.decodeThreads ?: 0,
            optimizedPrefillThreads = config.phasePolicy?.prefillThreads ?: 0,
            backend = config.backend.ordinal,
            gpuLayers = config.gpuLayers,
            useMmap = config.useMmap,
        )
    }

    override fun modelLayerCount(handle: Long): Int = nativeCall {
        nativeModelLayerCount(handle)
    }

    override fun generate(
        handle: Long,
        prompt: String,
        config: GenerationConfig,
        callback: NativeTokenCallback,
    ): LongArray = nativeCall {
        nativeGenerate(
            handle = handle,
            prompt = prompt,
            maxTokens = config.maxTokens,
            temperature = config.temperature,
            topP = config.topP,
            seed = config.seed,
            mode = config.mode.ordinal,
            callback = callback,
        )
    }

    override fun cancel(handle: Long) {
        if (available && handle != 0L) nativeCall { nativeCancel(handle) }
    }

    override fun destroySession(handle: Long) {
        if (available && handle != 0L) nativeCall { nativeDestroySession(handle) }
    }

    private inline fun <T> nativeCall(block: () -> T): T {
        if (!available) throw NativeUnavailableException(unavailableReason ?: "Native engine unavailable")
        return try {
            block()
        } catch (error: LinkageError) {
            throw NativeUnavailableException("JNI ABI mismatch: ${error.message}")
        }
    }

    private external fun nativeCapabilities(): String
    private external fun nativeCreateSession(
        modelPath: String,
        contextSize: Int,
        baselineDecodeThreads: Int,
        baselinePrefillThreads: Int,
        optimizedDecodeThreads: Int,
        optimizedPrefillThreads: Int,
        backend: Int,
        gpuLayers: Int,
        useMmap: Boolean,
    ): Long
    private external fun nativeGenerate(
        handle: Long,
        prompt: String,
        maxTokens: Int,
        temperature: Float,
        topP: Float,
        seed: Long,
        mode: Int,
        callback: NativeTokenCallback,
    ): LongArray
    private external fun nativeModelLayerCount(handle: Long): Int
    private external fun nativeCancel(handle: Long)
    private external fun nativeDestroySession(handle: Long)
}

internal fun unavailableCapabilities(reason: String?) = EngineCapabilities(
    nativeAvailable = false,
    backends = emptySet(),
    supportsSpeculativeDecoding = false,
    supportsCancellation = false,
    timingSource = "unavailable",
    detail = reason,
)

internal fun parseNativeCapabilities(raw: String): EngineCapabilities {
    val features = mutableSetOf<String>()
    val backendDevices = mutableListOf<BackendDeviceInfo>()
    val vulkanDevices = mutableListOf<VulkanDeviceInfo>()
    val notes = mutableListOf<String>()

    raw.lineSequence().filter(String::isNotBlank).forEach { line ->
        val fields = line.split('\t')
        when (fields.firstOrNull()) {
            "feature" -> fields.getOrNull(1)?.let(features::add)
            "backend" -> if (fields.size >= 6) {
                backendDevices += BackendDeviceInfo(
                    name = fields[1],
                    description = fields[2],
                    type = fields[3],
                    memoryFreeBytes = fields[4].toLongOrNull() ?: 0,
                    memoryTotalBytes = fields[5].toLongOrNull() ?: 0,
                    asynchronous = fields.getOrNull(6)?.toBooleanStrictOrNull() ?: false,
                    hostBuffer = fields.getOrNull(7)?.toBooleanStrictOrNull() ?: false,
                    bufferFromHostPointer = fields.getOrNull(8)?.toBooleanStrictOrNull() ?: false,
                    events = fields.getOrNull(9)?.toBooleanStrictOrNull() ?: false,
                )
            }
            "vulkan" -> if (fields.size >= 12) {
                vulkanDevices += VulkanDeviceInfo(
                    name = fields[1],
                    vendorId = fields[2].toLongOrNull() ?: 0,
                    deviceId = fields[3].toLongOrNull() ?: 0,
                    apiVersion = fields[4],
                    driverVersionRaw = fields[5].toLongOrNull() ?: 0,
                    driverVersion = fields[6],
                    deviceType = fields[7],
                    fp16 = fields[8].toBooleanStrictOrNull() ?: false,
                    integerDotProduct = fields[9].toBooleanStrictOrNull() ?: false,
                    cooperativeMatrix = fields[10].toBooleanStrictOrNull() ?: false,
                    cooperativeMatrix2 = fields[11].toBooleanStrictOrNull() ?: false,
                )
            }
            "system" -> notes += fields.drop(1).joinToString(" ")
            "vulkan-error" -> notes += "Vulkan probe: ${fields.drop(1).joinToString(" ")}"
            else -> notes += line
        }
    }
    val gpuReady = "gpu-offload" in features && vulkanDevices.isNotEmpty()
    return EngineCapabilities(
        nativeAvailable = true,
        backends = buildSet {
            add(Backend.CPU)
            add(Backend.AUTO)
            if (gpuReady) {
                add(Backend.VULKAN)
                add(Backend.HYBRID)
            }
        },
        supportsSpeculativeDecoding = "speculative" in features || "mtp" in features,
        supportsCancellation = true,
        timingSource = "llama.cpp native monotonic clock",
        supportsPhaseAwareThreadPolicy = "phase-aware" in features,
        supportsGpuOffload = gpuReady,
        supportsHybridOffload = gpuReady,
        supportsKleidiAI = "kleidiai" in features,
        backendDevices = backendDevices,
        vulkanDevices = vulkanDevices,
        detail = notes.joinToString("; ").ifBlank { null },
    )
}

class NativeRuntime private constructor() {
    val available: Boolean
    val error: String?

    init {
        var loaded = false
        var failure: String? = null
        try {
            System.loadLibrary("mobilespec_llama")
            loaded = true
        } catch (error: LinkageError) {
            failure = error.message ?: error.javaClass.simpleName
        } catch (error: SecurityException) {
            failure = error.message ?: error.javaClass.simpleName
        }
        available = loaded
        error = failure
    }

    companion object {
        val instance: NativeRuntime by lazy { NativeRuntime() }
    }
}
