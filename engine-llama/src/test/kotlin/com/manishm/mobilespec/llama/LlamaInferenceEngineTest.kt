package com.manishm.mobilespec.llama

import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.BackendPolicy
import com.manishm.mobilespec.engine.BenchmarkConfig
import com.manishm.mobilespec.engine.BenchmarkEvent
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.ModelConfig
import com.manishm.mobilespec.engine.PhasePolicy
import com.manishm.mobilespec.engine.TokenEvent
import com.manishm.mobilespec.engine.VulkanDeviceInfo
import com.manishm.mobilespec.engine.runtimeDeviceFingerprintSha256
import java.security.MessageDigest
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

    @Test
    fun `auto without a qualified GPU policy fails closed to CPU`() = runBlocking {
        val bindings = GpuCapableBindings()
        val engine = gpuEngine(bindings)

        engine.load(ModelConfig("/model.gguf", "model", "sha", backend = Backend.AUTO))

        assertEquals(Backend.CPU, bindings.loaded?.backend)
        assertEquals(0, bindings.loaded?.gpuLayers)
        assertTrue(engine.capabilities().detail.orEmpty().contains("no qualified GPU policy"))
    }

    @Test
    fun `matching auto policy selects full Vulkan offload`() = runBlocking {
        val bindings = GpuCapableBindings()
        val engine = gpuEngine(bindings)
        val model = modelWithPolicy().copy(backend = Backend.AUTO)
        val policy = backendPolicy(model, Backend.VULKAN, -1)

        engine.load(model.copy(backendPolicy = policy))

        assertEquals(Backend.VULKAN, bindings.loaded?.backend)
        assertEquals(-1, bindings.loaded?.gpuLayers)
    }

    @Test
    fun `stale auto policy fails closed to CPU`() = runBlocking {
        val bindings = GpuCapableBindings()
        val engine = gpuEngine(bindings)
        val model = modelWithPolicy().copy(backend = Backend.AUTO)
        val stale = backendPolicy(model, Backend.VULKAN, -1).copy(modelSha256 = "other")

        engine.load(model.copy(backendPolicy = stale))

        assertEquals(Backend.CPU, bindings.loaded?.backend)
        assertEquals(null, bindings.loaded?.backendPolicy)
        assertTrue(engine.capabilities().detail.orEmpty().contains("model hash is stale"))
    }

    @Test
    fun `manual hybrid offload requires native capability and positive layers`() = runBlocking {
        val bindings = GpuCapableBindings()
        val engine = gpuEngine(bindings)

        engine.load(
            ModelConfig(
                "/model.gguf",
                "model",
                "sha",
                backend = Backend.HYBRID,
                gpuLayers = 8,
            ),
        )

        assertEquals(Backend.HYBRID, bindings.loaded?.backend)
        assertEquals(8, bindings.loaded?.gpuLayers)
    }

    @Test
    fun `GPU session creation failure retries safely on CPU`() = runBlocking {
        val bindings = GpuCapableBindings(failGpuSession = true)
        val engine = gpuEngine(bindings)

        engine.load(ModelConfig("/model.gguf", "model", "sha", backend = Backend.VULKAN))

        assertEquals(listOf(Backend.VULKAN, Backend.CPU), bindings.loadAttempts)
        assertEquals(Backend.CPU, bindings.loaded?.backend)
        assertTrue(engine.capabilities().detail.orEmpty().contains("session failed"))
    }

    @Test
    fun `invalid model layer count destroys the unowned native session`() = runBlocking {
        val bindings = InvalidLayerCountBindings()
        val engine = LlamaInferenceEngine(bindings = bindings)

        val result = runCatching {
            engine.load(ModelConfig("/model.gguf", "model", "sha"))
        }

        assertTrue(result.exceptionOrNull() is IllegalStateException)
        assertEquals(listOf(1L), bindings.destroyedHandles)
    }

    @Test
    fun `native capability parser exposes Vulkan only when device and offload exist`() {
        val capabilities = parseNativeCapabilities(
            """
            feature\tphase-aware
            feature\tkleidiai
            feature\tgpu-offload
            backend\tVulkan0\tTest GPU\tGPU\t10\t20
            vulkan\tTest GPU\t1\t2\t1.3.0\t3\t0.0.3\tINTEGRATED_GPU\ttrue\ttrue\tfalse\tfalse
            system\ttest system
            """.trimIndent().replace("\\t", "\t"),
        )

        assertTrue(capabilities.supportsKleidiAI)
        assertTrue(capabilities.supportsGpuOffload)
        assertTrue(Backend.VULKAN in capabilities.backends)
        assertTrue(Backend.HYBRID in capabilities.backends)
        assertEquals("Test GPU", capabilities.vulkanDevices.single().name)
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

    private fun gpuEngine(bindings: NativeBindings) = LlamaInferenceEngine(
        bindings = bindings,
        llamaCommit = "178a6c4",
        nativeLibrarySha256 = "e".repeat(64),
        runtimeDeviceFingerprintSha256 = TEST_DEVICE_FINGERPRINT,
        runtimeDeviceModel = "24094RAD4I",
        runtimeSocModel = "MT6855",
    )

    private fun backendPolicy(model: ModelConfig, backend: Backend, gpuLayers: Int) = BackendPolicy(
        backend = backend,
        gpuLayers = gpuLayers,
        profileIdentitySha256 = "a".repeat(64),
        deviceFingerprintSha256 = TEST_DEVICE_FINGERPRINT,
        vulkanDeviceIdentitySha256 = TEST_VULKAN.identityMaterial().sha256(),
        nativeLibrarySha256 = "e".repeat(64),
        sourceReportSha256 = "c".repeat(64),
        cpuPhasePolicyIdentitySha256 = requireNotNull(model.phasePolicy).profileIdentitySha256,
        deviceModel = "24094RAD4I",
        socModel = "MT6855",
        modelSha256 = model.sha256,
        llamaCommit = "178a6c4",
        contextSize = model.contextSize,
        qualificationPromptSha256 = "f".repeat(64),
        qualificationMaxTokens = 64,
        qualificationTemperature = 0f,
        qualificationSeed = 42,
        workloadClass = "interactive-chat-decode-weighted",
        scoringPolicy = "e2e-3pct-correctness-memory-thermal-stability",
    )

    private object MissingBindings : NativeBindings {
        override val available = false
        override val unavailableReason = "not packaged"
        override fun capabilities(): EngineCapabilities = unavailableCapabilities(unavailableReason)
        override fun createSession(config: ModelConfig) = error("must not be called")
        override fun modelLayerCount(handle: Long) = error("must not be called")
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
        override fun modelLayerCount(handle: Long) = 16
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
        override fun modelLayerCount(handle: Long) = 16
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

    private class GpuCapableBindings(
        private val failGpuSession: Boolean = false,
    ) : NativeBindings {
        var loaded: ModelConfig? = null
        val loadAttempts = mutableListOf<Backend>()
        override val available = true
        override val unavailableReason: String? = null
        override fun capabilities() = EngineCapabilities(
            nativeAvailable = true,
            backends = setOf(Backend.CPU, Backend.AUTO, Backend.VULKAN, Backend.HYBRID),
            supportsSpeculativeDecoding = false,
            supportsCancellation = true,
            timingSource = "test",
            supportsPhaseAwareThreadPolicy = true,
            supportsGpuOffload = true,
            supportsHybridOffload = true,
            vulkanDevices = listOf(TEST_VULKAN),
        )
        override fun createSession(config: ModelConfig): Long {
            loadAttempts += config.backend
            if (failGpuSession && config.backend != Backend.CPU) {
                error("simulated GPU load failure")
            }
            loaded = config
            return 1L
        }
        override fun modelLayerCount(handle: Long) = 16
        override fun generate(
            handle: Long,
            prompt: String,
            config: GenerationConfig,
            callback: NativeTokenCallback,
        ) = longArrayOf(1, 1, 1, 2, 1)
        override fun cancel(handle: Long) = Unit
        override fun destroySession(handle: Long) = Unit
    }

    private class InvalidLayerCountBindings : NativeBindings {
        val destroyedHandles = mutableListOf<Long>()
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
        override fun modelLayerCount(handle: Long) = 0
        override fun generate(
            handle: Long,
            prompt: String,
            config: GenerationConfig,
            callback: NativeTokenCallback,
        ) = error("must not be called")
        override fun cancel(handle: Long) = Unit
        override fun destroySession(handle: Long) {
            destroyedHandles += handle
        }
    }

    private companion object {
        val TEST_DEVICE_FINGERPRINT = runtimeDeviceFingerprintSha256("24094RAD4I", "MT6855", 8)
        val TEST_VULKAN = VulkanDeviceInfo(
            name = "Test GPU",
            vendorId = 1,
            deviceId = 2,
            apiVersion = "1.3.0",
            driverVersionRaw = 3,
            driverVersion = "0.0.3",
            deviceType = "INTEGRATED_GPU",
            fp16 = true,
            integerDotProduct = true,
            cooperativeMatrix = false,
            cooperativeMatrix2 = false,
        )
    }
}

private fun String.sha256(): String = MessageDigest.getInstance("SHA-256")
    .digest(toByteArray())
    .joinToString("") { "%02x".format(it) }
