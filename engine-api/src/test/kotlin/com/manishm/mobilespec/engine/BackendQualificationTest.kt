package com.manishm.mobilespec.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BackendQualificationTest {
    @Test
    fun `complete correct stable improvement qualifies`() {
        val evaluation = BackendQualificationEvaluator.evaluate(evidence())

        assertEquals(BackendQualificationVerdict.QUALIFIED, evaluation.verdict)
        assertTrue(evaluation.gates.all { it.status == QualificationGateStatus.PASS })
        assertTrue(requireNotNull(evaluation.endToEndImprovement) > 0.15)
    }

    @Test
    fun `missing required operation evidence is inconclusive`() {
        val original = evidence()
        val evaluation = BackendQualificationEvaluator.evaluate(
            original.copy(
                candidatePreflight = original.candidatePreflight.copy(
                    requiredOperationsPassed = null,
                ),
            ),
        )

        assertEquals(BackendQualificationVerdict.INCONCLUSIVE, evaluation.verdict)
        assertEquals(
            QualificationGateStatus.INCONCLUSIVE,
            evaluation.gates.single { it.name == "required-operations" }.status,
        )
    }

    @Test
    fun `any output mismatch rejects candidate`() {
        val original = evidence()
        val mismatched = original.pairs.toMutableList().also { pairs ->
            pairs[1] = pairs[1].copy(
                candidate = pairs[1].candidate.copy(outputSha256 = "f".repeat(64)),
            )
        }
        val evaluation = BackendQualificationEvaluator.evaluate(original.copy(pairs = mismatched))

        assertEquals(BackendQualificationVerdict.REJECTED, evaluation.verdict)
        assertEquals(
            QualificationGateStatus.FAIL,
            evaluation.gates.single { it.name == "scored-correctness" }.status,
        )
    }

    @Test
    fun `material additional swap drop rejects candidate`() {
        val original = evidence()
        val swapped = original.pairs.map { pair ->
            pair.copy(
                candidate = pair.candidate.copy(
                    metrics = pair.candidate.metrics.copy(
                        telemetry = telemetry(swapDropBytes = 96L * 1024 * 1024),
                    ),
                ),
            )
        }
        val evaluation = BackendQualificationEvaluator.evaluate(original.copy(pairs = swapped))

        assertEquals(BackendQualificationVerdict.REJECTED, evaluation.verdict)
        assertEquals(
            QualificationGateStatus.FAIL,
            evaluation.gates.single { it.name == "memory-swap" }.status,
        )
    }

    @Test
    fun `improvement must exceed combined run noise`() {
        val original = evidence()
        val noisyCandidateDurations = listOf(55.0, 130.0, 95.0)
        val noisyPairs = original.pairs.mapIndexed { index, pair ->
            pair.copy(
                candidate = pair.candidate.copy(
                    metrics = pair.candidate.metrics.copy(
                        totalDurationMs = noisyCandidateDurations[index],
                    ),
                ),
            )
        }
        val evaluation = BackendQualificationEvaluator.evaluate(original.copy(pairs = noisyPairs))

        assertEquals(BackendQualificationVerdict.REJECTED, evaluation.verdict)
        assertEquals(
            QualificationGateStatus.FAIL,
            evaluation.gates.single { it.name == "end-to-end-improvement" }.status,
        )
    }

    @Test
    fun `qualification JSON retains raw pairs gates and identities`() {
        val evidence = evidence()
        val report = BackendQualificationReport(
            id = "qualification-1",
            createdAtEpochMs = 123,
            traceability = BackendQualificationTraceability(
                appCommit = "app",
                appSourceSha256 = "source",
                llamaCommit = "llama",
                llamaSourceDiffSha256 = "diff",
                nativeLibrarySha256 = "native",
                modelSha256 = "model",
                modelName = "model.gguf",
                contextSize = 512,
                phasePolicyIdentitySha256 = null,
                deviceFingerprintSha256 = "device",
                deviceModel = "phone",
                socModel = "soc",
                vulkanDeviceIdentitySha256 = "vulkan",
                promptSha256 = "prompt",
                maxTokens = 16,
                temperature = 0f,
                seed = 42,
            ),
            records = listOf(
                BackendQualificationRecord(
                    evidence,
                    BackendQualificationEvaluator.evaluate(evidence),
                ),
            ),
        )

        val json = BackendQualificationJson.encode(report)

        assertTrue(json.contains("\"schemaVersion\":1"))
        assertTrue(json.contains("\"candidateFirst\":true"))
        assertTrue(json.contains("\"verdict\":\"QUALIFIED\""))
        assertTrue(json.contains("\"nativeLibrarySha256\":\"native\""))
        assertTrue(!json.contains("NaN"))
    }

    private fun evidence(): BackendQualificationEvidence {
        val hash = "a".repeat(64)
        val baselineDurations = listOf(100.0, 102.0, 98.0)
        val candidateDurations = listOf(80.0, 82.0, 79.0)
        return BackendQualificationEvidence(
            candidate = BackendCandidate(Backend.HYBRID, 8),
            baselinePreflight = preflight(hash, loadDurationMs = 100.0),
            candidatePreflight = preflight(hash, loadDurationMs = 101.0),
            discardedWarmup = measurement(Backend.HYBRID, 8, 84.0, hash),
            pairs = baselineDurations.indices.map { index ->
                QualificationPair(
                    repetition = index + 1,
                    candidateFirst = index % 2 == 1,
                    baseline = measurement(Backend.CPU, 0, baselineDurations[index], hash),
                    candidate = measurement(
                        Backend.HYBRID,
                        8,
                        candidateDurations[index],
                        hash,
                    ),
                )
            },
        )
    }

    private fun preflight(hash: String, loadDurationMs: Double) = QualificationPreflight(
        modelLoadSucceeded = true,
        loadDurationMs = loadDurationMs,
        loadTelemetry = telemetry(),
        nonEmptyGreedyOutput = true,
        greedyOutputSha256 = hash,
        requiredOperationsPassed = true,
        requiredOperationsDetail = "required Q4_0 shapes passed",
        cancellationPassed = true,
        reusePassed = true,
        deviceAvailableAfter = true,
    )

    private fun measurement(
        backend: Backend,
        gpuLayers: Int,
        durationMs: Double,
        hash: String,
    ) = QualificationMeasurement(
        backend = backend,
        gpuLayers = gpuLayers,
        outputSha256 = hash,
        metrics = GenerationMetrics(
            promptTokens = 8,
            generatedTokens = 16,
            timeToFirstTokenMs = 10.0,
            totalDurationMs = durationMs,
            decodeTokensPerSecond = 20.0,
            nativeTiming = true,
            telemetry = telemetry(),
        ),
    )

    private fun telemetry(swapDropBytes: Long = 0) = RunTelemetry(
        before = snapshot(swapFreeBytes = 2L * 1024 * 1024 * 1024),
        after = snapshot(
            thermalStatus = 1,
            swapFreeBytes = 2L * 1024 * 1024 * 1024 - swapDropBytes,
        ),
    )

    private fun snapshot(
        thermalStatus: Int = 0,
        swapFreeBytes: Long,
    ) = TelemetrySnapshot(
        timestampEpochMs = 1,
        thermalStatus = thermalStatus,
        thermalStatusName = if (thermalStatus == 0) "NONE" else "LIGHT",
        batteryPercent = 80f,
        batteryTemperatureC = 32f,
        charging = false,
        availableMemoryBytes = 2L * 1024 * 1024 * 1024,
        totalMemoryBytes = 6L * 1024 * 1024 * 1024,
        lowMemory = false,
        deviceName = "device",
        socName = "soc",
        cpuCoreCount = 8,
        supportedAbis = listOf("arm64-v8a"),
        vulkanVersion = "1.3.0",
        vulkanDetail = null,
        swapTotalBytes = 3L * 1024 * 1024 * 1024,
        swapFreeBytes = swapFreeBytes,
    )
}
