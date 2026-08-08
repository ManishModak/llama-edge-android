package com.manishm.mobilespec.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.manishm.mobilespec.ChatUiState
import com.manishm.mobilespec.engine.InferenceMode
import java.util.Locale

@Composable
fun ChatScreen(
    state: ChatUiState,
    modelReady: Boolean,
    optimizedAvailable: Boolean,
    onPromptChange: (String) -> Unit,
    onModeChange: (InferenceMode) -> Unit,
    onGenerate: () -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Streaming chat", style = MaterialTheme.typography.headlineSmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            InferenceMode.entries.forEach { mode ->
                FilterChip(
                    selected = state.mode == mode,
                    onClick = { onModeChange(mode) },
                    label = { Text(mode.name.lowercase()) },
                    enabled =
                        !state.running &&
                            (mode == InferenceMode.BASELINE || optimizedAvailable),
                )
            }
        }
        OutlinedTextField(
            value = state.prompt,
            onValueChange = onPromptChange,
            label = { Text("Prompt") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
            enabled = !state.running,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = onGenerate,
                enabled = modelReady && !state.running && state.prompt.isNotBlank(),
            ) {
                Text(if (state.running) "Generating…" else "Generate")
            }
            if (state.running) {
                OutlinedButton(onClick = onCancel) { Text("Cancel") }
            }
        }
        if (!modelReady) {
            Text(
                "Import a model and package the JNI engine to generate.",
                color = MaterialTheme.colorScheme.tertiary,
            )
        } else if (!optimizedAvailable) {
            Text(
                "Optimized mode stays disabled until a verified phase policy matches this model, " +
                    "llama.cpp build, and context.",
                color = MaterialTheme.colorScheme.tertiary,
            )
        }
        state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (state.response.isNotEmpty()) {
            Card {
                Text(state.response, Modifier.padding(12.dp))
            }
        }
        state.metrics?.let { metrics ->
            Text(
                String.format(
                    Locale.US,
                    "%d tokens · %.2f tok/s · TTFT %.1f ms",
                    metrics.generatedTokens,
                    metrics.decodeTokensPerSecond,
                    metrics.timeToFirstTokenMs,
                ),
                style = MaterialTheme.typography.labelLarge,
            )
        }
    }
}
