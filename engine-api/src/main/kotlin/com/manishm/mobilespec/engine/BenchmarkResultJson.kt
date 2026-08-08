package com.manishm.mobilespec.engine

import java.util.Locale

object BenchmarkResultJson {
    fun encode(result: BenchmarkResult): String = buildString {
        append("{\n")
        field("schemaVersion", "1", quoted = false)
        field("id", result.id)
        field("createdAtEpochMs", result.createdAtEpochMs.toString(), quoted = false)
        append("  \"traceability\": {")
        append("\"appCommit\":\"").append(escape(result.traceability.appCommit)).append("\",")
        append("\"llamaCommit\":\"").append(escape(result.traceability.llamaCommit)).append("\",")
        append("\"modelSha256\":\"").append(escape(result.traceability.modelSha256)).append("\",")
        append("\"modelName\":\"").append(escape(result.traceability.modelName)).append("\",")
        append("\"appSourceSha256\":")
        appendNullableString(result.traceability.appSourceSha256)
        append(',')
        append("\"llamaSourceDiffSha256\":")
        appendNullableString(result.traceability.llamaSourceDiffSha256)
        append(',')
        append("\"nativeLibrarySha256\":")
        appendNullableString(result.traceability.nativeLibrarySha256)
        append(',')
        append("\"phasePolicyIdentitySha256\":")
        appendNullableString(result.traceability.phasePolicyIdentitySha256)
        append(',')
        append("\"deviceFingerprintSha256\":")
        appendNullableString(result.traceability.deviceFingerprintSha256)
        append(',')
        append("\"benchmarkBinarySha256\":")
        appendNullableString(result.traceability.benchmarkBinarySha256)
        append(',')
        append("\"sourceReportSha256\":")
        appendNullableString(result.traceability.sourceReportSha256)
        append(',')
        append("\"baselineDecodeThreads\":")
            .append(result.traceability.baselineDecodeThreads ?: "null").append(',')
        append("\"baselinePrefillThreads\":")
            .append(result.traceability.baselinePrefillThreads ?: "null").append(',')
        append("\"decodeThreads\":").append(result.traceability.decodeThreads ?: "null").append(',')
        append("\"prefillThreads\":").append(result.traceability.prefillThreads ?: "null").append("},\n")
        append("  \"config\": {")
        append("\"prompt\":\"").append(escape(result.config.prompt)).append("\",")
        append("\"repetitions\":").append(result.config.repetitions).append(',')
        append("\"maxTokens\":").append(result.config.generation.maxTokens).append(',')
        append("\"temperature\":").append(number(result.config.generation.temperature.toDouble()))
            .append(',')
        append("\"seed\":").append(result.config.generation.seed).append("},\n")
        append("  \"warmup\":")
        if (result.warmup == null) append("null") else appendRun(result.warmup)
        append(",\n")
        append("  \"correctnessMatched\":")
            .append(result.correctnessMatched ?: "null")
            .append(",\n")
        append("  \"runs\": [")
        result.runs.forEachIndexed { index, run ->
            if (index > 0) append(',')
            append("\n    ")
            appendRun(run)
        }
        if (result.runs.isNotEmpty()) append('\n').append("  ")
        append("]\n}")
    }

    private fun StringBuilder.appendRun(run: BenchmarkRun) {
        append("{\"mode\":\"").append(run.mode.name).append("\",")
        append("\"repetition\":").append(run.repetition).append(',')
        append("\"outputSha256\":\"").append(escape(run.outputSha256)).append("\",")
        append("\"metrics\":")
        appendMetrics(run.metrics)
        append('}')
    }

    private fun StringBuilder.appendMetrics(metrics: GenerationMetrics) {
        append("{\"promptTokens\":").append(metrics.promptTokens).append(',')
        append("\"generatedTokens\":").append(metrics.generatedTokens).append(',')
        append("\"timeToFirstTokenMs\":").append(number(metrics.timeToFirstTokenMs)).append(',')
        append("\"totalDurationMs\":").append(number(metrics.totalDurationMs)).append(',')
        append("\"decodeTokensPerSecond\":").append(number(metrics.decodeTokensPerSecond)).append(',')
        append("\"nativeTiming\":").append(metrics.nativeTiming).append(',')
        append("\"telemetry\":{\"before\":")
        appendTelemetry(metrics.telemetry.before)
        append(",\"after\":")
        appendTelemetry(metrics.telemetry.after)
        append("}}")
    }

    private fun StringBuilder.appendTelemetry(value: TelemetrySnapshot?) {
        if (value == null) {
            append("null")
            return
        }
        append("{\"timestampEpochMs\":").append(value.timestampEpochMs).append(',')
        append("\"thermalStatus\":").append(value.thermalStatus).append(',')
        append("\"thermalStatusName\":\"").append(escape(value.thermalStatusName)).append("\",")
        append("\"batteryPercent\":").append(value.batteryPercent?.let { number(it.toDouble()) } ?: "null")
            .append(',')
        append("\"batteryTemperatureC\":")
            .append(value.batteryTemperatureC?.let { number(it.toDouble()) } ?: "null").append(',')
        append("\"charging\":").append(value.charging ?: "null").append(',')
        append("\"availableMemoryBytes\":").append(value.availableMemoryBytes).append(',')
        append("\"totalMemoryBytes\":").append(value.totalMemoryBytes).append(',')
        append("\"lowMemory\":").append(value.lowMemory).append(',')
        append("\"deviceName\":\"").append(escape(value.deviceName)).append("\",")
        append("\"socName\":\"").append(escape(value.socName)).append("\",")
        append("\"cpuCoreCount\":").append(value.cpuCoreCount).append(',')
        append("\"supportedAbis\":[")
        value.supportedAbis.forEachIndexed { index, abi ->
            if (index > 0) append(',')
            append('"').append(escape(abi)).append('"')
        }
        append("],\"vulkanVersion\":")
        append(value.vulkanVersion?.let { "\"${escape(it)}\"" } ?: "null")
        append(",\"vulkanDetail\":")
        append(value.vulkanDetail?.let { "\"${escape(it)}\"" } ?: "null")
        append(",\"processResidentSetBytes\":").append(value.processResidentSetBytes ?: "null")
        append(",\"processPeakRssBytes\":").append(value.processPeakRssBytes ?: "null")
        append(",\"swapTotalBytes\":").append(value.swapTotalBytes ?: "null")
        append(",\"swapFreeBytes\":").append(value.swapFreeBytes ?: "null")
        append('}')
    }

    private fun StringBuilder.appendNullableString(value: String?) {
        if (value == null) append("null") else append('"').append(escape(value)).append('"')
    }

    private fun StringBuilder.field(name: String, value: String, quoted: Boolean = true) {
        append("  \"").append(name).append("\":")
        if (quoted) append('"').append(escape(value)).append('"') else append(value)
        append(",\n")
    }

    private fun number(value: Double): String =
        if (value.isFinite()) String.format(Locale.US, "%.4f", value) else "null"

    private fun escape(value: String): String = buildString(value.length) {
        value.forEach { char ->
            when (char) {
                '\\' -> append("\\\\")
                '"' -> append("\\\"")
                '\b' -> append("\\b")
                '\u000C' -> append("\\f")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (char.code < 0x20) append("\\u%04x".format(char.code)) else append(char)
            }
        }
    }
}
