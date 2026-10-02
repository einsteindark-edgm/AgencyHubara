plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.hubara.operator.core.data"
}

dependencies {
    api(project(":core:model"))
    api(project(":core:network"))
    api(project(":core:database"))
    api(project(":core:sdui"))
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.tink.android)
    api(libs.androidx.work.runtime)
    implementation(libs.androidx.hilt.work)
    ksp(libs.androidx.hilt.compiler)
    implementation(libs.androidx.lifecycle.process)
    testImplementation(libs.androidx.work.testing)
    testImplementation(libs.okhttp.mockwebserver)
}
