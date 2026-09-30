plugins {
    id("hubara.android.library.compose")
}

android {
    namespace = "com.hubara.operator.core.ui"
}

dependencies {
    api(project(":core:model"))
    api(project(":core:designsystem"))
    api(libs.kotlinx.collections.immutable)
    implementation(libs.androidx.navigationevent.compose)
}
