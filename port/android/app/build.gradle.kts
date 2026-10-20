plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// the port/ directory this project lives in, and the Azahar tree the core is built from
val portDir = rootDir.parentFile.absolutePath
val azaharDir = file(providers.gradleProperty("azahar.dir").get()).absolutePath

android {
    namespace = "org.mh4u.port"
    compileSdk = 35
    ndkVersion = "27.3.13750724"

    defaultConfig {
        applicationId = "org.mh4u.port"
        minSdk = 29
        targetSdk = 35
        // CI passes its run number, so every build counts as newer than the last
        versionCode = System.getenv("MH4U_VERSION_CODE")?.toIntOrNull() ?: 1
        versionName = "0.1"
        ndk { abiFilters += listOf("arm64-v8a") }
        externalNativeBuild {
            cmake {
                arguments(
                    "-DMH4U_PORT_DIR=$portDir",
                    "-DENABLE_QT=0", "-DENABLE_SDL2=0", "-DENABLE_TESTS=0", "-DENABLE_ROOM=0",
                    "-DENABLE_SCRIPTING=0", "-DENABLE_GDBSTUB=OFF", "-DENABLE_OPENGL=OFF",
                    "-DENABLE_VULKAN=ON", "-DANDROID_ARM_NEON=true",
                    "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON", "-DCITRA_WARNINGS_AS_ERRORS=OFF",
                )
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("$azaharDir/CMakeLists.txt")
            version = "3.25.0+"
        }
    }

    // One signing key for every build, so each APK can update the last one (SIGNING.md). The key
    // comes from the environment (CI: GitHub secrets); without it the debug key is used.
    signingConfigs {
        val keystore = System.getenv("MH4U_KEYSTORE_FILE")
        if (!keystore.isNullOrEmpty() && file(keystore).exists()) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("MH4U_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MH4U_KEY_ALIAS")
                keyPassword = System.getenv("MH4U_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging { jniLibs.useLegacyPackaging = true }   // the code library is dlopen'ed from files
}

dependencies {
    val bom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(bom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
}
