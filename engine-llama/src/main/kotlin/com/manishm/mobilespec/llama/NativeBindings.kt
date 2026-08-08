package com.manishm.mobilespec.llama

import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.ModelConfig

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

    override fun capabilities(): EngineCapabilities {
        if (!available) return unavailableCapabilities(unavailableReason)
        return nativeCall {
            val detail = nativeCapabilities()
            EngineCapabilities(
                nativeAvailable = true,
                backends = buildSet {
                    add(Backend.CPU)
                    if ("vulkan" in detail.lowercase()) add(Backend.VULKAN)
                },
                supportsSpeculativeDecoding =
                    "speculative" in detail.lowercase() || "mtp" in detail.lowercase(),
                supportsCancellation = true,
                timingSource = "llama.cpp native monotonic clock",
                supportsPhaseAwareThreadPolicy = "phase-aware threads" in detail.lowercase(),
                detail = detail,
            )
        }
    }

    override fun createSession(config: ModelConfig): Long = nativeCall {
        nativeCreateSession(
            modelPath = config.path,
            contextSize = config.contextSize,
            baselineDecodeThreads = config.phasePolicy?.baselineDecodeThreads ?: config.threads,
            baselinePrefillThreads = config.phasePolicy?.baselinePrefillThreads ?: config.threads,
            optimizedDecodeThreads = config.phasePolicy?.decodeThreads ?: 0,
            optimizedPrefillThreads = config.phasePolicy?.prefillThreads ?: 0,
            backend = config.backend.ordinal,
            useMmap = config.useMmap,
        )
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
