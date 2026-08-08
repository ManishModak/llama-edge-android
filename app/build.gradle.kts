import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

fun commandOutput(vararg command: String): String = runCatching {
    val output = ByteArrayOutputStream()
    exec {
        commandLine(*command)
        standardOutput = output
        errorOutput = ByteArrayOutputStream()
        isIgnoreExitValue = true
    }
    output.toString()
}.getOrDefault("")

fun gitRevision(vararg command: String): String =
    commandOutput(*command).trim().ifBlank { "unknown" }

fun sha256Text(value: String): String = MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray())
    .joinToString("") { "%02x".format(it) }

fun sha256Files(files: Set<File>): String {
    val digest = MessageDigest.getInstance("SHA-256")
    files.filter(File::isFile)
        .sortedBy { it.relativeTo(rootDir).invariantSeparatorsPath }
        .forEach { file ->
            digest.update(file.relativeTo(rootDir).invariantSeparatorsPath.toByteArray())
            digest.update(0)
            file.inputStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
        }
    return digest.digest().joinToString("") { "%02x".format(it) }
}

val appCommit = gitRevision("git", "rev-parse", "--short=12", "HEAD")
val llamaCommit = gitRevision(
    "git",
    "-C",
    rootProject.layout.projectDirectory.dir("third_party/llama.cpp").asFile.absolutePath,
    "rev-parse",
    "--short=12",
    "HEAD",
)
val llamaSourceDiffSha256 = sha256Text(
    commandOutput(
        "git",
        "-C",
        rootProject.layout.projectDirectory.dir("third_party/llama.cpp").asFile.absolutePath,
        "diff",
        "--binary",
        "HEAD",
        "--",
    ),
)
val runtimeSourceFiles = files(
    fileTree("src"),
    rootProject.fileTree("engine-api/src"),
    rootProject.fileTree("engine-llama/src"),
    rootProject.files(
        "settings.gradle.kts",
        "build.gradle.kts",
        "gradle.properties",
        "gradle/libs.versions.toml",
        "app/build.gradle.kts",
        "engine-api/build.gradle.kts",
        "engine-llama/build.gradle.kts",
    ),
).files
val appSourceSha256 = sha256Files(runtimeSourceFiles)

android {
    namespace = "com.manishm.mobilespec"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.manishm.mobilespec"
        minSdk = 28
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "APP_COMMIT", "\"$appCommit\"")
        buildConfigField("String", "APP_SOURCE_SHA256", "\"$appSourceSha256\"")
        buildConfigField("String", "LLAMA_COMMIT", "\"$llamaCommit\"")
        buildConfigField(
            "String",
            "LLAMA_SOURCE_DIFF_SHA256",
            "\"$llamaSourceDiffSha256\"",
        )
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    buildTypes {
        release {
            // A reproducible demo APK; replace with a private upload key for store distribution.
            signingConfig = signingConfigs.getByName("debug")
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions.jvmTarget.set(JvmTarget.JVM_17)
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    implementation(project(":engine-api"))
    implementation(project(":engine-llama"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.kotlinx.coroutines.android)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
}
