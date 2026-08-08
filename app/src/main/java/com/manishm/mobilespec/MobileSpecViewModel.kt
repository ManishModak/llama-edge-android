package com.manishm.mobilespec

import android.app.Application
import android.net.Uri
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.manishm.mobilespec.engine.BenchmarkConfig
import com.manishm.mobilespec.engine.BenchmarkEvent
import com.manishm.mobilespec.engine.BenchmarkResult
import com.manishm.mobilespec.engine.BenchmarkResultJson
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.GenerationConfig
import com.manishm.mobilespec.engine.GenerationMetrics
import com.manishm.mobilespec.engine.InferenceEngine
import com.manishm.mobilespec.engine.InferenceMode
import com.manishm.mobilespec.engine.ModelConfig
import com.manishm.mobilespec.engine.TokenEvent
import com.manishm.mobilespec.llama.LlamaInferenceEngine
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

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
    val status: String? = null,
    val error: String? = null,
)

class MobileSpecViewModel(
    application: Application,
    private val engine: InferenceEngine,
    val telemetryMonitor: DeviceTelemetryMonitor,
) : AndroidViewModel(application) {
    private val importer = ModelImporter(application)
    private val mutableBenchmark = MutableStateFlow(BenchmarkUiState())
    private val mutableChat = MutableStateFlow(ChatUiState())
    private val mutableModels = MutableStateFlow(ModelsUiState())
    private val mutableCapabilities = MutableStateFlow(engine.capabilities())
    private var benchmarkJob: Job? = null
    private var generationJob: Job? = null

    val benchmark: StateFlow<BenchmarkUiState> = mutableBenchmark.asStateFlow()
    val chat: StateFlow<ChatUiState> = mutableChat.asStateFlow()
    val models: StateFlow<ModelsUiState> = mutableModels.asStateFlow()
    val capabilities: StateFlow<EngineCapabilities> = mutableCapabilities.asStateFlow()
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
        mutableBenchmark.update {
            if (it.running) it.copy(running = false, currentLabel = null, error = "Cancelled") else it
        }
        mutableChat.update { it.copy(running = false) }
    }

    private suspend fun loadModel(model: ImportedModel) {
        val phasePolicy = BundledPhasePolicy.value
        val config = ModelConfig(
            path = model.path,
            displayName = model.displayName,
            sha256 = model.sha256,
            contextSize = phasePolicy?.contextSize ?: 512,
            phasePolicy = phasePolicy,
        )
        runCatching { engine.load(config) }
            .onSuccess {
                mutableCapabilities.value = engine.capabilities()
                mutableModels.update {
                    it.copy(
                        status = "Loaded ${model.displayName}; ${BundledPhasePolicy.status}",
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

    class Factory(
        private val application: Application,
        private val monitor: DeviceTelemetryMonitor,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            check(modelClass.isAssignableFrom(MobileSpecViewModel::class.java))
            val engine = LlamaInferenceEngine(
                telemetryProvider = monitor,
                appCommit = BuildConfig.APP_COMMIT,
                appSourceSha256 = BuildConfig.APP_SOURCE_SHA256,
                llamaCommit = BuildConfig.LLAMA_COMMIT,
                llamaSourceDiffSha256 = BuildConfig.LLAMA_SOURCE_DIFF_SHA256,
                nativeLibrarySha256 = sha256NativeLibrary(application),
                runtimeDeviceModel = Build.MODEL,
                runtimeSocModel = if (Build.VERSION.SDK_INT >= 31) Build.SOC_MODEL else Build.HARDWARE,
            )
            return MobileSpecViewModel(application, engine, monitor) as T
        }
    }
}

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
