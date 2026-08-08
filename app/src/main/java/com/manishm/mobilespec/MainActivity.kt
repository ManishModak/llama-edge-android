package com.manishm.mobilespec

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.ViewModelProvider
import com.manishm.mobilespec.ui.MobileSpecApp
import com.manishm.mobilespec.ui.theme.MobileSpecTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val monitor = DeviceTelemetryMonitor(applicationContext)
        val viewModel = ViewModelProvider(
            this,
            MobileSpecViewModel.Factory(application, monitor),
        )[MobileSpecViewModel::class.java]

        setContent {
            MobileSpecTheme {
                MobileSpecApp(
                    viewModel = viewModel,
                    onShareBenchmark = { json -> shareBenchmarkJson(this, json) },
                )
            }
        }
    }
}
