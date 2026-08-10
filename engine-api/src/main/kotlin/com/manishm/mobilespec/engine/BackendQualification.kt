package com.manishm.mobilespec.engine

import java.util.Locale
import kotlin.math.max
import kotlin.math.sqrt

data class BackendCandidate(
    val backend: Backend,
    val gpuLayers: Int,
) {
    init {
        require(backend == Backend.VULKAN || backend == Backend.HYBRID)
        require(
            (backend == Backend.VULKAN && gpuLayers == -1) ||
                (backend == Backend.HYBRID && gpuLayers > 0),
        )
    }
}

data class QualificationPreflight(
    val modelLoadSucceeded: Boolean,
    val loadDurationMs: Double?,
    val loadTelemetry: RunTelemetry?,
    val nonEmptyGreedyOutput: Boolean,
    val greedyOutputSha256: String?,
    val requiredOperationsPassed: Boolean?,
    val requiredOperationsDetail: String,
    val cancellationPassed: Boolean,
    val reusePassed: Boolean,
    val deviceAvailableAfter: Boolean,
    val failure: String? = null,
)

data class QualificationMeasurement(
    val backend: Backend,
    val gpuLayers: Int,
    val outputSha256: String,
    val metrics: GenerationMetrics,
)

data class QualificationPair(
    val repetition: Int,
    val candidateFirst: Boolean,
    val baseline: QualificationMeasurement,
    val candidate: QualificationMeasurement,
)

data class BackendQualificationEvidence(
    val candidate: BackendCandidate,
    val baselinePreflight: QualificationPreflight,
    val candidatePreflight: QualificationPreflight,
    val discardedWarmup: QualificationMeasurement?,
    val pairs: List<QualificationPair>,
)

enum class QualificationGateStatus { PASS, FAIL, INCONCLUSIVE }

data class QualificationGate(
    val name: String,
    val status: QualificationGateStatus,
    val detail: String,
)

enum class BackendQualificationVerdict { QUALIFIED, REJECTED, INCONCLUSIVE }

data class BackendQualificationThresholds(
    val scoredRepetitions: Int = 3,
    val minimumEndToEndImprovement: Double = 0.03,
    val maximumRegression: Double = 0.03,
    val maximumAdditionalCv: Double = 0.03,
    val maximumAdditionalSwapDropBytes: Long = 64L * 1024 * 1024,
    val maximumStartThermalStatus: Int = 0,
    val maximumEndThermalStatus: Int = 3,
)

data class BackendQualificationEvaluation(
    val verdict: BackendQualificationVerdict,
    val gates: List<QualificationGate>,
    val endToEndImprovement: Double?,
    val noiseFloor: Double?,
)

object BackendQualificationEvaluator {
    fun evaluate(
        evidence: BackendQualificationEvidence,
        thresholds: BackendQualificationThresholds = BackendQualificationThresholds(),
    ): BackendQualificationEvaluation {
        val gates = mutableListOf<QualificationGate>()
        gates += booleanGate(
            "baseline-preflight",
            evidence.baselinePreflight.modelLoadSucceeded &&
                evidence.baselinePreflight.nonEmptyGreedyOutput &&
                evidence.baselinePreflight.cancellationPassed &&
                evidence.baselinePreflight.reusePassed &&
                evidence.baselinePreflight.deviceAvailableAfter,
            evidence.baselinePreflight.failure ?: "CPU load/output/cancel/reuse/device checks",
        )
        gates += booleanGate(
            "candidate-preflight",
            evidence.candidatePreflight.modelLoadSucceeded &&
                evidence.candidatePreflight.nonEmptyGreedyOutput &&
                evidence.candidatePreflight.cancellationPassed &&
                evidence.candidatePreflight.reusePassed &&
                evidence.candidatePreflight.deviceAvailableAfter,
            evidence.candidatePreflight.failure ?: "candidate load/output/cancel/reuse/device checks",
        )
        gates += when (evidence.candidatePreflight.requiredOperationsPassed) {
            true -> pass("required-operations", evidence.candidatePreflight.requiredOperationsDetail)
            false -> fail("required-operations", evidence.candidatePreflight.requiredOperationsDetail)
            null -> inconclusive("required-operations", evidence.candidatePreflight.requiredOperationsDetail)
        }
        gates += booleanGate(
            "preflight-correctness",
            evidence.baselinePreflight.greedyOutputSha256?.let { reference ->
                reference.length == 64 &&
                    reference.equals(
                        evidence.candidatePreflight.greedyOutputSha256,
                        ignoreCase = true,
                    )
            } == true,
            "CPU and candidate greedy output hashes must match",
        )
        gates += ratioNoRegressionGate(
            "model-load",
            evidence.baselinePreflight.loadDurationMs,
            evidence.candidatePreflight.loadDurationMs,
            thresholds.maximumRegression,
        )
        gates += preflightThermalGate(evidence, thresholds)
        gates += preflightMemoryGate(evidence, thresholds)
        if (gates.any { it.status == QualificationGateStatus.FAIL }) {
            return BackendQualificationEvaluation(
                BackendQualificationVerdict.REJECTED,
                gates,
                endToEndImprovement = null,
                noiseFloor = null,
            )
        }
        if (gates.any { it.status == QualificationGateStatus.INCONCLUSIVE }) {
            return BackendQualificationEvaluation(
                BackendQualificationVerdict.INCONCLUSIVE,
                gates,
                endToEndImprovement = null,
                noiseFloor = null,
            )
        }
        gates += if (evidence.discardedWarmup == null) {
            inconclusive("discarded-warmup", "candidate warm-up is missing")
        } else if (
            evidence.discardedWarmup.backend != evidence.candidate.backend ||
            evidence.discardedWarmup.gpuLayers != evidence.candidate.gpuLayers
        ) {
            fail("discarded-warmup", "warm-up does not match the declared candidate")
        } else {
            pass("discarded-warmup", "one candidate warm-up was excluded from scoring")
        }

        val pairs = evidence.pairs.sortedBy(QualificationPair::repetition)
        val repetitionReady = pairs.size == thresholds.scoredRepetitions &&
            pairs.map(QualificationPair::repetition) == (1..thresholds.scoredRepetitions).toList()
        gates += booleanGate(
            "scored-repetitions",
            repetitionReady,
            "${pairs.size}/${thresholds.scoredRepetitions} complete scored pairs",
        )
        val counterbalanced = pairs.size >= 2 &&
            pairs.zipWithNext().all { (first, second) ->
                first.candidateFirst != second.candidateFirst
            }
        gates += booleanGate(
            "counterbalanced-order",
            counterbalanced,
            "candidate-first order must alternate between scored pairs",
        )

        val measurementsMatchCandidate = pairs.all { pair ->
            pair.baseline.backend == Backend.CPU && pair.baseline.gpuLayers == 0 &&
                pair.candidate.backend == evidence.candidate.backend &&
                pair.candidate.gpuLayers == evidence.candidate.gpuLayers
        }
        gates += booleanGate(
            "candidate-identity",
            pairs.isNotEmpty() && measurementsMatchCandidate,
            "every measurement must use the declared CPU/candidate placement",
        )
        gates += booleanGate(
            "native-timing",
            pairs.isNotEmpty() && pairs.all {
                it.baseline.metrics.nativeTiming && it.candidate.metrics.nativeTiming
            },
            "every scored measurement must use native monotonic timing",
        )
        val exactOutputs = pairs.isNotEmpty() && pairs.all { pair ->
            pair.baseline.outputSha256.length == 64 &&
                pair.baseline.outputSha256.equals(pair.candidate.outputSha256, ignoreCase = true)
        }
        gates += booleanGate(
            "scored-correctness",
            exactOutputs,
            "every scored CPU/candidate output hash must match",
        )

        val baseline = pairs.map { it.baseline }
        val candidate = pairs.map { it.candidate }
        val baselineTtft = baseline.map { it.metrics.timeToFirstTokenMs }.meanOrNull()
        val candidateTtft = candidate.map { it.metrics.timeToFirstTokenMs }.meanOrNull()
        gates += ratioNoRegressionGate(
            "ttft",
            baselineTtft,
            candidateTtft,
            thresholds.maximumRegression,
        )

        val baselineDuration = baseline.map { it.metrics.totalDurationMs }
        val candidateDuration = candidate.map { it.metrics.totalDurationMs }
        val baselineMean = baselineDuration.meanOrNull()
        val candidateMean = candidateDuration.meanOrNull()
        val endToEndImprovement = if (
            baselineMean != null && candidateMean != null && baselineMean > 0.0
        ) 1.0 - candidateMean / baselineMean else null
        val baselineCv = baselineDuration.cvOrNull()
        val candidateCv = candidateDuration.cvOrNull()
        val noiseFloor = if (baselineCv != null && candidateCv != null) {
            sqrt(baselineCv * baselineCv + candidateCv * candidateCv)
        } else null
        val requiredImprovement = max(
            thresholds.minimumEndToEndImprovement,
            noiseFloor ?: thresholds.minimumEndToEndImprovement,
        )
        gates += if (endToEndImprovement == null) {
            inconclusive("end-to-end-improvement", "valid duration samples are missing")
        } else if (endToEndImprovement >= requiredImprovement) {
            pass(
                "end-to-end-improvement",
                "improvement=${endToEndImprovement.percent()} required=${requiredImprovement.percent()}",
            )
        } else {
            fail(
                "end-to-end-improvement",
                "improvement=${endToEndImprovement.percent()} required=${requiredImprovement.percent()}",
            )
        }
        gates += if (baselineCv == null || candidateCv == null) {
            inconclusive("stability", "at least two finite duration samples per arm are required")
        } else if (candidateCv <= baselineCv + thresholds.maximumAdditionalCv) {
            pass("stability", "CPU CV=${baselineCv.percent()}, candidate CV=${candidateCv.percent()}")
        } else {
            fail("stability", "CPU CV=${baselineCv.percent()}, candidate CV=${candidateCv.percent()}")
        }

        gates += thermalGate(evidence, thresholds)
        gates += memoryGate(evidence, thresholds)

        val verdict = when {
            gates.any { it.status == QualificationGateStatus.FAIL } ->
                BackendQualificationVerdict.REJECTED
            gates.any { it.status == QualificationGateStatus.INCONCLUSIVE } ->
                BackendQualificationVerdict.INCONCLUSIVE
            else -> BackendQualificationVerdict.QUALIFIED
        }
        return BackendQualificationEvaluation(verdict, gates, endToEndImprovement, noiseFloor)
    }

    private fun thermalGate(
        evidence: BackendQualificationEvidence,
        thresholds: BackendQualificationThresholds,
    ): QualificationGate {
        if (
            evidence.baselinePreflight.loadTelemetry == null ||
            evidence.candidatePreflight.loadTelemetry == null
        ) {
            return inconclusive("thermal", "load and generation thermal snapshots are required")
        }
        val measured = evidence.pairs.flatMap { listOf(it.baseline, it.candidate) }
            .map { it.metrics.telemetry }
        val snapshots = listOfNotNull(
            evidence.baselinePreflight.loadTelemetry,
            evidence.candidatePreflight.loadTelemetry,
        ) + measured
        if (snapshots.isEmpty() || snapshots.any { it.before == null || it.after == null }) {
            return inconclusive("thermal", "complete before/after thermal snapshots are required")
        }
        val starts = snapshots.map { requireNotNull(it.before).thermalStatus }
        val ends = snapshots.map { requireNotNull(it.after).thermalStatus }
        return if (
            starts.all { it in 0..thresholds.maximumStartThermalStatus } &&
            ends.all { it in 0..thresholds.maximumEndThermalStatus }
        ) {
            pass("thermal", "start<=${thresholds.maximumStartThermalStatus}, end<=${thresholds.maximumEndThermalStatus}")
        } else {
            fail("thermal", "observed start=${starts.maxOrNull()}, end=${ends.maxOrNull()}")
        }
    }

    private fun preflightThermalGate(
        evidence: BackendQualificationEvidence,
        thresholds: BackendQualificationThresholds,
    ): QualificationGate {
        val telemetry = listOf(
            evidence.baselinePreflight.loadTelemetry,
            evidence.candidatePreflight.loadTelemetry,
        )
        if (telemetry.any { it?.before == null || it.after == null }) {
            return inconclusive("preflight-thermal", "complete load thermal snapshots are required")
        }
        val starts = telemetry.map { requireNotNull(it?.before).thermalStatus }
        val ends = telemetry.map { requireNotNull(it?.after).thermalStatus }
        return if (
            starts.all { it in 0..thresholds.maximumStartThermalStatus } &&
            ends.all { it in 0..thresholds.maximumEndThermalStatus }
        ) {
            pass("preflight-thermal", "load start/end thermal gates passed")
        } else {
            fail("preflight-thermal", "observed start=${starts.maxOrNull()}, end=${ends.maxOrNull()}")
        }
    }

    private fun preflightMemoryGate(
        evidence: BackendQualificationEvidence,
        thresholds: BackendQualificationThresholds,
    ): QualificationGate {
        val baseline = evidence.baselinePreflight.loadTelemetry
        val candidate = evidence.candidatePreflight.loadTelemetry
        if (
            baseline?.before?.swapFreeBytes == null || baseline.after?.swapFreeBytes == null ||
            candidate?.before?.swapFreeBytes == null || candidate.after?.swapFreeBytes == null
        ) {
            return inconclusive("preflight-memory-swap", "complete load SwapFree snapshots are required")
        }
        val lowMemory = requireNotNull(candidate.before).lowMemory || requireNotNull(candidate.after).lowMemory
        val baselineDrop = baseline.swapDropBytes()
        val candidateDrop = candidate.swapDropBytes()
        val limit = baselineDrop + thresholds.maximumAdditionalSwapDropBytes
        return if (!lowMemory && candidateDrop <= limit) {
            pass("preflight-memory-swap", "candidate load drop=$candidateDrop B, allowed=$limit B")
        } else {
            fail(
                "preflight-memory-swap",
                "lowMemory=$lowMemory, candidate load drop=$candidateDrop B, allowed=$limit B",
            )
        }
    }

    private fun memoryGate(
        evidence: BackendQualificationEvidence,
        thresholds: BackendQualificationThresholds,
    ): QualificationGate {
        if (
            evidence.baselinePreflight.loadTelemetry == null ||
            evidence.candidatePreflight.loadTelemetry == null
        ) {
            return inconclusive("memory-swap", "load and generation SwapFree snapshots are required")
        }
        val baseline = listOfNotNull(evidence.baselinePreflight.loadTelemetry) +
            evidence.pairs.map { it.baseline.metrics.telemetry }
        val candidate = listOfNotNull(evidence.candidatePreflight.loadTelemetry) +
            evidence.pairs.map { it.candidate.metrics.telemetry }
        if (
            baseline.isEmpty() ||
            (baseline + candidate).any {
                it.before?.swapFreeBytes == null || it.after?.swapFreeBytes == null
            }
        ) {
            return inconclusive("memory-swap", "complete SwapFree snapshots are required")
        }
        val lowMemory = (baseline + candidate).any {
            requireNotNull(it.before).lowMemory || requireNotNull(it.after).lowMemory
        }
        val baselineDrop = baseline.maxOf { it.swapDropBytes() }
        val candidateDrop = candidate.maxOf { it.swapDropBytes() }
        val limit = baselineDrop + thresholds.maximumAdditionalSwapDropBytes
        return if (!lowMemory && candidateDrop <= limit) {
            pass("memory-swap", "candidate drop=$candidateDrop B, allowed=$limit B")
        } else {
            fail(
                "memory-swap",
                "lowMemory=$lowMemory, candidate drop=$candidateDrop B, allowed=$limit B",
            )
        }
    }
}

private fun ratioNoRegressionGate(
    name: String,
    baseline: Double?,
    candidate: Double?,
    maximumRegression: Double,
): QualificationGate = when {
    baseline == null || candidate == null || baseline <= 0.0 || !candidate.isFinite() ->
        inconclusive(name, "finite positive CPU/candidate values are required")
    candidate <= baseline * (1.0 + maximumRegression) ->
        pass(name, "CPU=${baseline.format()}, candidate=${candidate.format()}")
    else -> fail(name, "CPU=${baseline.format()}, candidate=${candidate.format()}")
}

private fun booleanGate(name: String, passed: Boolean, detail: String): QualificationGate =
    if (passed) pass(name, detail) else fail(name, detail)

private fun pass(name: String, detail: String) =
    QualificationGate(name, QualificationGateStatus.PASS, detail)

private fun fail(name: String, detail: String) =
    QualificationGate(name, QualificationGateStatus.FAIL, detail)

private fun inconclusive(name: String, detail: String) =
    QualificationGate(name, QualificationGateStatus.INCONCLUSIVE, detail)

private fun List<Double>.meanOrNull(): Double? =
    takeIf { isNotEmpty() && all(Double::isFinite) }?.average()

private fun List<Double>.cvOrNull(): Double? {
    if (size < 2 || any { !it.isFinite() }) return null
    val mean = average()
    if (mean <= 0.0) return null
    val variance = sumOf { value -> (value - mean) * (value - mean) } / (size - 1)
    return sqrt(variance) / mean
}

private fun RunTelemetry.swapDropBytes(): Long =
    (requireNotNull(before).swapFreeBytes!! - requireNotNull(after).swapFreeBytes!!).coerceAtLeast(0)

private fun Double.percent(): String = String.format(Locale.US, "%.2f%%", this * 100.0)
private fun Double.format(): String = String.format(Locale.US, "%.3f", this)
