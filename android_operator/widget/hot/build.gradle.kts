plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.hubara.operator.widget.hot"
    buildFeatures { compose = true }
}

dependencies {
    implementation(project(":core:push"))
    implementation(libs.androidx.glance.appwidget)
    implementation(libs.androidx.glance.material3)
}
