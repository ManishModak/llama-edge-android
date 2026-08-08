package com.manishm.mobilespec.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.manishm.mobilespec.MobileSpecViewModel
import com.manishm.mobilespec.ui.screens.BenchmarkScreen
import com.manishm.mobilespec.ui.screens.ChatScreen
import com.manishm.mobilespec.ui.screens.DeviceScreen
import com.manishm.mobilespec.ui.screens.ModelsScreen

private enum class AppTab(val label: String, val shortLabel: String) {
    BENCHMARK("Benchmark", "A/B"),
    DEVICE("Device", "HW"),
    CHAT("Chat", "AI"),
    MODELS("Models", "GG"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MobileSpecApp(
    viewModel: MobileSpecViewModel,
    onShareBenchmark: (String) -> Unit,
) {
    var selectedTab by remember { mutableStateOf(AppTab.BENCHMARK) }
    val benchmark by viewModel.benchmark.collectAsStateWithLifecycle()
    val chat by viewModel.chat.collectAsStateWithLifecycle()
    val models by viewModel.models.collectAsStateWithLifecycle()
    val capabilities by viewModel.capabilities.collectAsStateWithLifecycle()
    val telemetry by viewModel.telemetryMonitor.snapshots.collectAsStateWithLifecycle()
    val modelLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) viewModel.importModel(uri)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("MobileSpec")
                        Text(
                            text = if (capabilities.nativeAvailable) {
                                "llama.cpp ready"
                            } else {
                                "UI mode · native engine not packaged"
                            },
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar {
                AppTab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = selectedTab == tab,
                        onClick = { selectedTab = tab },
                        icon = { Text(tab.shortLabel) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        val modifier = Modifier.fillMaxSize().padding(padding)
        when (selectedTab) {
            AppTab.BENCHMARK -> BenchmarkScreen(
                state = benchmark,
                modelReady = models.selected != null && capabilities.nativeAvailable,
                optimizedAvailable = capabilities.supportsPhaseAwareThreadPolicy,
                onRun = viewModel::runBenchmark,
                onCancel = viewModel::cancel,
                onExport = {
                    viewModel.benchmarkJson()?.let(onShareBenchmark)
                },
                modifier = modifier,
            )
            AppTab.DEVICE -> DeviceScreen(
                telemetry = telemetry,
                capabilities = capabilities,
                modifier = modifier,
            )
            AppTab.CHAT -> ChatScreen(
                state = chat,
                modelReady = models.selected != null && capabilities.nativeAvailable,
                optimizedAvailable = capabilities.supportsPhaseAwareThreadPolicy,
                onPromptChange = viewModel::updatePrompt,
                onModeChange = viewModel::setMode,
                onGenerate = viewModel::generate,
                onCancel = viewModel::cancel,
                modifier = modifier,
            )
            AppTab.MODELS -> ModelsScreen(
                state = models,
                onImport = { modelLauncher.launch(arrayOf("application/octet-stream", "*/*")) },
                onSelect = viewModel::selectModel,
                modifier = modifier,
            )
        }
    }
}
