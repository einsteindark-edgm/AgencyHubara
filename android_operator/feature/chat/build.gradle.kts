plugins {
    id("hubara.android.feature")
}

android {
    namespace = "com.hubara.operator.feature.chat"
}

dependencies {
    implementation(libs.coil.compose)
}
