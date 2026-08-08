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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.manishm.mobilespec.ImportedModel
import com.manishm.mobilespec.ModelsUiState
import java.util.Locale

@Composable
fun ModelsScreen(
    state: ModelsUiState,
    onImport: () -> Unit,
    onSelect: (ImportedModel) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("GGUF models", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Selected files are copied into app storage and hashed before native loading.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Button(onClick = onImport, enabled = !state.importing) {
            Text(if (state.importing) "Importing…" else "Import GGUF")
        }
        state.status?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (state.models.isEmpty()) {
            Text("No models imported in this session.")
        }
        state.models.forEach { model ->
            Card(
                onClick = { onSelect(model) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    Modifier.padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    RadioButton(
                        selected = state.selected?.path == model.path,
                        onClick = { onSelect(model) },
                    )
                    Column {
                        Text(model.displayName, style = MaterialTheme.typography.titleMedium)
                        Text(model.sizeBytes.toSize())
                        Text(
                            "sha256 ${model.sha256.take(16)}…",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }
        }
    }
}

private fun Long.toSize(): String = String.format(Locale.US, "%.2f GiB", this / 1_073_741_824.0)
