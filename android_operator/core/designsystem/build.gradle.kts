plugins {
    id("hubara.android.library.compose")
}

android {
    namespace = "com.hubara.operator.core.designsystem"
}

dependencies {
    api(libs.androidx.compose.material3)
    api(libs.androidx.compose.material.icons.core)
}
