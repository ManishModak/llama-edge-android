package com.manishm.mobilespec.llama

import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.BenchmarkConfig
import com.manishm.mobilespec.engine.BenchmarkEvent
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.ModelConfig
import com.manishm.mobilespec.engine.PhasePolicy
import com.manishm.mobilespec.engine.TokenEvent
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LlamaInferenceEngineTest {
    @Test
    fun `missing native library fails safely`() = runBlocking {
        val engine = LlamaInferenceEngine(bindings = MissingBindings)

        val events = engine.generate("hello").toList()

        assertEquals(1, events.size)
        assertTrue(events.single() is TokenEvent.Failed)
        assertTrue(!engine.capabilities().nativeAvailable)
    }

    @Test
    fun `optimized benchmark is rejected when native build does not support it`() = runBlocking {
        val bindings = BaselineOnlyBindings()
        val engine = phaseAwareEngine(bindings)
        engine.load(ModelConfig("/model.gguf", "model", "sha"))

        val events = engine.benchmark(BenchmarkConfig("hello", repetitions = 1)).toList()

        assertEquals(1, events.size)
        assertTrue(events.single() is BenchmarkEvent.Failed)
        assertEquals(0, bindings.generateCalls)
    }

    @Test
    fun `verified phase policy enables optimized A B and reaches native bindings`() = runBlocking {
        val bindings = PhaseCapableBindings()
        val engine = phaseAwareEngine(bindings)
        engine.load(modelWithPolicy())

        val events = engine.benchmark(BenchmarkConfig("hello", repetitions = 1)).toList()

        assertTrue(engine.capabilities().supportsPhaseAwareThreadPolicy)
        assertEquals(3, bindings.generateCalls) // discarded baseline warm-up + two scored modes
        assertEquals(6, bindings.loaded?.phasePolicy?.decodeThreads)
        assertEquals(8, bindings.loaded?.phasePolicy?.prefillThreads)
        assertEquals(8, bindings.loaded?.phasePolicy?.baselineDecodeThreads)
        assertEquals(8, bindings.loaded?.phasePolicy?.baselinePrefillThreads)
        val finished = events.last() as BenchmarkEvent.Finished
        assertTrue(finished.result.warmup != null)
        assertTrue(finished.result.correctnessMatched == true)
    }

    @Test
    fun `benchmark order is counterbalanced and output mismatches fail correctness`() = runBlocking {
        val bindings = PhaseCapableBindings(optimizedOutputDifferent = true)
        val engine = phaseAwareEngine(bindings)
        engine.load(modelWithPolicy())

        val events = engine.benchmark(BenchmarkConfig("hello", repetitions = 2)).toList()
        val result = (events.last() as BenchmarkEvent.Finished).result

        assertEquals(
            listOf(
                com.manishm.mobilespec.engine.InferenceMode.BASELINE, // warm-up
                com.manishm.mobilespec.engine.InferenceMode.BASELINE,
                com.manishm.mobilespec.engine.InferenceMode.OPTIMIZED,
                com.manishm.mobilespec.engine.InferenceMode.OPTIMIZED,
                com.manishm.mobilespec.engine.InferenceMode.BASELINE,
            ),
            bindings.modes,
        )
        assertTrue(result.correctnessMatched == false)
    }

    @Test
    fun `stale phase policy fails closed to stock defaults`() = runBlocking {
        val bindings = PhaseCapableBindings()
        val engine = phaseAwareEngine(bindings)
        val stale = modelWithPolicy().copy(
            phasePolicy = modelWithPolicy().phasePolicy?.copy(modelSha256 = "other"),
        )
        engine.load(stale)

        val events = engine.benchmark(BenchmarkConfig("hello", repetitions = 1)).toList()

        assertTrue(!engine.capabilities().supportsPhaseAwareThreadPolicy)
        assertEquals(null, bindings.loaded?.phasePolicy)
        assertEquals(0, bindings.generateCalls)
        assertTrue(events.single() is BenchmarkEvent.Failed)
        assertTrue(engine.capabilities().detail.orEmpty().contains("model hash is stale"))
    }

    @Test
    fun `phase policy for another device fails closed`() = runBlocking {
        val bindings = PhaseCapableBindings()
        val engine = LlamaInferenceEngine(
            bindings = bindings,
            runtimeDeviceModel = "another-phone",
            runtimeSocModel = "MT6855",
        )
        engine.load(modelWithPolicy())

        assertTrue(!engine.capabilities().supportsPhaseAwareThreadPolicy)
        assertEquals(null, bindings.loaded?.phasePolicy)
        assertTrue(engine.capabilities().detail.orEmpty().contains("device model is stale"))
    }

    private fun modelWithPolicy() = ModelConfig(
        path = "/model.gguf",
        displayName = "model",
        sha256 = "sha",
        phasePolicy = PhasePolicy(
            baselineDecodeThreads = 8,
            baselinePrefillThreads = 8,
            decodeThreads = 6,
            prefillThreads = 8,
            profileIdentitySha256 = "a".repeat(64),
            deviceFingerprintSha256 = "b".repeat(64),
            deviceModel = "24094RAD4I",
            socModel = "MT6855",
            benchmarkBinarySha256 = "c".repeat(64),
            sourceReportSha256 = "d".repeat(64),
            modelSha256 = "sha",
            llamaCommit = "178a6c4",
            contextSize = 512,
            workloadClass = "interactive-chat-decode-weighted",
        ),
    )

    private fun phaseAwareEngine(bindings: NativeBindings) = LlamaInferenceEngine(
        bindings = bindings,
        runtimeDeviceModel = "24094RAD4I",
        runtimeSocModel = "MT6855",
    )

    private object MissingBindings : NativeBindings {
        override val available = false
        override val unavailableReason = "not packaged"
        override fun capabilities(): EngineCapabilities = unavailableCapabilities(unavailableReason)
        override fun createSession(config: ModelConfig) = error("must not be called")
        override fun generate(
            handle: Long,
            prompt: String,
            config: GenerationConfig,
            callback: NativeTokenCallback,
        ) = error("must not be called")
        override fun cancel(handle: Long) = Unit
        override fun destroySession(handle: Long) = Unit
    }

    private class BaselineOnlyBindings : NativeBindings {
        var generateCalls = 0
        override val available = true
        override val unavailableReason: String? = null
        override fun capabilities() = EngineCapabilities(
            nativeAvailable = true,
            backends = setOf(Backend.CPU),
            supportsSpeculativeDecoding = false,
            supportsCancellation = true,
            timingSource = "test",
        )
        override fun createSession(config: ModelConfig) = 1L
        override fun generate(
            handle: Long,
            prompt: String,
            config: GenerationConfig,
            callback: NativeTokenCallback,
        ): LongArray {
            generateCalls++
            return longArrayOf(1, 1, 1, 2, 1)
        }
        override fun cancel(handle: Long) = Unit
        override fun destroySession(handle: Long) = Unit
    }

    private class PhaseCapableBindings(
        private val optimizedOutputDifferent: Boolean = false,
    ) : NativeBindings {
        var generateCalls = 0
        var loaded: ModelConfig? = null
        val modes = mutableListOf<com.manishm.mobilespec.engine.InferenceMode>()
        override val available = true
        override val unavailableReason: String? = null
        override fun capabilities() = EngineCapabilities(
            nativeAvailable = true,
            backends = setOf(Backend.CPU),
            supportsSpeculativeDecoding = false,
            supportsCancellation = true,
            timingSource = "test",
            supportsPhaseAwareThreadPolicy = true,
        )
        override fun createSession(config: ModelConfig): Long {
            loaded = config
            return 1L
        }
        override fun generate(
            handle: Long,
            prompt: String,
            config: GenerationConfig,
            callback: NativeTokenCallback,
        ): LongArray {
            generateCalls++
            modes += config.mode
            callback.onToken(
                if (optimizedOutputDifferent &&
                    config.mode == com.manishm.mobilespec.engine.InferenceMode.OPTIMIZED
                ) "different" else "same",
                0,
                1,
            )
            return longArrayOf(1, 1, 1, 2, 1)
        }
        override fun cancel(handle: Long) = Unit
        override fun destroySession(handle: Long) = Unit
    }
}
