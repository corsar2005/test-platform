plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val generatedOfflineAssets = layout.buildDirectory.dir("generated/offlineAssets")
val prepareOfflineAssets = tasks.register<Sync>("prepareOfflineAssets") {
    from("../../static") {
        exclude("offline-question-bank.js")
    }
    from("../../static/offline-question-bank.js") {
        rename { "offline-question-bank-runtime.js" }
        filter { line: String -> "window.OFFLINE_QUESTION_BANK=$line;" }
    }
    into(generatedOfflineAssets)
}

android {
    namespace = "ru.testplatform.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.testplatform.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.1.0"
    }

    sourceSets.getByName("main").assets.srcDir(generatedOfflineAssets)

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    tasks.named("preBuild").configure {
        dependsOn(prepareOfflineAssets)
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}
