// La app: mismos SDKs que las bibliotecas, Compose, y R8 completo en release (skill r8-analyzer).
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.tasks.KotlinJvmCompile

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    compileSdk = 37
    defaultConfig {
        minSdk = 30
        targetSdk = 36
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    buildTypes {
        getByName("release") {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    testOptions {
        unitTests.isIncludeAndroidResources = true
    }
}

tasks.withType<KotlinJvmCompile>().configureEach {
    compilerOptions.jvmTarget.set(JvmTarget.JVM_17)
}

tasks.withType<Test>().configureEach {
    jvmArgs(
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.util=ALL-UNNAMED",
        "--add-exports=java.base/jdk.internal.access=ALL-UNNAMED",
        "--add-opens=java.base/jdk.internal.access=ALL-UNNAMED",
    )
}

val catalog = extensions.getByType<VersionCatalogsExtension>().named("libs")
fun lib(alias: String) = catalog.findLibrary(alias).get()

dependencies {
    val bom = platform(lib("androidx-compose-bom"))
    "implementation"(bom)
    "implementation"(lib("androidx-compose-ui"))
    "implementation"(lib("androidx-compose-material3"))
    "implementation"(lib("androidx-activity-compose"))
    "debugImplementation"(lib("androidx-compose-ui-tooling"))
    "debugImplementation"(lib("androidx-compose-ui-test-manifest"))
    "testImplementation"(bom)
    "testImplementation"(lib("androidx-compose-ui-test-junit4"))
    "testImplementation"(lib("junit"))
    "testImplementation"(lib("truth"))
    "testImplementation"(lib("robolectric"))
    "testImplementation"(lib("androidx-test-core"))
    "testImplementation"(lib("androidx-test-ext-junit"))
    "testImplementation"(lib("kotlinx-coroutines-test"))
}
