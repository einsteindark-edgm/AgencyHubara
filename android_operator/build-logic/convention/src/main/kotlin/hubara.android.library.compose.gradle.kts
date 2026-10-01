// Biblioteca Android con Compose (Material 3 por el BOM).
plugins {
    id("hubara.android.library")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    buildFeatures { compose = true }
}

// :core:model es Kotlin puro: sin esto sus tipos cuentan como inestables y la bandeja, el chat y las tarjetas se
// recomponen en cada emisión aunque sus datos no cambien.
composeCompiler {
    stabilityConfigurationFiles.add(rootProject.layout.projectDirectory.file("compose-stability.conf"))
}

val catalog = extensions.getByType<VersionCatalogsExtension>().named("libs")
fun lib(alias: String) = catalog.findLibrary(alias).get()

dependencies {
    val bom = platform(lib("androidx-compose-bom"))
    "implementation"(bom)
    "implementation"(lib("androidx-compose-ui"))
    "implementation"(lib("androidx-compose-foundation"))
    "implementation"(lib("androidx-compose-material3"))
    "implementation"(lib("androidx-compose-ui-tooling-preview"))
    // activity 1.13+: ComponentActivity provee el NavigationEventDispatcher (retroceso predictivo).
    "implementation"(lib("androidx-activity-compose"))
    "debugImplementation"(lib("androidx-compose-ui-tooling"))
    "debugImplementation"(lib("androidx-compose-ui-test-manifest"))
    "testImplementation"(bom)
    "testImplementation"(lib("androidx-compose-ui-test-junit4"))
}
