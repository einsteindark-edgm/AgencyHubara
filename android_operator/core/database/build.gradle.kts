plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
    alias(libs.plugins.room)
}

android {
    namespace = "com.hubara.operator.core.database"
}

// MigrationTestHelper lee los esquemas exportados como assets de los tests. Con el tipo de la DSL nueva de AGP 9:
// el accesor `android.sourceSets` del script devuelve el tipo viejo y revienta con ClassCastException.
extensions.configure<com.android.build.api.dsl.LibraryExtension> {
    sourceSets.getByName("test").assets.directories.add("$projectDir/schemas")
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
