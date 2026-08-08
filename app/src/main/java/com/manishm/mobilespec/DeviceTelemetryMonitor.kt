package com.manishm.mobilespec

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import com.manishm.mobilespec.engine.TelemetryProvider
import com.manishm.mobilespec.engine.TelemetrySnapshot
import java.io.Closeable
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class DeviceTelemetryMonitor(context: Context) : TelemetryProvider, Closeable {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val mutableSnapshots = MutableStateFlow<TelemetrySnapshot?>(null)
    private val pollingJob: Job

    val snapshots: StateFlow<TelemetrySnapshot?> = mutableSnapshots.asStateFlow()

    init {
        pollingJob = scope.launch {
            while (isActive) {
                mutableSnapshots.value = snapshot()
                delay(1_000)
            }
        }
    }

    override suspend fun snapshot(): TelemetrySnapshot = withContext(Dispatchers.IO) {
        val activityManager = appContext.getSystemService(ActivityManager::class.java)
        val powerManager = appContext.getSystemService(PowerManager::class.java)
        val memory = ActivityManager.MemoryInfo().also(activityManager::getMemoryInfo)
        val battery = appContext.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val status = battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val temperatureTenths =
            battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE) ?: Int.MIN_VALUE
        val thermal = if (Build.VERSION.SDK_INT >= 29) {
            powerManager.currentThermalStatus
        } else {
            -1
        }
        val vulkan = vulkanVersion()
        val processStatus = readKilobyteFields(
            File("/proc/self/status"),
            setOf("VmRSS", "VmHWM"),
        )
        val meminfo = readKilobyteFields(
            File("/proc/meminfo"),
            setOf("SwapTotal", "SwapFree"),
        )
        TelemetrySnapshot(
            timestampEpochMs = System.currentTimeMillis(),
            thermalStatus = thermal,
            thermalStatusName = thermalName(thermal),
            batteryPercent =
                if (level >= 0 && scale > 0) level * 100f / scale else null,
            batteryTemperatureC =
                if (temperatureTenths != Int.MIN_VALUE) temperatureTenths / 10f else null,
            charging =
                if (status >= 0) {
                    status == BatteryManager.BATTERY_STATUS_CHARGING ||
                        status == BatteryManager.BATTERY_STATUS_FULL
                } else {
                    null
                },
            availableMemoryBytes = memory.availMem,
            totalMemoryBytes = memory.totalMem,
            lowMemory = memory.lowMemory,
            deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
            socName = if (Build.VERSION.SDK_INT >= 31) Build.SOC_MODEL else "unknown",
            cpuCoreCount = Runtime.getRuntime().availableProcessors(),
            supportedAbis = Build.SUPPORTED_ABIS.toList(),
            vulkanVersion = vulkan,
            vulkanDetail = if (vulkan == null) "No Vulkan hardware feature reported" else null,
            processResidentSetBytes = processStatus["VmRSS"]?.times(1024),
            processPeakRssBytes = processStatus["VmHWM"]?.times(1024),
            swapTotalBytes = meminfo["SwapTotal"]?.times(1024),
            swapFreeBytes = meminfo["SwapFree"]?.times(1024),
        )
    }

    override fun close() {
        pollingJob.cancel()
    }

    private fun vulkanVersion(): String? {
        val feature = appContext.packageManager.systemAvailableFeatures.firstOrNull {
            it.name == PackageManager.FEATURE_VULKAN_HARDWARE_VERSION
        } ?: return null
        val version = feature.version
        val major = version ushr 22
        val minor = (version ushr 12) and 0x3ff
        val patch = version and 0xfff
        return "$major.$minor.$patch"
    }

    private fun thermalName(status: Int): String = if (Build.VERSION.SDK_INT < 29) {
        "UNAVAILABLE"
    } else {
        when (status) {
            PowerManager.THERMAL_STATUS_NONE -> "NONE"
            PowerManager.THERMAL_STATUS_LIGHT -> "LIGHT"
            PowerManager.THERMAL_STATUS_MODERATE -> "MODERATE"
            PowerManager.THERMAL_STATUS_SEVERE -> "SEVERE"
            PowerManager.THERMAL_STATUS_CRITICAL -> "CRITICAL"
            PowerManager.THERMAL_STATUS_EMERGENCY -> "EMERGENCY"
            PowerManager.THERMAL_STATUS_SHUTDOWN -> "SHUTDOWN"
            else -> "UNKNOWN"
        }
    }

    private fun readKilobyteFields(file: File, keys: Set<String>): Map<String, Long> =
        runCatching {
            file.useLines { lines ->
                lines.mapNotNull { line ->
                    val separator = line.indexOf(':')
                    if (separator <= 0) return@mapNotNull null
                    val key = line.substring(0, separator)
                    if (key !in keys) return@mapNotNull null
                    val value = line.substring(separator + 1)
                        .trim()
                        .substringBefore(' ')
                        .toLongOrNull()
                    value?.let { key to it }
                }.toMap()
            }
        }.getOrDefault(emptyMap())
}
