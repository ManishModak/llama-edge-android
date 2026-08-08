package com.manishm.mobilespec.engine

enum class Backend { CPU, VULKAN }

enum class InferenceMode { BASELINE, OPTIMIZED }

const val INTERACTIVE_PHASE_POLICY_WORKLOAD = "interactive-chat-decode-weighted"

data class ModelConfig(
    val path: String,
    val displayName: String,
    val sha256: String,
    val contextSize: Int = 512,
    /** 0 keeps the llama API default; a verified policy overrides it with measured stock widths. */
    val threads: Int = 0,
    val backend: Backend = Backend.CPU,
    val useMmap: Boolean = true,
    val phasePolicy: PhasePolicy? = null,
)

data class PhasePolicy(
    val baselineDecodeThreads: Int,
    val baselinePrefillThreads: Int,
    val decodeThreads: Int,
    val prefillThreads: Int,
    val profileIdentitySha256: String,
    val deviceFingerprintSha256: String,
    val deviceModel: String,
    val socModel: String,
    val benchmarkBinarySha256: String,
    val sourceReportSha256: String,
    val modelSha256: String,
    val llamaCommit: String,
    val contextSize: Int,
    val workloadClass: String,
) {
    fun compatibilityError(
        model: ModelConfig,
        runtimeLlamaCommit: String,
        runtimeDeviceModel: String?,
        runtimeSocModel: String?,
    ): String? = when {
        baselineDecodeThreads <= 0 || baselinePrefillThreads <= 0 ||
            decodeThreads <= 0 || prefillThreads <= 0 ->
            "phase-policy baseline and optimized thread counts must be positive"
        profileIdentitySha256.length != 64 -> "phase-policy identity is missing or malformed"
        deviceFingerprintSha256.length != 64 -> "phase-policy device identity is malformed"
        benchmarkBinarySha256.length != 64 -> "phase-policy benchmark identity is malformed"
        sourceReportSha256.length != 64 -> "phase-policy source report identity is malformed"
        runtimeDeviceModel.isNullOrBlank() || runtimeSocModel.isNullOrBlank() ->
            "runtime device identity is unavailable"
        !deviceModel.equals(runtimeDeviceModel, ignoreCase = true) ->
            "phase-policy device model is stale"
        !socModel.equals(runtimeSocModel, ignoreCase = true) -> "phase-policy SoC is stale"
        !modelSha256.equals(model.sha256, ignoreCase = true) -> "phase-policy model hash is stale"
        llamaCommit != runtimeLlamaCommit -> "phase-policy llama.cpp build is stale"
        contextSize != model.contextSize -> "phase-policy context is stale"
        workloadClass != INTERACTIVE_PHASE_POLICY_WORKLOAD ->
            "phase-policy workload class is stale or unsupported"
        else -> null
    }
}

data class GenerationConfig(
    val maxTokens: Int = 128,
    val temperature: Float = 0.0f,
    val topP: Float = 0.95f,
    val seed: Long = 42L,
    val mode: InferenceMode = InferenceMode.BASELINE,
)

data class EngineCapabilities(
    val nativeAvailable: Boolean,
    val backends: Set<Backend>,
    val supportsSpeculativeDecoding: Boolean,
    val supportsCancellation: Boolean,
    val timingSource: String,
    val supportsPhaseAwareThreadPolicy: Boolean = false,
    val detail: String? = null,
)

data class GenerationMetrics(
    val promptTokens: Int,
    val generatedTokens: Int,
    val timeToFirstTokenMs: Double,
    val totalDurationMs: Double,
    val decodeTokensPerSecond: Double,
    val nativeTiming: Boolean,
    val telemetry: RunTelemetry,
)

data class RunTelemetry(
    val before: TelemetrySnapshot?,
    val after: TelemetrySnapshot?,
)

data class TelemetrySnapshot(
    val timestampEpochMs: Long,
    val thermalStatus: Int,
    val thermalStatusName: String,
    val batteryPercent: Float?,
    val batteryTemperatureC: Float?,
    val charging: Boolean?,
    val availableMemoryBytes: Long,
    val totalMemoryBytes: Long,
    val lowMemory: Boolean,
    val deviceName: String,
    val socName: String,
    val cpuCoreCount: Int,
    val supportedAbis: List<String>,
    val vulkanVersion: String?,
    val vulkanDetail: String?,
    val processResidentSetBytes: Long? = null,
    val processPeakRssBytes: Long? = null,
    val swapTotalBytes: Long? = null,
    val swapFreeBytes: Long? = null,
)

sealed interface TokenEvent {
    data class Started(val startedAtEpochMs: Long) : TokenEvent
    data class Token(val text: String, val index: Int, val elapsedMs: Double) : TokenEvent
    data class Completed(val metrics: GenerationMetrics) : TokenEvent
    data class Failed(val message: String, val recoverable: Boolean) : TokenEvent
}

data class BenchmarkConfig(
    val prompt: String,
    val repetitions: Int = 5,
    val generation: GenerationConfig = GenerationConfig(),
    val modes: List<InferenceMode> =
        listOf(InferenceMode.BASELINE, InferenceMode.OPTIMIZED),
)

data class BenchmarkRun(
    val mode: InferenceMode,
    val repetition: Int,
    val metrics: GenerationMetrics,
    val outputSha256: String,
)

data class Traceability(
    val appCommit: String,
    val llamaCommit: String,
    val modelSha256: String,
    val modelName: String,
    val appSourceSha256: String? = null,
    val llamaSourceDiffSha256: String? = null,
    val nativeLibrarySha256: String? = null,
    val phasePolicyIdentitySha256: String? = null,
    val deviceFingerprintSha256: String? = null,
    val benchmarkBinarySha256: String? = null,
    val sourceReportSha256: String? = null,
    val baselineDecodeThreads: Int? = null,
    val baselinePrefillThreads: Int? = null,
    val decodeThreads: Int? = null,
    val prefillThreads: Int? = null,
)

data class BenchmarkResult(
    val id: String,
    val createdAtEpochMs: Long,
    val config: BenchmarkConfig,
    val traceability: Traceability,
    val warmup: BenchmarkRun? = null,
    val runs: List<BenchmarkRun>,
    val correctnessMatched: Boolean? = null,
)

data class BenchmarkSummary(
    val mode: InferenceMode,
    val completedRuns: Int,
    val meanTokensPerSecond: Double,
    val meanTimeToFirstTokenMs: Double,
)

fun BenchmarkResult.summary(mode: InferenceMode): BenchmarkSummary {
    val matching = runs.filter { it.mode == mode }
    return BenchmarkSummary(
        mode = mode,
        completedRuns = matching.size,
        meanTokensPerSecond = matching.map { it.metrics.decodeTokensPerSecond }.averageOrZero(),
        meanTimeToFirstTokenMs = matching.map { it.metrics.timeToFirstTokenMs }.averageOrZero(),
    )
}

private fun List<Double>.averageOrZero(): Double = if (isEmpty()) 0.0 else average()

sealed interface BenchmarkEvent {
    data class Started(val totalRuns: Int) : BenchmarkEvent
    data class RunStarted(val mode: InferenceMode, val repetition: Int) : BenchmarkEvent
    data class RunCompleted(val run: BenchmarkRun, val completedRuns: Int, val totalRuns: Int) :
        BenchmarkEvent
    data class Finished(val result: BenchmarkResult) : BenchmarkEvent
    data class Failed(val message: String, val recoverable: Boolean) : BenchmarkEvent
}
