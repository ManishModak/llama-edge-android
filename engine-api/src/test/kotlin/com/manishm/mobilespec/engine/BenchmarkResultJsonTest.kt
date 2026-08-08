package com.manishm.mobilespec.engine

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BenchmarkResultJsonTest {
    @Test
    fun `export is traceable and escapes prompts`() {
        val result = BenchmarkResult(
            id = "run-1",
            createdAtEpochMs = 123,
            config = BenchmarkConfig(prompt = "say \"hi\"\nnow", repetitions = 1),
            traceability = Traceability(
                "app-sha",
                "llama-sha",
                "model-sha",
                "model.gguf",
                appSourceSha256 = "app-source-sha",
                llamaSourceDiffSha256 = "llama-diff-sha",
                nativeLibrarySha256 = "native-library-sha",
                phasePolicyIdentitySha256 = "policy-sha",
                deviceFingerprintSha256 = "device-sha",
                benchmarkBinarySha256 = "binary-sha",
                sourceReportSha256 = "report-sha",
                baselineDecodeThreads = 8,
                baselinePrefillThreads = 8,
                decodeThreads = 6,
                prefillThreads = 8,
                executionBackend = Backend.HYBRID,
                gpuLayers = 12,
                backendPolicyIdentitySha256 = "backend-policy-sha",
                vulkanDeviceIdentitySha256 = "vulkan-device-sha",
            ),
            runs = emptyList(),
        )

        val json = BenchmarkResultJson.encode(result)

        assertTrue(json.contains("\"schemaVersion\":1"))
        assertTrue(json.contains("say \\\"hi\\\"\\nnow"))
        assertTrue(json.contains("\"llamaCommit\":\"llama-sha\""))
        assertTrue(json.contains("\"appSourceSha256\":\"app-source-sha\""))
        assertTrue(json.contains("\"llamaSourceDiffSha256\":\"llama-diff-sha\""))
        assertTrue(json.contains("\"nativeLibrarySha256\":\"native-library-sha\""))
        assertTrue(json.contains("\"phasePolicyIdentitySha256\":\"policy-sha\""))
        assertTrue(json.contains("\"deviceFingerprintSha256\":\"device-sha\""))
        assertTrue(json.contains("\"benchmarkBinarySha256\":\"binary-sha\""))
        assertTrue(json.contains("\"sourceReportSha256\":\"report-sha\""))
        assertTrue(json.contains("\"baselineDecodeThreads\":8"))
        assertTrue(json.contains("\"baselinePrefillThreads\":8"))
        assertTrue(json.contains("\"decodeThreads\":6"))
        assertTrue(json.contains("\"prefillThreads\":8"))
        assertTrue(json.contains("\"executionBackend\":\"HYBRID\""))
        assertTrue(json.contains("\"gpuLayers\":12"))
        assertTrue(json.contains("\"backendPolicyIdentitySha256\":\"backend-policy-sha\""))
        assertTrue(json.contains("\"vulkanDeviceIdentitySha256\":\"vulkan-device-sha\""))
        assertFalse(json.contains("NaN"))
    }

    @Test
    fun `empty summary has finite zero values`() {
        val result = BenchmarkResult(
            id = "empty",
            createdAtEpochMs = 0,
            config = BenchmarkConfig("prompt"),
            traceability = Traceability("", "", "", ""),
            runs = emptyList(),
        )

        val summary = result.summary(InferenceMode.OPTIMIZED)

        assertTrue(summary.meanTokensPerSecond == 0.0)
        assertTrue(summary.meanTimeToFirstTokenMs == 0.0)
    }

    @Test
    fun `telemetry export includes process peak rss and swap counters`() {
        val telemetry = TelemetrySnapshot(
            timestampEpochMs = 1,
            thermalStatus = 2,
            thermalStatusName = "MODERATE",
            batteryPercent = 90f,
            batteryTemperatureC = 36f,
            charging = true,
            availableMemoryBytes = 10,
            totalMemoryBytes = 20,
            lowMemory = false,
            deviceName = "device",
            socName = "soc",
            cpuCoreCount = 8,
            supportedAbis = listOf("arm64-v8a"),
            vulkanVersion = null,
            vulkanDetail = null,
            processResidentSetBytes = 30,
            processPeakRssBytes = 40,
            swapTotalBytes = 50,
            swapFreeBytes = 60,
        )
        val run = BenchmarkRun(
            mode = InferenceMode.BASELINE,
            repetition = 1,
            metrics = GenerationMetrics(
                promptTokens = 1,
                generatedTokens = 1,
                timeToFirstTokenMs = 1.0,
                totalDurationMs = 2.0,
                decodeTokensPerSecond = 1.0,
                nativeTiming = true,
                telemetry = RunTelemetry(telemetry, telemetry),
            ),
            outputSha256 = "output",
        )
        val json = BenchmarkResultJson.encode(
            BenchmarkResult(
                id = "telemetry",
                createdAtEpochMs = 1,
                config = BenchmarkConfig("prompt", repetitions = 1),
                traceability = Traceability("app", "llama", "model", "model.gguf"),
                runs = listOf(run),
            ),
        )

        assertTrue(json.contains("\"processResidentSetBytes\":30"))
        assertTrue(json.contains("\"processPeakRssBytes\":40"))
        assertTrue(json.contains("\"swapTotalBytes\":50"))
        assertTrue(json.contains("\"swapFreeBytes\":60"))
    }

    @Test
    fun `GPU layer candidates are bounded and deduplicated`() {
        assertTrue(gpuLayerCandidates(1) == listOf(0, -1))
        assertTrue(gpuLayerCandidates(4) == listOf(0, 1, 2, 3, -1))
        assertTrue(gpuLayerCandidates(32) == listOf(0, 8, 16, 24, -1))
    }
}
