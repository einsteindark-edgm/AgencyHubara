plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.hubara.operator.core.network"
}

dependencies {
    api(project(":core:model"))
    api(libs.okhttp)
    api(libs.kotlinx.coroutines.core)
    implementation(libs.okhttp.sse)
    api(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    testImplementation(libs.okhttp.mockwebserver)
}
