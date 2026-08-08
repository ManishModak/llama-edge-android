package com.manishm.mobilespec.engine

import java.util.Locale

data class BackendQualificationTraceability(
    val appCommit: String,
    val appSourceSha256: String?,
    val llamaCommit: String,
    val llamaSourceDiffSha256: String?,
    val nativeLibrarySha256: String?,
    val modelSha256: String,
    val modelName: String,
    val contextSize: Int,
    val phasePolicyIdentitySha256: String?,
    val deviceFingerprintSha256: String?,
    val deviceModel: String,
    val socModel: String,
    val vulkanDeviceIdentitySha256: String?,
    val promptSha256: String,
    val maxTokens: Int,
    val temperature: Float,
    val seed: Long,
    val workloadClass: String = INTERACTIVE_PHASE_POLICY_WORKLOAD,
    val scoringPolicy: String = EXECUTION_POLICY_SCORING,
)

data class BackendQualificationRecord(
    val evidence: BackendQualificationEvidence,
    val evaluation: BackendQualificationEvaluation,
)

data class BackendQualificationReport(
    val id: String,
    val createdAtEpochMs: Long,
    val traceability: BackendQualificationTraceability,
    val records: List<BackendQualificationRecord>,
)

object BackendQualificationJson {
    fun encode(report: BackendQualificationReport): String = buildString {
        append("{\n  \"schemaVersion\":1,")
        append("\n  \"id\":").string(report.id).append(',')
        append("\n  \"createdAtEpochMs\":").append(report.createdAtEpochMs).append(',')
        append("\n  \"traceability\":")
        appendTraceability(report.traceability)
        append(",\n  \"records\":[")
        report.records.forEachIndexed { index, record ->
            if (index > 0) append(',')
            append("\n    ")
            appendRecord(record)
        }
        if (report.records.isNotEmpty()) append('\n').append("  ")
        append("]\n}")
    }

    private fun StringBuilder.appendTraceability(value: BackendQualificationTraceability) {
        append('{')
        name("appCommit").string(value.appCommit).append(',')
        name("appSourceSha256").nullableString(value.appSourceSha256).append(',')
        name("llamaCommit").string(value.llamaCommit).append(',')
        name("llamaSourceDiffSha256").nullableString(value.llamaSourceDiffSha256).append(',')
        name("nativeLibrarySha256").nullableString(value.nativeLibrarySha256).append(',')
        name("modelSha256").string(value.modelSha256).append(',')
        name("modelName").string(value.modelName).append(',')
        name("contextSize").append(value.contextSize).append(',')
        name("phasePolicyIdentitySha256").nullableString(value.phasePolicyIdentitySha256).append(',')
        name("deviceFingerprintSha256").nullableString(value.deviceFingerprintSha256).append(',')
        name("deviceModel").string(value.deviceModel).append(',')
        name("socModel").string(value.socModel).append(',')
        name("vulkanDeviceIdentitySha256").nullableString(value.vulkanDeviceIdentitySha256).append(',')
        name("promptSha256").string(value.promptSha256).append(',')
        name("maxTokens").append(value.maxTokens).append(',')
        name("temperature").append(number(value.temperature.toDouble())).append(',')
        name("seed").append(value.seed).append(',')
        name("workloadClass").string(value.workloadClass).append(',')
        name("scoringPolicy").string(value.scoringPolicy)
        append('}')
    }

    private fun StringBuilder.appendRecord(record: BackendQualificationRecord) {
        val evidence = record.evidence
        append('{')
        name("candidate").appendCandidate(evidence.candidate).append(',')
        name("baselinePreflight").appendPreflight(evidence.baselinePreflight).append(',')
        name("candidatePreflight").appendPreflight(evidence.candidatePreflight).append(',')
        name("discardedWarmup")
        if (evidence.discardedWarmup == null) append("null") else appendMeasurement(evidence.discardedWarmup)
        append(',')
        name("pairs").append('[')
        evidence.pairs.forEachIndexed { index, pair ->
            if (index > 0) append(',')
            append('{')
            name("repetition").append(pair.repetition).append(',')
            name("candidateFirst").append(pair.candidateFirst).append(',')
            name("baseline").appendMeasurement(pair.baseline).append(',')
            name("candidate").appendMeasurement(pair.candidate)
            append('}')
        }
        append("],")
        name("evaluation").appendEvaluation(record.evaluation)
        append('}')
    }

    private fun StringBuilder.appendCandidate(value: BackendCandidate): StringBuilder {
        append('{')
        name("backend").string(value.backend.name).append(',')
        name("gpuLayers").append(value.gpuLayers)
        return append('}')
    }

    private fun StringBuilder.appendPreflight(value: QualificationPreflight): StringBuilder {
        append('{')
        name("modelLoadSucceeded").append(value.modelLoadSucceeded).append(',')
        name("loadDurationMs").append(value.loadDurationMs?.let(::number) ?: "null").append(',')
        name("loadTelemetry").appendTelemetry(value.loadTelemetry).append(',')
        name("nonEmptyGreedyOutput").append(value.nonEmptyGreedyOutput).append(',')
        name("greedyOutputSha256").nullableString(value.greedyOutputSha256).append(',')
        name("requiredOperationsPassed").append(value.requiredOperationsPassed ?: "null").append(',')
        name("requiredOperationsDetail").string(value.requiredOperationsDetail).append(',')
        name("cancellationPassed").append(value.cancellationPassed).append(',')
        name("reusePassed").append(value.reusePassed).append(',')
        name("deviceAvailableAfter").append(value.deviceAvailableAfter).append(',')
        name("failure").nullableString(value.failure)
        return append('}')
    }

    private fun StringBuilder.appendMeasurement(value: QualificationMeasurement): StringBuilder {
        append('{')
        name("backend").string(value.backend.name).append(',')
        name("gpuLayers").append(value.gpuLayers).append(',')
        name("outputSha256").string(value.outputSha256).append(',')
        name("metrics").appendMetrics(value.metrics)
        return append('}')
    }

    private fun StringBuilder.appendMetrics(value: GenerationMetrics): StringBuilder {
        append('{')
        name("promptTokens").append(value.promptTokens).append(',')
        name("generatedTokens").append(value.generatedTokens).append(',')
        name("timeToFirstTokenMs").append(number(value.timeToFirstTokenMs)).append(',')
        name("totalDurationMs").append(number(value.totalDurationMs)).append(',')
        name("decodeTokensPerSecond").append(number(value.decodeTokensPerSecond)).append(',')
        name("nativeTiming").append(value.nativeTiming).append(',')
        name("telemetry").appendTelemetry(value.telemetry)
        return append('}')
    }

    private fun StringBuilder.appendTelemetry(value: RunTelemetry?): StringBuilder {
        if (value == null) return append("null")
        append('{')
        name("before").appendSnapshot(value.before).append(',')
        name("after").appendSnapshot(value.after)
        return append('}')
    }

    private fun StringBuilder.appendSnapshot(value: TelemetrySnapshot?): StringBuilder {
        if (value == null) return append("null")
        append('{')
        name("timestampEpochMs").append(value.timestampEpochMs).append(',')
        name("thermalStatus").append(value.thermalStatus).append(',')
        name("thermalStatusName").string(value.thermalStatusName).append(',')
        name("batteryTemperatureC").append(value.batteryTemperatureC?.let { number(it.toDouble()) } ?: "null").append(',')
        name("charging").append(value.charging ?: "null").append(',')
        name("availableMemoryBytes").append(value.availableMemoryBytes).append(',')
        name("totalMemoryBytes").append(value.totalMemoryBytes).append(',')
        name("lowMemory").append(value.lowMemory).append(',')
        name("processResidentSetBytes").append(value.processResidentSetBytes ?: "null").append(',')
        name("processPeakRssBytes").append(value.processPeakRssBytes ?: "null").append(',')
        name("swapTotalBytes").append(value.swapTotalBytes ?: "null").append(',')
        name("swapFreeBytes").append(value.swapFreeBytes ?: "null")
        return append('}')
    }

    private fun StringBuilder.appendEvaluation(value: BackendQualificationEvaluation): StringBuilder {
        append('{')
        name("verdict").string(value.verdict.name).append(',')
        name("endToEndImprovement").append(value.endToEndImprovement?.let(::number) ?: "null").append(',')
        name("noiseFloor").append(value.noiseFloor?.let(::number) ?: "null").append(',')
        name("gates").append('[')
        value.gates.forEachIndexed { index, gate ->
            if (index > 0) append(',')
            append('{')
            name("name").string(gate.name).append(',')
            name("status").string(gate.status.name).append(',')
            name("detail").string(gate.detail)
            append('}')
        }
        append(']')
        return append('}')
    }

    private fun StringBuilder.name(value: String): StringBuilder =
        string(value).append(':')

    private fun StringBuilder.string(value: String): StringBuilder =
        append('"').append(escape(value)).append('"')

    private fun StringBuilder.nullableString(value: String?): StringBuilder =
        if (value == null) append("null") else string(value)

    private fun number(value: Double): String =
        if (value.isFinite()) String.format(Locale.US, "%.6f", value) else "null"

    private fun escape(value: String): String = buildString(value.length) {
        value.forEach { char ->
            when (char) {
                '\\' -> append("\\\\")
                '"' -> append("\\\"")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (char.code < 0x20) append("\\u%04x".format(char.code)) else append(char)
            }
        }
    }
}
