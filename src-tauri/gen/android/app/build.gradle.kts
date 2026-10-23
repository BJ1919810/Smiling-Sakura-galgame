import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("rust")
}

val tauriProperties = Properties().apply {
    val propFile = file("tauri.properties")
    if (propFile.exists()) {
        propFile.inputStream().use { load(it) }
    }
}

android {
    compileSdk = 36
    namespace = "com.qingyu.xiaoying"
    defaultConfig {
        manifestPlaceholders["usesCleartextTraffic"] = "false"
        applicationId = "com.qingyu.xiaoying"
        minSdk = 24
        targetSdk = 36
        versionCode = tauriProperties.getProperty("tauri.android.versionCode", "1").toInt()
        versionName = tauriProperties.getProperty("tauri.android.versionName", "1.0")
    }
    buildTypes {
        getByName("debug") {
            manifestPlaceholders["usesCleartextTraffic"] = "true"
            isDebuggable = true
            isJniDebuggable = true
            isMinifyEnabled = false
            // ★ 2026-09-19 瘦身（主人令「瘦点身」）：
            //   ① 删掉原来的 `jniLibs.keepDebugSymbols.add("*/arm64-v8a/*.so")` 等四条 —— 它们会让
            //      AGP **不 strip**，把 221 MB 的 debug .so 原样塞进包。现在改由
            //      `tools/build-apk.py` 预先 `llvm-strip --strip-all`（221 MB → 121 MB）。
            //   ② `useLegacyPackaging = true` —— 默认 .so 在 APK 里是 **stored（不压缩）**，
            //      启用后走 deflate（体积再降约 2/3，代价是安装时解压、首启略慢）。
            packaging {
                jniLibs {
                    useLegacyPackaging = true
                }
            }
        }
        getByName("release") {
            isMinifyEnabled = true
            packaging {
                jniLibs {
                    useLegacyPackaging = true
                }
            }
            proguardFiles(
                *fileTree(".") { include("**/*.pro") }
                    .plus(getDefaultProguardFile("proguard-android-optimize.txt"))
                    .toList().toTypedArray()
            )
        }
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }
    buildFeatures {
        buildConfig = true
    }
}

rust {
    rootDirRel = "../../../"
}

dependencies {
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-process:2.10.0")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.4")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.0")
}

apply(from = "tauri.build.gradle.kts")