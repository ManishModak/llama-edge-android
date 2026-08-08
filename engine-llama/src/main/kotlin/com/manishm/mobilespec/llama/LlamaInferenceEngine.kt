package com.manishm.mobilespec.llama

import com.manishm.mobilespec.engine.BenchmarkConfig
import com.manishm.mobilespec.engine.BenchmarkEvent
import com.manishm.mobilespec.engine.BenchmarkResult
import com.manishm.mobilespec.engine.BenchmarkRun
import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.EngineState
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.GenerationMetrics
import com.manishm.mobilespec.engine.InferenceEngine
import com.manishm.mobilespec.engine.InferenceMode
import com.manishm.mobilespec.engine.ModelConfig
import com.manishm.mobilespec.engine.RunTelemetry
import com.manishm.mobilespec.engine.TelemetryProvider
import com.manishm.mobilespec.engine.TelemetrySnapshot
import com.manishm.mobilespec.engine.TokenEvent
import com.manishm.mobilespec.engine.Traceability
import com.manishm.mobilespec.engine.vulkanDeviceIdentitySha256
import java.util.UUID
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

class LlamaInferenceEngine(
    private val bindings: NativeBindings = JniNativeBindings(),
    private val telemetryProvider: TelemetryProvider? = null,
    private val appCommit: String = "unknown",
    private val appSourceSha256: String? = null,
    private val llamaCommit: String = "178a6c4",
    private val llamaSourceDiffSha256: String? = null,
    private val nativeLibrarySha256: String? = null,
    private val runtimeDeviceFingerprintSha256: String? = null,
    private val runtimeDeviceModel: String? = null,
    private val runtimeSocModel: String? = null,
) : InferenceEngine {
    private val operationMutex = Mutex()
    private val activeHandle = AtomicLong(0L)
    private val mutableState = MutableStateFlow<EngineState>(EngineState.Unloaded)
    private var loadedModel: ModelConfig? = null
    private var phasePolicyRejection: String? = null
    private var backendPolicyRejection: String? = null
    private var activeVulkanDeviceIdentitySha256: String? = null

    override val state: StateFlow<EngineState> = mutableState.asStateFlow()

    override fun capabilities(): EngineCapabilities {
        val native = bindings.capabilities()
        val policyReady = loadedModel?.phasePolicy != null
        return native.copy(
            supportsPhaseAwareThreadPolicy =
                native.supportsPhaseAwareThreadPolicy && policyReady,
            detail = listOfNotNull(
                native.detail,
                phasePolicyRejection?.let { "phase policy disabled: $it" },
                backendPolicyRejection?.let { "backend policy fallback: $it" },
            ).joinToString("; ").ifBlank { null },
        )
    }

    override suspend fun load(model: ModelConfig) = operationMutex.withLock {
        if (!bindings.available) {
            val message = bindings.unavailableReason ?: "Native llama.cpp library is not packaged"
            mutableState.value = EngineState.Error(message, recoverable = true)
            throw NativeUnavailableException(message)
        }
        mutableState.value = EngineState.Loading(model.displayName)
        try {
            destroyActiveSession()
            val nativeCapabilities = bindings.capabilities()
            val policyError = model.phasePolicy?.compatibilityError(
                model,
                llamaCommit,
                runtimeDeviceModel,
                runtimeSocModel,
            )
                ?: if (
                    model.phasePolicy != null &&
                    !nativeCapabilities.supportsPhaseAwareThreadPolicy
                ) "native build does not support phase-aware threads" else null
            val phaseSafeModel = if (policyError == null) model else model.copy(phasePolicy = null)
            phasePolicyRejection = policyError
            val vulkanIdentity = vulkanDeviceIdentitySha256(nativeCapabilities.vulkanDevices)
            val backendPolicy = model.backendPolicy
            val compatibilityError = backendPolicy?.compatibilityError(
                model = model,
                runtimeLlamaCommit = llamaCommit,
                runtimeNativeLibrarySha256 = nativeLibrarySha256,
                runtimeDeviceFingerprintSha256 = runtimeDeviceFingerprintSha256,
                runtimeDeviceModel = runtimeDeviceModel,
                runtimeSocModel = runtimeSocModel,
                runtimeVulkanDeviceIdentitySha256 = vulkanIdentity,
            )
            val backendPolicyError = compatibilityError ?: if (
                backendPolicy != null && policyError != null
            ) {
                "backend-policy CPU phase policy is invalid: $policyError"
            } else if (
                model.backend != Backend.AUTO &&
                backendPolicy != null &&
                backendPolicy.backend != model.backend
            ) {
                "backend policy does not match the requested backend"
            } else null
            val requestedBackend = when (model.backend) {
                Backend.AUTO -> if (backendPolicyError == null) {
                    backendPolicy?.backend ?: Backend.CPU
                } else {
                    Backend.CPU
                }
                else -> model.backend
            }
            val capabilityError = when (requestedBackend) {
                Backend.CPU -> null
                Backend.VULKAN -> when {
                    !nativeCapabilities.supportsGpuOffload -> "Vulkan offload is unavailable"
                    model.backend == Backend.AUTO && backendPolicy?.gpuLayers != -1 ->
                        "qualified Vulkan policy does not request full offload"
                    else -> null
                }
                Backend.HYBRID -> when {
                    !nativeCapabilities.supportsHybridOffload -> "hybrid offload is unavailable"
                    (backendPolicy?.gpuLayers ?: model.gpuLayers) <= 0 ->
                        "hybrid offload requires a positive layer count"
                    else -> null
                }
                Backend.AUTO -> "Auto backend was not resolved"
            }
            val autoFallback = if (model.backend == Backend.AUTO && backendPolicy == null) {
                "no qualified GPU policy; using CPU"
            } else null
            val fallbackReason = backendPolicyError ?: capabilityError ?: autoFallback
            val selectionError = backendPolicyError ?: capabilityError
            val effectiveBackend = if (selectionError == null) requestedBackend else Backend.CPU
            val effectiveGpuLayers = when (effectiveBackend) {
                Backend.VULKAN -> -1
                Backend.HYBRID -> backendPolicy?.gpuLayers ?: model.gpuLayers
                else -> 0
            }
            var effectiveModel = phaseSafeModel.copy(
                backend = effectiveBackend,
                gpuLayers = effectiveGpuLayers,
                backendPolicy = if (fallbackReason == null) backendPolicy else null,
            )
            backendPolicyRejection = fallbackReason
            activeVulkanDeviceIdentitySha256 = vulkanIdentity
            val handle = withContext(Dispatchers.IO) {
                try {
                    bindings.createSession(effectiveModel)
                } catch (gpuError: Exception) {
                    if (effectiveModel.backend == Backend.CPU) throw gpuError
                    val failedBackend = effectiveModel.backend
                    effectiveModel = effectiveModel.copy(
                        backend = Backend.CPU,
                        gpuLayers = 0,
                        backendPolicy = null,
                    )
                    backendPolicyRejection =
                        "$failedBackend session failed (${gpuError.safeMessage()}); using CPU"
                    bindings.createSession(effectiveModel)
                }
            }
            check(handle != 0L) { "Native engine returned a null session" }
            val modelLayerCount = try {
                withContext(Dispatchers.IO) {
                    bindings.modelLayerCount(handle)
                }
            } catch (error: Exception) {
                withContext(Dispatchers.IO) {
                    bindings.destroySession(handle)
                }
                throw error
            }
            if (modelLayerCount <= 0) {
                withContext(Dispatchers.IO) {
                    bindings.destroySession(handle)
                }
                error("Native engine returned an invalid model layer count")
            }
            effectiveModel = effectiveModel.copy(modelLayerCount = modelLayerCount)
            activeHandle.set(handle)
            loadedModel = effectiveModel
            mutableState.value = EngineState.Ready(effectiveModel)
        } catch (error: Exception) {
            mutableState.value = EngineState.Error(error.safeMessage(), recoverable = true)
            throw error
        }
    }

    override fun generate(prompt: String, config: GenerationConfig): Flow<TokenEvent> = channelFlow {
        operationMutex.withLock {
            val model = loadedModel
            val handle = activeHandle.get()
            if (model == null || handle == 0L) {
                trySend(TokenEvent.Failed("Load a GGUF model before generating", recoverable = true))
                return@withLock
            }
            if (
                config.mode == InferenceMode.OPTIMIZED &&
                !capabilities().supportsPhaseAwareThreadPolicy
            ) {
                trySend(
                    TokenEvent.Failed(
                        "Optimized mode requires a verified, non-stale phase policy",
                        recoverable = true,
                    ),
                )
                return@withLock
            }
            val before = telemetryProvider.snapshotOrNull()
            mutableState.value = EngineState.Running("generation")
            trySend(TokenEvent.Started(System.currentTimeMillis()))
            try {
                val raw = withContext(Dispatchers.Default) {
                    bindings.generate(handle, prompt, config) { text, index, elapsedMicros ->
                        trySend(TokenEvent.Token(text, index, elapsedMicros / 1_000.0))
                    }
                }
                val after = telemetryProvider.snapshotOrNull()
                val metrics = raw.toMetrics(before, after)
                trySend(TokenEvent.Completed(metrics))
                mutableState.value = EngineState.Ready(model)
            } catch (cancelled: CancellationException) {
                mutableState.value = EngineState.Ready(model)
                throw cancelled
            } catch (error: Exception) {
                val message = error.safeMessage()
                trySend(TokenEvent.Failed(message, recoverable = true))
                mutableState.value = EngineState.Error(message, recoverable = true)
            }
        }
    }

    override fun benchmark(config: BenchmarkConfig): Flow<BenchmarkEvent> = channelFlow {
        operationMutex.withLock {
            if (
                InferenceMode.OPTIMIZED in config.modes &&
                !capabilities().supportsPhaseAwareThreadPolicy
            ) {
                trySend(
                    BenchmarkEvent.Failed(
                        "Optimized mode requires a verified, non-stale phase policy",
                        recoverable = true,
                    ),
                )
                return@withLock
            }
            val model = loadedModel
            val handle = activeHandle.get()
            if (model == null || handle == 0L) {
                trySend(BenchmarkEvent.Failed("Load a GGUF model before benchmarking", true))
                return@withLock
            }
            val totalRuns = config.repetitions * config.modes.size
            val runs = mutableListOf<BenchmarkRun>()
            trySend(BenchmarkEvent.Started(totalRuns))
            mutableState.value = EngineState.Running("benchmark")
            try {
                val warmupText = StringBuilder()
                val warmupBefore = telemetryProvider.snapshotOrNull()
                val warmupRaw = withContext(Dispatchers.Default) {
                    bindings.generate(
                        handle,
                        config.prompt,
                        config.generation.copy(mode = InferenceMode.BASELINE),
                    ) { text, _, _ -> warmupText.append(text) }
                }
                val warmup = BenchmarkRun(
                    mode = InferenceMode.BASELINE,
                    repetition = 0,
                    metrics = warmupRaw.toMetrics(
                        warmupBefore,
                        telemetryProvider.snapshotOrNull(),
                    ),
                    outputSha256 = warmupText.toString().sha256(),
                )

                repeat(config.repetitions) { index ->
                    val repetition = index + 1
                    val order = if (index % 2 == 0) config.modes else config.modes.reversed()
                    order.forEach { mode ->
                        trySend(BenchmarkEvent.RunStarted(mode, repetition))
                        val before = telemetryProvider.snapshotOrNull()
                        val output = StringBuilder()
                        val raw = withContext(Dispatchers.Default) {
                            bindings.generate(
                                handle,
                                config.prompt,
                                config.generation.copy(mode = mode),
                            ) { text, _, _ -> output.append(text) }
                        }
                        val run = BenchmarkRun(
                            mode = mode,
                            repetition = repetition,
                            metrics = raw.toMetrics(before, telemetryProvider.snapshotOrNull()),
                            outputSha256 = output.toString().sha256(),
                        )
                        runs += run
                        trySend(BenchmarkEvent.RunCompleted(run, runs.size, totalRuns))
                    }
                }
                val correctnessMatched = if (
                    InferenceMode.BASELINE in config.modes &&
                    InferenceMode.OPTIMIZED in config.modes
                ) {
                    (1..config.repetitions).all { repetition ->
                        val baseline = runs.firstOrNull {
                            it.repetition == repetition && it.mode == InferenceMode.BASELINE
                        }
                        val optimized = runs.firstOrNull {
                            it.repetition == repetition && it.mode == InferenceMode.OPTIMIZED
                        }
                        baseline != null && optimized != null &&
                            baseline.outputSha256 == optimized.outputSha256
                    }
                } else null
                val result = BenchmarkResult(
                    id = UUID.randomUUID().toString(),
                    createdAtEpochMs = System.currentTimeMillis(),
                    config = config,
                    traceability = Traceability(
                        appCommit = appCommit,
                        llamaCommit = llamaCommit,
                        modelSha256 = model.sha256,
                        modelName = model.displayName,
                        appSourceSha256 = appSourceSha256,
                        llamaSourceDiffSha256 = llamaSourceDiffSha256,
                        nativeLibrarySha256 = nativeLibrarySha256,
                        phasePolicyIdentitySha256 =
                            model.phasePolicy?.profileIdentitySha256,
                        deviceFingerprintSha256 =
                            model.backendPolicy?.deviceFingerprintSha256
                                ?: model.phasePolicy?.deviceFingerprintSha256
                                ?: runtimeDeviceFingerprintSha256,
                        benchmarkBinarySha256 =
                            model.phasePolicy?.benchmarkBinarySha256,
                        sourceReportSha256 = model.backendPolicy?.sourceReportSha256
                            ?: model.phasePolicy?.sourceReportSha256,
                        baselineDecodeThreads = model.phasePolicy?.baselineDecodeThreads,
                        baselinePrefillThreads = model.phasePolicy?.baselinePrefillThreads,
                        decodeThreads = model.phasePolicy?.decodeThreads,
                        prefillThreads = model.phasePolicy?.prefillThreads,
                        executionBackend = model.backend,
                        gpuLayers = model.gpuLayers,
                        backendPolicyIdentitySha256 =
                            model.backendPolicy?.profileIdentitySha256,
                        vulkanDeviceIdentitySha256 = activeVulkanDeviceIdentitySha256,
                    ),
                    warmup = warmup,
                    runs = runs,
                    correctnessMatched = correctnessMatched,
                )
                trySend(BenchmarkEvent.Finished(result))
                mutableState.value = EngineState.Ready(model)
            } catch (cancelled: CancellationException) {
                mutableState.value = EngineState.Ready(model)
                throw cancelled
            } catch (error: Exception) {
                val message = error.safeMessage()
                trySend(BenchmarkEvent.Failed(message, recoverable = true))
                mutableState.value = EngineState.Error(message, recoverable = true)
            }
        }
    }

    override fun cancel() {
        val handle = activeHandle.get()
        if (handle != 0L) runCatching { bindings.cancel(handle) }
    }

    override suspend fun unload() = operationMutex.withLock {
        destroyActiveSession()
        loadedModel = null
        phasePolicyRejection = null
        backendPolicyRejection = null
        activeVulkanDeviceIdentitySha256 = null
        mutableState.value = EngineState.Unloaded
    }

    override fun close() {
        cancel()
        runBlocking { unload() }
    }

    private fun destroyActiveSession() {
        val oldHandle = activeHandle.getAndSet(0L)
        if (oldHandle != 0L) bindings.destroySession(oldHandle)
    }
}

private suspend fun TelemetryProvider?.snapshotOrNull(): TelemetrySnapshot? =
    this?.let { runCatching { it.snapshot() }.getOrNull() }

private fun LongArray.toMetrics(
    before: TelemetrySnapshot?,
    after: TelemetrySnapshot?,
): GenerationMetrics {
    require(size >= 5) { "Native timing result must contain five values" }
    val generated = this[1].toInt()
    val totalMs = this[3] / 1_000.0
    val decodeMs = this[4] / 1_000.0
    return GenerationMetrics(
        promptTokens = this[0].toInt(),
        generatedTokens = generated,
        timeToFirstTokenMs = this[2] / 1_000.0,
        totalDurationMs = totalMs,
        decodeTokensPerSecond =
            if (generated > 0 && decodeMs > 0.0) generated * 1_000.0 / decodeMs else 0.0,
        nativeTiming = true,
        telemetry = RunTelemetry(before, after),
    )
}

private fun Throwable.safeMessage(): String = message ?: javaClass.simpleName

private fun String.sha256(): String = MessageDigest.getInstance("SHA-256")
    .digest(toByteArray(Charsets.UTF_8))
    .joinToString("") { "%02x".format(it) }
