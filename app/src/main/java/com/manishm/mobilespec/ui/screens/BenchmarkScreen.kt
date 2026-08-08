package com.manishm.mobilespec.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.manishm.mobilespec.BenchmarkUiState
import com.manishm.mobilespec.engine.BenchmarkSummary
import com.manishm.mobilespec.engine.InferenceMode
import com.manishm.mobilespec.engine.summary
import java.util.Locale

@Composable
fun BenchmarkScreen(
    state: BenchmarkUiState,
    modelReady: Boolean,
    optimizedAvailable: Boolean,
    onRun: (String) -> Unit,
    onCancel: () -> Unit,
    onExport: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var prompt by rememberSaveable {
        mutableStateOf("Write a compact Kotlin function that computes a SHA-256 digest.")
    }
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Baseline vs optimized", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Runs the same fixed prompt five times per mode. Native timings and before/after " +
                "thermal, battery, and memory snapshots are included in the JSON.",
            style = MaterialTheme.typography.bodyMedium,
        )
        OutlinedTextField(
            value = prompt,
            onValueChange = { prompt = it },
            label = { Text("Fixed benchmark prompt") },
            minLines = 3,
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.running,
        )
        if (state.running) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            Text("${state.completedRuns}/${state.totalRuns} · ${state.currentLabel.orEmpty()}")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { onRun(prompt) },
                enabled =
                    modelReady && optimizedAvailable && !state.running && prompt.isNotBlank(),
            ) {
                Text("Run A/B suite")
            }
            if (state.running) {
                OutlinedButton(onClick = onCancel) { Text("Cancel") }
            }
            OutlinedButton(onClick = onExport, enabled = state.result != null && !state.running) {
                Text("Export JSON")
            }
        }
        if (!modelReady) {
            Text(
                "Import a model and package the JNI engine to run benchmarks.",
                color = MaterialTheme.colorScheme.tertiary,
            )
        } else if (!optimizedAvailable) {
            Text(
                "A/B is disabled until a verified phase-policy profile matches this exact " +
                    "model, llama.cpp build, and context.",
                color = MaterialTheme.colorScheme.tertiary,
            )
        }
        state.error?.let {
            Text(it, color = MaterialTheme.colorScheme.error)
        }
        state.result?.let { result ->
            Text("Result ${result.id.take(8)}", style = MaterialTheme.typography.titleMedium)
            Text(
                when (result.correctnessMatched) {
                    true -> "Correctness: exact output hashes match"
                    false -> "Correctness gate failed: output hashes differ"
                    null -> "Correctness: not comparable"
                },
                color = if (result.correctnessMatched == false) {
                    MaterialTheme.colorScheme.error
                } else {
                    MaterialTheme.colorScheme.onSurface
                },
            )
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                SummaryCard(result.summary(InferenceMode.BASELINE), Modifier.weight(1f))
                SummaryCard(result.summary(InferenceMode.OPTIMIZED), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun SummaryCard(summary: BenchmarkSummary, modifier: Modifier) {
    Card(modifier = modifier) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(summary.mode.name.lowercase().replaceFirstChar(Char::uppercase))
            Text(
                String.format(Locale.US, "%.2f tok/s", summary.meanTokensPerSecond),
                style = MaterialTheme.typography.titleLarge,
            )
            Text(String.format(Locale.US, "TTFT %.1f ms", summary.meanTimeToFirstTokenMs))
            Text("${summary.completedRuns} runs", style = MaterialTheme.typography.labelMedium)
        }
    }
}
