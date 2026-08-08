package com.manishm.mobilespec.engine

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

/**
 * Serialized inference surface shared by the app and native implementations.
 *
 * Implementations must run at most one load/generate/benchmark/unload operation at a time.
 * [cancel] is intentionally exempt so it can interrupt a blocking native decode.
 */
interface InferenceEngine : AutoCloseable {
    val state: StateFlow<EngineState>

    fun capabilities(): EngineCapabilities

    suspend fun load(model: ModelConfig)

    fun generate(prompt: String, config: GenerationConfig = GenerationConfig()): Flow<TokenEvent>

    fun benchmark(config: BenchmarkConfig): Flow<BenchmarkEvent>

    fun cancel()

    suspend fun unload()

    override fun close()
}

sealed interface EngineState {
    data object Unloaded : EngineState
    data class Loading(val modelName: String) : EngineState
    data class Ready(val model: ModelConfig) : EngineState
    data class Running(val operation: String) : EngineState
    data class Error(val message: String, val recoverable: Boolean) : EngineState
}

fun interface TelemetryProvider {
    suspend fun snapshot(): TelemetrySnapshot
}
