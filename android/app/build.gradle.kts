plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val releasePlatformUrl = providers.gradleProperty("testPlatformUrl").orNull?.trimEnd('/')
val releasePlatformUrlField = releasePlatformUrl
    ?.replace("\\", "\\\\")
    ?.replace("\"", "\\\"")
    ?.let { "\"$it\"" }
    ?: "\"\""

android {
    namespace = "ru.testplatform.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.testplatform.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
        buildConfigField("String", "PLATFORM_URL", "\"http://10.0.2.2:8000\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        release {
            buildConfigField("String", "PLATFORM_URL", releasePlatformUrlField)
        }
    }
}

tasks.configureEach {
    if (name == "preReleaseBuild") {
        doFirst {
            val url = releasePlatformUrl
                ?: throw GradleException("Set -PtestPlatformUrl to the deployed HTTPS platform URL for release builds")
            require(url.startsWith("https://")) { "Release platform URL must use HTTPS" }
        }
    }
}
