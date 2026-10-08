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
    implementation(project(":core:ui"))           // listTimeLabel: la misma hora que la bandeja
    implementation(project(":core:designsystem"))  // la paleta de la marca (Android 11 no tiene colores dinámicos)
    implementation(libs.androidx.glance.appwidget)
    implementation(libs.androidx.glance.material3)
    testImplementation(libs.androidx.glance.appwidget.testing)
}
