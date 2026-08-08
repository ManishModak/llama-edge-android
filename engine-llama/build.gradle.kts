import org.jetbrains.kotlin.gradle.dsl.JvmTarget

val enableKleidiAI = providers.gradleProperty("mobilespec.enableKleidiAI").orElse("true")
val enableVulkan = providers.gradleProperty("mobilespec.enableVulkan").orElse("true")

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.manishm.mobilespec.llama"
    compileSdk = 36

    defaultConfig {
        minSdk = 28
        ndkVersion = "28.2.13676358"
        consumerProguardFiles("consumer-rules.pro")
        ndk {
            abiFilters += "arm64-v8a"
        }
        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DANDROID_STL=c++_shared",
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DMOBILESPEC_ENABLE_KLEIDIAI=${enableKleidiAI.get()}",
                    "-DMOBILESPEC_ENABLE_VULKAN=${enableVulkan.get()}",
                )
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions.jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    api(project(":engine-api"))
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(libs.junit)
}
