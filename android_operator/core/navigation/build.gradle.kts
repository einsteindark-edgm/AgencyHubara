plugins {
    id("hubara.android.library.compose")
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.hubara.operator.core.navigation"
}

dependencies {
    api(project(":core:model"))
    api(libs.androidx.navigation3.runtime)
    api(libs.androidx.navigation3.ui)
    implementation(libs.androidx.lifecycle.viewmodel.navigation3)
    implementation(libs.kotlinx.serialization.json)
}
