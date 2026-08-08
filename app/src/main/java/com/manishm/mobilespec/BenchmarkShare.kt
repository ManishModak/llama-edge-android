package com.manishm.mobilespec

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File

fun shareBenchmarkJson(context: Context, json: String) {
    val exportDirectory = File(context.cacheDir, "exports").apply { mkdirs() }
    val file = File(exportDirectory, "mobilespec-${System.currentTimeMillis()}.json")
    file.writeText(json)
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = "application/json"
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(intent, "Export benchmark result"))
}
