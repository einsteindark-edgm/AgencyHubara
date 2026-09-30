plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
    alias(libs.plugins.room)
}

android {
    namespace = "com.hubara.operator.core.database"
}

room {
    schemaDirectory("$projectDir/schemas")
}

dependencies {
    api(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)
    api(libs.kotlinx.coroutines.core)
    testImplementation(libs.androidx.room.testing)
}
