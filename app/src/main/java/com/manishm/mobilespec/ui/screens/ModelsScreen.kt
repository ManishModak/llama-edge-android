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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.manishm.mobilespec.ImportedModel
import com.manishm.mobilespec.ModelsUiState
import com.manishm.mobilespec.QualificationUiState
import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.QualificationGateStatus
import java.util.Locale

@Composable
fun ModelsScreen(
    state: ModelsUiState,
    capabilities: EngineCapabilities,
    qualification: QualificationUiState,
    onImport: () -> Unit,
    onSelect: (ImportedModel) -> Unit,
    onBackendChange: (Backend) -> Unit,
    onGpuLayersChange: (Int) -> Unit,
    onRunQualification: () -> Unit,
    onCancelQualification: () -> Unit,
    onExportQualification: () -> Unit,
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
        Text("Execution backend", style = MaterialTheme.typography.titleMedium)
        Text(
            "Auto uses GPU only with a matching qualified profile; otherwise it safely uses CPU.",
            style = MaterialTheme.typography.bodySmall,
        )
        Backend.entries.forEach { backend ->
            val supported = backend == Backend.CPU || backend == Backend.AUTO ||
                backend in capabilities.backends
            FilterChip(
                selected = state.backend == backend,
                onClick = { onBackendChange(backend) },
                enabled = supported,
                label = {
                    Text(
                        when (backend) {
                            Backend.CPU -> "CPU"
                            Backend.VULKAN -> "Vulkan (full offload)"
                            Backend.HYBRID -> "Hybrid (partial offload)"
                            Backend.AUTO -> "Auto (qualified policy)"
                        },
                    )
                },
            )
        }
        if (state.backend == Backend.HYBRID) {
            OutlinedTextField(
                value = state.gpuLayers.toString(),
                onValueChange = { value -> value.toIntOrNull()?.let(onGpuLayersChange) },
                label = { Text("GPU layers") },
                supportingText = { Text("Positive bounded layer count; validate against memory before qualification.") },
                singleLine = true,
            )
        }
        Text("Short backend qualification", style = MaterialTheme.typography.titleMedium)
        Text(
            "Runs fail-fast load, output, operation, cancellation, reuse, memory, thermal, and " +
                "counterbalanced A/B gates. Capability alone cannot promote Auto.",
            style = MaterialTheme.typography.bodySmall,
        )
        if (qualification.running) {
            Text(
                qualification.currentLabel ?: "Qualification running…",
                color = MaterialTheme.colorScheme.primary,
            )
            Text("${qualification.completedCandidates}/${qualification.totalCandidates} candidates")
            Button(onClick = onCancelQualification) { Text("Cancel qualification") }
        } else {
            Button(
                onClick = onRunQualification,
                enabled = state.selected != null && capabilities.supportsGpuOffload,
            ) {
                Text("Run short qualification")
            }
        }
        qualification.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        qualification.report?.let { report ->
            report.records.forEach { record ->
                val candidate = record.evidence.candidate
                val failed = record.evaluation.gates.firstOrNull {
                    it.status != QualificationGateStatus.PASS
                }
                Text(
                    "${candidate.backend} ${candidate.gpuLayers}: " +
                        "${record.evaluation.verdict}" +
                        (failed?.let { " · ${it.name}: ${it.detail}" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Button(onClick = onExportQualification) { Text("Export qualification JSON") }
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
