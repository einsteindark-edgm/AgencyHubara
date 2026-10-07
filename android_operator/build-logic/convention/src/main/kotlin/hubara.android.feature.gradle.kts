// Funcionalidad: pantallas Compose + ViewModels con Hilt + entradas de navegación.
plugins {
    id("hubara.android.library.compose")
    id("hubara.android.hilt")
    id("org.jetbrains.kotlin.plugin.serialization")
}

val catalog = extensions.getByType<VersionCatalogsExtension>().named("libs")
fun lib(alias: String) = catalog.findLibrary(alias).get()

dependencies {
    "implementation"(project(":core:model"))
    "implementation"(project(":core:navigation"))
    "implementation"(project(":core:designsystem"))
    "implementation"(project(":core:ui"))
    "implementation"(project(":core:data"))
    "implementation"(lib("androidx-lifecycle-runtime-compose"))
    "implementation"(lib("androidx-lifecycle-viewmodel-compose"))
    "implementation"(lib("androidx-lifecycle-viewmodel-navigation3"))
    "implementation"(lib("androidx-hilt-lifecycle-viewmodel-compose"))
    "implementation"(lib("androidx-navigation3-runtime"))
    "implementation"(lib("androidx-navigation3-ui"))
    "implementation"(lib("androidx-compose-material3-adaptive-navigation3"))
    "implementation"(lib("kotlinx-collections-immutable"))
    "implementation"(lib("kotlinx-serialization-json"))
}
