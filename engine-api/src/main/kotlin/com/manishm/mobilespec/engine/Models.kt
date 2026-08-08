package com.manishm.mobilespec.engine

import java.security.MessageDigest

enum class Backend { CPU, VULKAN, HYBRID, AUTO }

enum class InferenceMode { BASELINE, OPTIMIZED }

const val INTERACTIVE_PHASE_POLICY_WORKLOAD = "interactive-chat-decode-weighted"
const val EXECUTION_POLICY_SCORING = "e2e-3pct-correctness-memory-thermal-stability"

data class ModelConfig(
    val path: String,
    val displayName: String,
    val sha256: String,
    val contextSize: Int = 512,
    /** 0 keeps the llama API default; a verified policy overrides it with measured stock widths. */
    val threads: Int = 0,
    val backend: Backend = Backend.CPU,
    /** CPU=0, Vulkan=-1 (all), Hybrid=positive bounded layer count. */
    val gpuLayers: Int = 0,
    val useMmap: Boolean = true,
    val phasePolicy: PhasePolicy? = null,
    val backendPolicy: BackendPolicy? = null,
    /** Populated by the native engine after model load; ignored as an input hint. */
    val modelLayerCount: Int? = null,
)

data class BackendPolicy(
    val backend: Backend,
    val gpuLayers: Int,
    val profileIdentitySha256: String,
    val deviceFingerprintSha256: String,
    val vulkanDeviceIdentitySha256: String,
    val nativeLibrarySha256: String,
    val sourceReportSha256: String,
    val cpuPhasePolicyIdentitySha256: String,
    val deviceModel: String,
    val socModel: String,
    val modelSha256: String,
    val llamaCommit: String,
    val contextSize: Int,
    val qualificationPromptSha256: String,
    val qualificationMaxTokens: Int,
    val qualificationTemperature: Float,
    val qualificationSeed: Long,
    val workloadClass: String,
    val scoringPolicy: String,
) {
    fun compatibilityError(
        model: ModelConfig,
        runtimeLlamaCommit: String,
        runtimeNativeLibrarySha256: String?,
        runtimeDeviceFingerprintSha256: String?,
        runtimeDeviceModel: String?,
        runtimeSocModel: String?,
        runtimeVulkanDeviceIdentitySha256: String?,
    ): String? = when {
        backend != Backend.VULKAN && backend != Backend.HYBRID ->
            "backend policy must select Vulkan or Hybrid"
        backend == Backend.VULKAN && gpuLayers != -1 ->
            "Vulkan backend policy must request full offload"
        backend == Backend.HYBRID && gpuLayers <= 0 ->
            "Hybrid backend policy must request a positive layer count"
        profileIdentitySha256.length != 64 -> "backend-policy identity is missing or malformed"
        deviceFingerprintSha256.length != 64 -> "backend-policy device identity is malformed"
        vulkanDeviceIdentitySha256.length != 64 -> "Vulkan device identity is malformed"
        nativeLibrarySha256.length != 64 -> "backend-policy native-library identity is malformed"
        sourceReportSha256.length != 64 -> "backend-policy source report identity is malformed"
        cpuPhasePolicyIdentitySha256.length != 64 ->
            "backend-policy CPU phase identity is malformed"
        qualificationPromptSha256.length != 64 ->
            "backend-policy qualification prompt identity is malformed"
        qualificationMaxTokens <= 0 || !qualificationTemperature.isFinite() ->
            "backend-policy qualification generation shape is malformed"
        runtimeDeviceModel.isNullOrBlank() || runtimeSocModel.isNullOrBlank() ->
            "runtime device identity is unavailable"
        runtimeNativeLibrarySha256.isNullOrBlank() ->
            "runtime native-library identity is unavailable"
        runtimeDeviceFingerprintSha256.isNullOrBlank() ->
            "runtime device fingerprint is unavailable"
        runtimeVulkanDeviceIdentitySha256.isNullOrBlank() ->
            "runtime Vulkan device identity is unavailable"
        !deviceModel.equals(runtimeDeviceModel, ignoreCase = true) ->
            "backend-policy device model is stale"
        !socModel.equals(runtimeSocModel, ignoreCase = true) -> "backend-policy SoC is stale"
        !deviceFingerprintSha256.equals(runtimeDeviceFingerprintSha256, ignoreCase = true) ->
            "backend-policy device fingerprint is stale"
        !modelSha256.equals(model.sha256, ignoreCase = true) -> "backend-policy model hash is stale"
        llamaCommit != runtimeLlamaCommit -> "backend-policy llama.cpp build is stale"
        !nativeLibrarySha256.equals(runtimeNativeLibrarySha256, ignoreCase = true) ->
            "backend-policy native library is stale"
        !vulkanDeviceIdentitySha256.equals(runtimeVulkanDeviceIdentitySha256, ignoreCase = true) ->
            "backend-policy Vulkan driver/device is stale"
        model.phasePolicy == null -> "backend-policy CPU phase policy is unavailable"
        !cpuPhasePolicyIdentitySha256.equals(
            model.phasePolicy.profileIdentitySha256,
            ignoreCase = true,
        ) -> "backend-policy CPU phase policy is stale"
        contextSize != model.contextSize -> "backend-policy context is stale"
        workloadClass != INTERACTIVE_PHASE_POLICY_WORKLOAD ->
            "backend-policy workload class is stale or unsupported"
        scoringPolicy != EXECUTION_POLICY_SCORING ->
            "backend-policy scoring policy is stale or unsupported"
        else -> null
    }
}

fun runtimeDeviceFingerprintSha256(deviceModel: String, socModel: String, cpuCoreCount: Int): String {
    require(deviceModel.isNotBlank() && socModel.isNotBlank() && cpuCoreCount > 0) {
        "device model, SoC, and CPU core count are required"
    }
    val material = listOf(
        deviceModel.trim().lowercase(),
        socModel.trim().lowercase(),
        cpuCoreCount,
    ).joinToString("|")
    return MessageDigest.getInstance("SHA-256")
        .digest(material.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}

fun vulkanDeviceIdentitySha256(devices: List<VulkanDeviceInfo>): String? {
    val material = devices.sortedBy(VulkanDeviceInfo::identityMaterial)
        .joinToString("\n", transform = VulkanDeviceInfo::identityMaterial)
        .takeIf(String::isNotBlank)
        ?: return null
    return MessageDigest.getInstance("SHA-256")
        .digest(material.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}

/** CPU, roughly 25/50/75 percent partial offload, and full Vulkan offload (-1). */
fun gpuLayerCandidates(modelLayerCount: Int): List<Int> {
    require(modelLayerCount > 0) { "model layer count must be positive" }
    val partial = if (modelLayerCount == 1) emptyList() else listOf(0.25, 0.50, 0.75)
        .map { fraction -> (modelLayerCount * fraction).toInt().coerceIn(1, modelLayerCount - 1) }
    return (listOf(0) + partial + listOf(-1)).distinct()
}

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
    val supportsGpuOffload: Boolean = false,
    val supportsHybridOffload: Boolean = false,
    val supportsKleidiAI: Boolean = false,
    val backendDevices: List<BackendDeviceInfo> = emptyList(),
    val vulkanDevices: List<VulkanDeviceInfo> = emptyList(),
    val detail: String? = null,
)

data class BackendDeviceInfo(
    val name: String,
    val description: String,
    val type: String,
    val memoryFreeBytes: Long,
    val memoryTotalBytes: Long,
    val asynchronous: Boolean = false,
    val hostBuffer: Boolean = false,
    val bufferFromHostPointer: Boolean = false,
    val events: Boolean = false,
)

data class VulkanDeviceInfo(
    val name: String,
    val vendorId: Long,
    val deviceId: Long,
    val apiVersion: String,
    val driverVersionRaw: Long,
    val driverVersion: String,
    val deviceType: String,
    val fp16: Boolean,
    val integerDotProduct: Boolean,
    val cooperativeMatrix: Boolean,
    val cooperativeMatrix2: Boolean,
) {
    val unifiedMemory: Boolean
        get() = deviceType == "INTEGRATED_GPU"

    fun identityMaterial(): String = listOf(
        name,
        vendorId,
        deviceId,
        apiVersion,
        driverVersionRaw,
        deviceType,
        fp16,
        integerDotProduct,
        cooperativeMatrix,
        cooperativeMatrix2,
    ).joinToString("|")
}

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
    val executionBackend: Backend = Backend.CPU,
    val gpuLayers: Int = 0,
    val backendPolicyIdentitySha256: String? = null,
    val vulkanDeviceIdentitySha256: String? = null,
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
