package com.manishm.mobilespec

import android.app.Application
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.manishm.mobilespec.engine.BenchmarkConfig
import com.manishm.mobilespec.engine.BenchmarkEvent
import com.manishm.mobilespec.engine.BenchmarkResult
import com.manishm.mobilespec.engine.BenchmarkResultJson
import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.BackendCandidate
import com.manishm.mobilespec.engine.BackendQualificationEvaluator
import com.manishm.mobilespec.engine.BackendQualificationEvidence
import com.manishm.mobilespec.engine.BackendQualificationJson
import com.manishm.mobilespec.engine.BackendQualificationRecord
import com.manishm.mobilespec.engine.BackendQualificationReport
import com.manishm.mobilespec.engine.BackendQualificationTraceability
import com.manishm.mobilespec.engine.BackendQualificationVerdict
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.EngineState
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.GenerationMetrics
import com.manishm.mobilespec.engine.InferenceEngine
import com.manishm.mobilespec.engine.InferenceMode
import com.manishm.mobilespec.engine.ModelConfig
import com.manishm.mobilespec.engine.QualificationMeasurement
import com.manishm.mobilespec.engine.QualificationPair
import com.manishm.mobilespec.engine.QualificationPreflight
import com.manishm.mobilespec.engine.RunTelemetry
import com.manishm.mobilespec.engine.TokenEvent
import com.manishm.mobilespec.engine.gpuLayerCandidates
import com.manishm.mobilespec.engine.runtimeDeviceFingerprintSha256
import com.manishm.mobilespec.engine.vulkanDeviceIdentitySha256
import com.manishm.mobilespec.llama.LlamaInferenceEngine
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipFile
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

data class BenchmarkUiState(
    val running: Boolean = false,
    val completedRuns: Int = 0,
    val totalRuns: Int = 0,
    val currentLabel: String? = null,
    val result: BenchmarkResult? = null,
    val error: String? = null,
)

data class ChatUiState(
    val prompt: String = "Explain why on-device inference matters in two sentences.",
    val response: String = "",
    val mode: InferenceMode = InferenceMode.BASELINE,
    val running: Boolean = false,
    val metrics: GenerationMetrics? = null,
    val error: String? = null,
)

data class ModelsUiState(
    val models: List<ImportedModel> = emptyList(),
    val selected: ImportedModel? = null,
    val importing: Boolean = false,
    val backend: Backend = Backend.AUTO,
    val gpuLayers: Int = 1,
    val status: String? = null,
    val error: String? = null,
)

data class QualificationUiState(
    val running: Boolean = false,
    val completedCandidates: Int = 0,
    val totalCandidates: Int = 0,
    val currentLabel: String? = null,
    val report: BackendQualificationReport? = null,
    val error: String? = null,
)

data class QualificationBuildIdentity(
    val appCommit: String = "unknown",
    val appSourceSha256: String? = null,
    val llamaCommit: String = "unknown",
    val llamaSourceDiffSha256: String? = null,
    val nativeLibrarySha256: String? = null,
    val deviceFingerprintSha256: String? = null,
    val deviceModel: String = "unknown",
    val socModel: String = "unknown",
)

private data class GeneratedOutput(
    val text: String,
    val metrics: GenerationMetrics,
)

class MobileSpecViewModel(
    application: Application,
    private val engine: InferenceEngine,
    val telemetryMonitor: DeviceTelemetryMonitor,
    private val qualificationBuildIdentity: QualificationBuildIdentity = QualificationBuildIdentity(),
) : AndroidViewModel(application) {
    private val importer = ModelImporter(application)
    private val mutableBenchmark = MutableStateFlow(BenchmarkUiState())
    private val mutableChat = MutableStateFlow(ChatUiState())
    private val mutableModels = MutableStateFlow(ModelsUiState())
    private val mutableCapabilities = MutableStateFlow(engine.capabilities())
    private val mutableQualification = MutableStateFlow(QualificationUiState())
    private var benchmarkJob: Job? = null
    private var generationJob: Job? = null
    private var qualificationJob: Job? = null

    val benchmark: StateFlow<BenchmarkUiState> = mutableBenchmark.asStateFlow()
    val chat: StateFlow<ChatUiState> = mutableChat.asStateFlow()
    val models: StateFlow<ModelsUiState> = mutableModels.asStateFlow()
    val capabilities: StateFlow<EngineCapabilities> = mutableCapabilities.asStateFlow()
    val qualification: StateFlow<QualificationUiState> = mutableQualification.asStateFlow()
    val engineState = engine.state

    fun importModel(uri: Uri) {
        if (mutableModels.value.importing) return
        viewModelScope.launch {
            mutableModels.update { it.copy(importing = true, status = "Copying and hashing model…", error = null) }
            runCatching { importer.import(uri) }
                .onSuccess { imported ->
                    mutableModels.update {
                        it.copy(
                            models = it.models + imported,
                            selected = imported,
                            importing = false,
                            status = "Imported ${imported.displayName}",
                        )
                    }
                    loadModel(imported)
                }
                .onFailure { error ->
                    mutableModels.update {
                        it.copy(importing = false, status = null, error = error.message ?: "Import failed")
                    }
                }
        }
    }

    fun selectModel(model: ImportedModel) {
        mutableModels.update { it.copy(selected = model, error = null) }
        viewModelScope.launch { loadModel(model) }
    }

    fun setBackend(backend: Backend) {
        mutableModels.update { it.copy(backend = backend, error = null) }
        mutableModels.value.selected?.let { selected ->
            viewModelScope.launch { loadModel(selected) }
        }
    }

    fun setGpuLayers(value: Int) {
        mutableModels.update { it.copy(gpuLayers = value.coerceAtLeast(1), error = null) }
        if (mutableModels.value.backend == Backend.HYBRID) {
            mutableModels.value.selected?.let { selected ->
                viewModelScope.launch { loadModel(selected) }
            }
        }
    }

    fun runBenchmark(prompt: String) {
        if (mutableBenchmark.value.running) return
        benchmarkJob = viewModelScope.launch {
            mutableBenchmark.value = BenchmarkUiState(running = true)
            try {
                engine.benchmark(
                    BenchmarkConfig(
                        prompt = prompt,
                        repetitions = 5,
                        generation = GenerationConfig(maxTokens = 128, temperature = 0f, seed = 42),
                    ),
                ).collect { event ->
                    when (event) {
                        is BenchmarkEvent.Started -> mutableBenchmark.update {
                            it.copy(totalRuns = event.totalRuns)
                        }
                        is BenchmarkEvent.RunStarted -> mutableBenchmark.update {
                            it.copy(currentLabel = "${event.mode.name.lowercase()} ${event.repetition}")
                        }
                        is BenchmarkEvent.RunCompleted -> mutableBenchmark.update {
                            it.copy(
                                completedRuns = event.completedRuns,
                                totalRuns = event.totalRuns,
                            )
                        }
                        is BenchmarkEvent.Finished -> mutableBenchmark.update {
                            it.copy(running = false, currentLabel = null, result = event.result)
                        }
                        is BenchmarkEvent.Failed -> mutableBenchmark.update {
                            it.copy(running = false, currentLabel = null, error = event.message)
                        }
                    }
                }
            } finally {
                mutableBenchmark.update {
                    if (it.running) {
                        it.copy(running = false, currentLabel = null, error = "Cancelled")
                    } else {
                        it
                    }
                }
            }
        }
    }

    fun benchmarkJson(): String? = mutableBenchmark.value.result?.let(BenchmarkResultJson::encode)

    fun runBackendQualification() {
        if (mutableQualification.value.running) return
        val model = mutableModels.value.selected ?: run {
            mutableQualification.update { it.copy(error = "Import and load a model first") }
            return
        }
        qualificationJob = viewModelScope.launch {
            mutableQualification.value = QualificationUiState(
                running = true,
                currentLabel = "CPU preflight",
            )
            try {
                val report = qualifyBackends(model)
                mutableQualification.update {
                    it.copy(
                        running = false,
                        currentLabel = null,
                        report = report,
                        error = null,
                    )
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                mutableQualification.update {
                    it.copy(
                        running = false,
                        currentLabel = null,
                        error = error.message ?: "Backend qualification failed",
                    )
                }
            }
            loadModel(model)
        }
    }

    fun qualificationJson(): String? =
        mutableQualification.value.report?.let(BackendQualificationJson::encode)

    fun updatePrompt(value: String) {
        mutableChat.update { it.copy(prompt = value) }
    }

    fun setMode(mode: InferenceMode) {
        mutableChat.update { it.copy(mode = mode) }
    }

    fun generate() {
        val current = mutableChat.value
        if (current.running || current.prompt.isBlank()) return
        mutableChat.update { it.copy(response = "", running = true, metrics = null, error = null) }
        generationJob = viewModelScope.launch {
            engine.generate(
                current.prompt,
                GenerationConfig(maxTokens = 128, mode = current.mode),
            ).collect { event ->
                when (event) {
                    is TokenEvent.Started -> Unit
                    is TokenEvent.Token -> mutableChat.update { it.copy(response = it.response + event.text) }
                    is TokenEvent.Completed -> mutableChat.update {
                        it.copy(running = false, metrics = event.metrics)
                    }
                    is TokenEvent.Failed -> mutableChat.update {
                        it.copy(running = false, error = event.message)
                    }
                }
            }
        }
    }

    fun cancel() {
        engine.cancel()
        benchmarkJob?.cancel()
        generationJob?.cancel()
        qualificationJob?.cancel()
        mutableBenchmark.update {
            if (it.running) it.copy(running = false, currentLabel = null, error = "Cancelled") else it
        }
        mutableChat.update { it.copy(running = false) }
        mutableQualification.update {
            if (it.running) it.copy(running = false, currentLabel = null, error = "Cancelled") else it
        }
    }

    private suspend fun qualifyBackends(model: ImportedModel): BackendQualificationReport {
        val prompt = QUALIFICATION_PROMPT
        val generation = GenerationConfig(
            maxTokens = QUALIFICATION_MAX_TOKENS,
            temperature = 0f,
            seed = QUALIFICATION_SEED,
        )
        val layerCount = (engine.state.value as? EngineState.Ready)
            ?.model
            ?.modelLayerCount
            ?: error("Native model layer count is unavailable")
        val available = engine.capabilities()
        val candidates = gpuLayerCandidates(layerCount).mapNotNull { layers ->
            when {
                layers == -1 && Backend.VULKAN in available.backends ->
                    BackendCandidate(Backend.VULKAN, -1)
                layers > 0 && Backend.HYBRID in available.backends ->
                    BackendCandidate(Backend.HYBRID, layers)
                else -> null
            }
        }
        check(candidates.isNotEmpty()) { "No Vulkan or hybrid candidates are available" }
        mutableQualification.update {
            it.copy(totalCandidates = candidates.size, currentLabel = "CPU load")
        }
        val baselineConfig = qualificationModelConfig(model, Backend.CPU, gpuLayers = 0)
        val baselinePreflight = runPreflight(
            baselineConfig,
            label = "CPU",
            requiredOperationsPassed = true,
            requiredOperationsDetail = "CPU reference backend",
        )
        check(baselinePreflight.modelLoadSucceeded) {
            baselinePreflight.failure ?: "CPU preflight failed"
        }

        val records = mutableListOf<BackendQualificationRecord>()
        candidates.forEachIndexed { index, candidate ->
            mutableQualification.update {
                it.copy(currentLabel = "${candidate.label()} preflight")
            }
            val operationEvidence = requiredOperationEvidence(available)
            val candidateConfig = qualificationModelConfig(
                model,
                candidate.backend,
                candidate.gpuLayers,
            )
            val candidatePreflight = if (operationEvidence.first == false) {
                skippedOperationPreflight(operationEvidence.second)
            } else {
                runPreflight(
                    candidateConfig,
                    label = candidate.label(),
                    requiredOperationsPassed = operationEvidence.first,
                    requiredOperationsDetail = operationEvidence.second,
                )
            }
            val preflightEvidence = BackendQualificationEvidence(
                candidate = candidate,
                baselinePreflight = baselinePreflight,
                candidatePreflight = candidatePreflight,
                discardedWarmup = null,
                pairs = emptyList(),
            )
            val preflightEvaluation = BackendQualificationEvaluator.evaluate(preflightEvidence)
            val record = if (
                preflightEvaluation.verdict == BackendQualificationVerdict.QUALIFIED
            ) {
                error("Preflight-only evidence cannot be qualified")
            } else if (
                preflightEvaluation.verdict != BackendQualificationVerdict.REJECTED &&
                candidatePreflight.requiredOperationsPassed == true
            ) {
                mutableQualification.update {
                    it.copy(currentLabel = "${candidate.label()} discarded warm-up")
                }
                loadExact(candidateConfig)
                val warmup = generateMeasurement(candidate, generation)
                val pairs = (1..QUALIFICATION_REPETITIONS).map { repetition ->
                    val candidateFirst = repetition % 2 == 0
                    mutableQualification.update {
                        it.copy(
                            currentLabel =
                                "${candidate.label()} scored pair $repetition/$QUALIFICATION_REPETITIONS",
                        )
                    }
                    if (candidateFirst) {
                        val candidateRun = loadAndMeasure(candidateConfig, candidate, generation)
                        val baselineRun = loadAndMeasure(
                            baselineConfig,
                            Backend.CPU,
                            0,
                            generation,
                        )
                        QualificationPair(repetition, true, baselineRun, candidateRun)
                    } else {
                        val baselineRun = loadAndMeasure(
                            baselineConfig,
                            Backend.CPU,
                            0,
                            generation,
                        )
                        val candidateRun = loadAndMeasure(candidateConfig, candidate, generation)
                        QualificationPair(repetition, false, baselineRun, candidateRun)
                    }
                }
                val evidence = preflightEvidence.copy(
                    discardedWarmup = warmup,
                    pairs = pairs,
                )
                BackendQualificationRecord(
                    evidence,
                    BackendQualificationEvaluator.evaluate(evidence),
                )
            } else {
                BackendQualificationRecord(preflightEvidence, preflightEvaluation)
            }
            records += record
            mutableQualification.update { it.copy(completedCandidates = index + 1) }
        }

        val capabilities = engine.capabilities()
        return BackendQualificationReport(
            id = UUID.randomUUID().toString(),
            createdAtEpochMs = System.currentTimeMillis(),
            traceability = BackendQualificationTraceability(
                appCommit = qualificationBuildIdentity.appCommit,
                appSourceSha256 = qualificationBuildIdentity.appSourceSha256,
                llamaCommit = qualificationBuildIdentity.llamaCommit,
                llamaSourceDiffSha256 = qualificationBuildIdentity.llamaSourceDiffSha256,
                nativeLibrarySha256 = qualificationBuildIdentity.nativeLibrarySha256,
                modelSha256 = model.sha256,
                modelName = model.displayName,
                contextSize = baselineConfig.contextSize,
                phasePolicyIdentitySha256 = baselineConfig.phasePolicy?.profileIdentitySha256,
                deviceFingerprintSha256 = qualificationBuildIdentity.deviceFingerprintSha256,
                deviceModel = qualificationBuildIdentity.deviceModel,
                socModel = qualificationBuildIdentity.socModel,
                vulkanDeviceIdentitySha256 =
                    vulkanDeviceIdentitySha256(capabilities.vulkanDevices),
                promptSha256 = prompt.sha256Text(),
                maxTokens = generation.maxTokens,
                temperature = generation.temperature,
                seed = generation.seed,
            ),
            records = records,
        )
    }

    private suspend fun runPreflight(
        config: ModelConfig,
        label: String,
        requiredOperationsPassed: Boolean?,
        requiredOperationsDetail: String,
    ): QualificationPreflight {
        val before = telemetryMonitor.snapshotSafely()
        val started = SystemClock.elapsedRealtimeNanos()
        val loadResult = runCatching {
            boundedQualificationStage("$label load") { engine.load(config) }
        }
        val loadDurationMs = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000.0
        val after = telemetryMonitor.snapshotSafely()
        if (loadResult.isFailure) {
            return QualificationPreflight(
                modelLoadSucceeded = false,
                loadDurationMs = loadDurationMs,
                loadTelemetry = RunTelemetry(before, after),
                nonEmptyGreedyOutput = false,
                greedyOutputSha256 = null,
                requiredOperationsPassed = requiredOperationsPassed,
                requiredOperationsDetail = requiredOperationsDetail,
                cancellationPassed = false,
                reusePassed = false,
                deviceAvailableAfter = false,
                failure = loadResult.exceptionOrNull()?.message ?: "model load failed",
            )
        }
        val active = (engine.state.value as? EngineState.Ready)?.model
        val exactBackend = active?.backend == config.backend && active.gpuLayers == config.gpuLayers
        if (!exactBackend) {
            return QualificationPreflight(
                modelLoadSucceeded = false,
                loadDurationMs = loadDurationMs,
                loadTelemetry = RunTelemetry(before, after),
                nonEmptyGreedyOutput = false,
                greedyOutputSha256 = null,
                requiredOperationsPassed = requiredOperationsPassed,
                requiredOperationsDetail = requiredOperationsDetail,
                cancellationPassed = false,
                reusePassed = false,
                deviceAvailableAfter = true,
                failure = "requested ${config.backend}/${config.gpuLayers}, active ${active?.backend}/${active?.gpuLayers}",
            )
        }
        val greedy = runCatching {
            boundedQualificationStage("$label greedy output") {
                generateOutput(QUALIFICATION_PREFLIGHT_TOKENS)
            }
        }
        val cancellationPassed = runCatching {
            boundedQualificationStage("$label cancellation") { cancellationAndReuseCheck() }
        }.getOrDefault(false)
        val reuse = runCatching {
            boundedQualificationStage("$label reuse") {
                generateOutput(QUALIFICATION_REUSE_TOKENS)
            }
        }
        val currentCapabilities = engine.capabilities()
        val deviceAvailable = config.backend == Backend.CPU ||
            (config.backend in currentCapabilities.backends &&
                currentCapabilities.vulkanDevices.isNotEmpty())
        return QualificationPreflight(
            modelLoadSucceeded = true,
            loadDurationMs = loadDurationMs,
            loadTelemetry = RunTelemetry(before, after),
            nonEmptyGreedyOutput = greedy.getOrNull()?.text?.isNotEmpty() == true,
            greedyOutputSha256 = greedy.getOrNull()?.text?.sha256Text(),
            requiredOperationsPassed = requiredOperationsPassed,
            requiredOperationsDetail = requiredOperationsDetail,
            cancellationPassed = cancellationPassed,
            reusePassed = reuse.getOrNull()?.text?.isNotEmpty() == true,
            deviceAvailableAfter = deviceAvailable,
            failure = greedy.exceptionOrNull()?.message ?: reuse.exceptionOrNull()?.message,
        )
    }

    private suspend fun skippedOperationPreflight(detail: String): QualificationPreflight {
        val snapshot = telemetryMonitor.snapshotSafely()
        return QualificationPreflight(
            modelLoadSucceeded = false,
            loadDurationMs = 0.0,
            loadTelemetry = RunTelemetry(snapshot, snapshot),
            nonEmptyGreedyOutput = false,
            greedyOutputSha256 = null,
            requiredOperationsPassed = false,
            requiredOperationsDetail = detail,
            cancellationPassed = false,
            reusePassed = false,
            deviceAvailableAfter = true,
            failure = "Candidate execution skipped because required operation evidence failed",
        )
    }

    private suspend fun <T> boundedQualificationStage(
        label: String,
        block: suspend () -> T,
    ): T = coroutineScope {
        mutableQualification.update { it.copy(currentLabel = label) }
        val watchdog = launch {
            delay(QUALIFICATION_STAGE_TIMEOUT_MS)
            engine.cancel()
        }
        try {
            withTimeout(QUALIFICATION_STAGE_TIMEOUT_MS + QUALIFICATION_CANCEL_GRACE_MS) {
                block()
            }
        } finally {
            watchdog.cancel()
        }
    }

    private suspend fun cancellationAndReuseCheck(): Boolean {
        var emitted = 0
        var completed: GenerationMetrics? = null
        engine.generate(
            QUALIFICATION_PROMPT,
            GenerationConfig(maxTokens = QUALIFICATION_CANCEL_MAX_TOKENS, temperature = 0f),
        ).collect { event ->
            when (event) {
                is TokenEvent.Started -> Unit
                is TokenEvent.Token -> {
                    emitted++
                    if (emitted == QUALIFICATION_CANCEL_AFTER_TOKENS) engine.cancel()
                }
                is TokenEvent.Completed -> completed = event.metrics
                is TokenEvent.Failed -> error(event.message)
            }
        }
        return emitted in 1 until QUALIFICATION_CANCEL_MAX_TOKENS &&
            (completed?.generatedTokens ?: QUALIFICATION_CANCEL_MAX_TOKENS) <
            QUALIFICATION_CANCEL_MAX_TOKENS
    }

    private suspend fun loadAndMeasure(
        config: ModelConfig,
        candidate: BackendCandidate,
        generation: GenerationConfig,
    ): QualificationMeasurement =
        loadAndMeasure(config, candidate.backend, candidate.gpuLayers, generation)

    private suspend fun loadAndMeasure(
        config: ModelConfig,
        backend: Backend,
        gpuLayers: Int,
        generation: GenerationConfig,
    ): QualificationMeasurement {
        loadExact(config)
        val generated = generateOutput(generation.maxTokens)
        return QualificationMeasurement(
            backend = backend,
            gpuLayers = gpuLayers,
            outputSha256 = generated.text.sha256Text(),
            metrics = generated.metrics,
        )
    }

    private suspend fun generateMeasurement(
        candidate: BackendCandidate,
        generation: GenerationConfig,
    ): QualificationMeasurement {
        val generated = generateOutput(generation.maxTokens)
        return QualificationMeasurement(
            backend = candidate.backend,
            gpuLayers = candidate.gpuLayers,
            outputSha256 = generated.text.sha256Text(),
            metrics = generated.metrics,
        )
    }

    private suspend fun generateOutput(maxTokens: Int): GeneratedOutput {
        val output = StringBuilder()
        var metrics: GenerationMetrics? = null
        engine.generate(
            QUALIFICATION_PROMPT,
            GenerationConfig(
                maxTokens = maxTokens,
                temperature = 0f,
                seed = QUALIFICATION_SEED,
            ),
        ).collect { event ->
            when (event) {
                is TokenEvent.Started -> Unit
                is TokenEvent.Token -> output.append(event.text)
                is TokenEvent.Completed -> metrics = event.metrics
                is TokenEvent.Failed -> error(event.message)
            }
        }
        return GeneratedOutput(output.toString(), checkNotNull(metrics) { "Generation did not complete" })
    }

    private suspend fun loadExact(config: ModelConfig) {
        engine.load(config)
        val active = (engine.state.value as? EngineState.Ready)?.model
        check(active?.backend == config.backend && active.gpuLayers == config.gpuLayers) {
            "requested ${config.backend}/${config.gpuLayers}, active ${active?.backend}/${active?.gpuLayers}"
        }
    }

    private fun qualificationModelConfig(
        model: ImportedModel,
        backend: Backend,
        gpuLayers: Int,
    ) = ModelConfig(
        path = model.path,
        displayName = model.displayName,
        sha256 = model.sha256,
        contextSize = BundledPhasePolicy.value?.contextSize ?: 512,
        backend = backend,
        gpuLayers = gpuLayers,
        phasePolicy = BundledPhasePolicy.value,
    )

    private fun requiredOperationEvidence(capabilities: EngineCapabilities): Pair<Boolean?, String> {
        val knownPowerVrFailure = capabilities.vulkanDevices.any { device ->
            device.name.contains("PowerVR", ignoreCase = true) &&
                device.name.contains("BXM", ignoreCase = true)
        }
        return if (knownPowerVrFailure) {
            false to (
                "Known PowerVR BXM bf16/Q4_0 backend-op failures; retained logs sha256 " +
                    "0ae38cb89b2cc2ded272db5d2d720608a6f51fc98f1b6f8b676a42e85bfcf9cb " +
                    "and 1696850613818327f53becf1a3d7e0395f994991b91c2985332dfbbe7338cf1b"
                )
        } else {
            null to "Standalone required Q4_0 operation-shape evidence has not been imported"
        }
    }

    private suspend fun loadModel(model: ImportedModel) {
        val phasePolicy = BundledPhasePolicy.value
        val selection = mutableModels.value
        val config = ModelConfig(
            path = model.path,
            displayName = model.displayName,
            sha256 = model.sha256,
            contextSize = phasePolicy?.contextSize ?: 512,
            backend = selection.backend,
            gpuLayers = if (selection.backend == Backend.HYBRID) selection.gpuLayers else 0,
            phasePolicy = phasePolicy,
            backendPolicy = if (selection.backend == Backend.AUTO) BundledBackendPolicy.value else null,
        )
        runCatching { engine.load(config) }
            .onSuccess {
                mutableCapabilities.value = engine.capabilities()
                val active = (engine.state.value as? EngineState.Ready)?.model
                mutableModels.update {
                    it.copy(
                        status = buildString {
                            append("Loaded ${model.displayName}; ")
                            append("requested ${selection.backend.name}, active ")
                            append(active?.backend?.name ?: "unknown")
                            if (active?.backend == Backend.HYBRID) {
                                append(" (${active.gpuLayers} GPU layers)")
                            }
                            append("; ${BundledPhasePolicy.status}")
                            append("; ${BundledBackendPolicy.status}")
                        },
                        error = null,
                    )
                }
            }
            .onFailure { error ->
                mutableModels.update {
                    it.copy(
                        status = "Model imported; native engine unavailable",
                        error = error.message,
                    )
                }
            }
    }

    override fun onCleared() {
        engine.close()
        telemetryMonitor.close()
        super.onCleared()
    }

    private fun BackendCandidate.label(): String = when (backend) {
        Backend.VULKAN -> "Vulkan full offload"
        Backend.HYBRID -> "Hybrid $gpuLayers layers"
        else -> "$backend $gpuLayers"
    }

    class Factory(
        private val application: Application,
        private val monitor: DeviceTelemetryMonitor,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            check(modelClass.isAssignableFrom(MobileSpecViewModel::class.java))
            val socModel = if (Build.VERSION.SDK_INT >= 31) Build.SOC_MODEL else Build.HARDWARE
            val deviceFingerprint = runCatching {
                runtimeDeviceFingerprintSha256(
                    Build.MODEL,
                    socModel,
                    Runtime.getRuntime().availableProcessors(),
                )
            }.getOrNull()
            val nativeLibrarySha256 = sha256NativeLibrary(application)
            val engine = LlamaInferenceEngine(
                telemetryProvider = monitor,
                appCommit = BuildConfig.APP_COMMIT,
                appSourceSha256 = BuildConfig.APP_SOURCE_SHA256,
                llamaCommit = BuildConfig.LLAMA_COMMIT,
                llamaSourceDiffSha256 = BuildConfig.LLAMA_SOURCE_DIFF_SHA256,
                nativeLibrarySha256 = nativeLibrarySha256,
                runtimeDeviceFingerprintSha256 = deviceFingerprint,
                runtimeDeviceModel = Build.MODEL,
                runtimeSocModel = socModel,
            )
            return MobileSpecViewModel(
                application,
                engine,
                monitor,
                QualificationBuildIdentity(
                    appCommit = BuildConfig.APP_COMMIT,
                    appSourceSha256 = BuildConfig.APP_SOURCE_SHA256,
                    llamaCommit = BuildConfig.LLAMA_COMMIT,
                    llamaSourceDiffSha256 = BuildConfig.LLAMA_SOURCE_DIFF_SHA256,
                    nativeLibrarySha256 = nativeLibrarySha256,
                    deviceFingerprintSha256 = deviceFingerprint,
                    deviceModel = Build.MODEL,
                    socModel = socModel,
                ),
            ) as T
        }
    }

    private companion object {
        const val QUALIFICATION_PROMPT =
            "Explain why on-device inference matters in exactly two concise sentences."
        const val QUALIFICATION_MAX_TOKENS = 64
        const val QUALIFICATION_PREFLIGHT_TOKENS = 32
        const val QUALIFICATION_REUSE_TOKENS = 8
        const val QUALIFICATION_CANCEL_MAX_TOKENS = 64
        const val QUALIFICATION_CANCEL_AFTER_TOKENS = 2
        const val QUALIFICATION_REPETITIONS = 3
        const val QUALIFICATION_SEED = 42L
        const val QUALIFICATION_STAGE_TIMEOUT_MS = 30_000L
        const val QUALIFICATION_CANCEL_GRACE_MS = 5_000L
    }
}

private suspend fun DeviceTelemetryMonitor.snapshotSafely() =
    runCatching { snapshot() }.getOrNull()

private fun String.sha256Text(): String = MessageDigest.getInstance("SHA-256")
    .digest(toByteArray(Charsets.UTF_8))
    .joinToString("") { "%02x".format(it) }

private fun sha256OrNull(file: File): String? = runCatching {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    digest.digest().joinToString("") { "%02x".format(it) }
}.getOrNull()

private fun sha256NativeLibrary(application: Application): String? {
    val libraryName = "libmobilespec_llama.so"
    val extracted = File(application.applicationInfo.nativeLibraryDir, libraryName)
    sha256OrNull(extracted)?.let { return it }

    return runCatching {
        ZipFile(application.applicationInfo.sourceDir).use { apk ->
            val entry = Build.SUPPORTED_ABIS
                .asSequence()
                .map { abi -> "lib/$abi/$libraryName" }
                .mapNotNull(apk::getEntry)
                .first()
            val digest = MessageDigest.getInstance("SHA-256")
            apk.getInputStream(entry).use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
            digest.digest().joinToString("") { "%02x".format(it) }
        }
    }.getOrNull()
}
