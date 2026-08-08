package com.manishm.mobilespec

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class ImportedModel(
    val displayName: String,
    val path: String,
    val sha256: String,
    val sizeBytes: Long,
)

class ModelImporter(private val context: Context) {
    suspend fun import(uri: Uri): ImportedModel = withContext(Dispatchers.IO) {
        val (displayName, declaredSize) = queryMetadata(uri)
        require(displayName.endsWith(".gguf", ignoreCase = true)) {
            "Choose a .gguf model"
        }
        val modelDirectory = File(context.filesDir, "models").apply { mkdirs() }
        val safeName = displayName.replace(Regex("[^A-Za-z0-9._-]"), "_")
        val destination = uniqueDestination(modelDirectory, safeName)
        val temporary = File(modelDirectory, "${destination.name}.partial")
        val digest = MessageDigest.getInstance("SHA-256")
        try {
            context.contentResolver.openInputStream(uri).use { input ->
                requireNotNull(input) { "Unable to open selected model" }
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        output.write(buffer, 0, count)
                        digest.update(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            check(temporary.renameTo(destination)) { "Unable to finalize imported model" }
            ImportedModel(
                displayName = displayName,
                path = destination.absolutePath,
                sha256 = digest.digest().joinToString("") { "%02x".format(it) },
                sizeBytes = if (declaredSize >= 0) declaredSize else destination.length(),
            )
        } catch (error: Exception) {
            temporary.delete()
            throw error
        }
    }

    private fun queryMetadata(uri: Uri): Pair<String, Long> {
        context.contentResolver.query(
            uri,
            arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null,
            null,
            null,
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                val name = cursor.getString(0) ?: "model.gguf"
                val size = if (cursor.isNull(1)) -1L else cursor.getLong(1)
                return name to size
            }
        }
        return "model.gguf" to -1L
    }

    private fun uniqueDestination(directory: File, name: String): File {
        val candidate = File(directory, name)
        if (!candidate.exists()) return candidate
        val stem = name.substringBeforeLast('.', name)
        val extension = name.substringAfterLast('.', "")
        var index = 2
        while (true) {
            val suffix = if (extension.isBlank()) "$stem-$index" else "$stem-$index.$extension"
            val next = File(directory, suffix)
            if (!next.exists()) return next
            index++
        }
    }
}
