plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.hubara.operator.core.push"
}

dependencies {
    api(project(":core:data"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.hilt.work)
    ksp(libs.androidx.hilt.compiler)
    implementation(libs.androidx.lifecycle.process)
    testImplementation(libs.androidx.work.testing)
}
