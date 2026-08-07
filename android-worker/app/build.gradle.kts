import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val swarmSigningKeystore = System.getenv("SWARM_SIGNING_KEYSTORE")

android {
    namespace = "com.camalabs.computeswarm"
    compileSdk = 35
    ndkVersion = "29.0.14206865"

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "com.camalabs.computeswarm"
        minSdk = 26
        targetSdk = 35
        versionCode = 5
        versionName = "0.5.0"

        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
            }
        }
    }

    if (!swarmSigningKeystore.isNullOrBlank()) {
        signingConfigs {
            create("swarmDebug") {
                storeFile = file(swarmSigningKeystore)
                storePassword = "android"
                keyAlias = "androiddebugkey"
                keyPassword = "android"
            }
        }
    }

    buildTypes {
        getByName("debug") {
            if (!swarmSigningKeystore.isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("swarmDebug")
            }
        }
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.google.ai.edge.litert:litert:2.1.6")
}
