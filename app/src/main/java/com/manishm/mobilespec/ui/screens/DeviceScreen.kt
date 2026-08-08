package com.manishm.mobilespec.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.manishm.mobilespec.engine.EngineCapabilities
import com.manishm.mobilespec.engine.TelemetrySnapshot
import java.util.Locale

@Composable
fun DeviceScreen(
    telemetry: TelemetrySnapshot?,
    capabilities: EngineCapabilities,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Live device telemetry", style = MaterialTheme.typography.headlineSmall)
        if (telemetry == null) {
            Text("Reading device…")
            return@Column
        }
        InfoCard("Device") {
            InfoRow("Name", telemetry.deviceName)
            InfoRow("SoC", telemetry.socName)
            InfoRow("CPU", "${telemetry.cpuCoreCount} cores · ${telemetry.supportedAbis.joinToString()}")
        }
        InfoCard("Memory") {
            InfoRow("Available", telemetry.availableMemoryBytes.toGiB())
            InfoRow("Total", telemetry.totalMemoryBytes.toGiB())
            InfoRow("Pressure", if (telemetry.lowMemory) "LOW MEMORY" else "normal")
        }
        InfoCard("Thermal & battery") {
            InfoRow("Thermal", telemetry.thermalStatusName)
            InfoRow("Battery", telemetry.batteryPercent?.let { "%.0f %%".format(it) } ?: "unknown")
            InfoRow(
                "Temperature",
                telemetry.batteryTemperatureC?.let { "%.1f °C".format(it) } ?: "unknown",
            )
            InfoRow("Charging", telemetry.charging?.toString() ?: "unknown")
        }
        InfoCard("Inference") {
            InfoRow("Vulkan API", telemetry.vulkanVersion ?: "not reported")
            InfoRow("Native engine", if (capabilities.nativeAvailable) "ready" else "not packaged")
            InfoRow(
                "Backends",
                capabilities.backends.joinToString().ifBlank { "none" },
            )
            InfoRow(
                "Speculation",
                if (capabilities.supportsSpeculativeDecoding) "supported" else "unavailable",
            )
            capabilities.detail?.let { InfoRow("Detail", it) }
        }
    }
}

@Composable
private fun InfoCard(title: String, content: @Composable () -> Unit) {
    Card {
        Column(
            Modifier.fillMaxSize().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Text("$label: $value", style = MaterialTheme.typography.bodyMedium)
}

private fun Long.toGiB(): String = String.format(Locale.US, "%.2f GiB", this / 1_073_741_824.0)
