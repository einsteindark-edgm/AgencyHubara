// Pantallas definidas por el servidor: el ViewModel genérico, el render de cada componente del catálogo y las
// entradas de navegación. El contrato y la validación viven en :core:sdui.
plugins {
    id("hubara.android.feature")
}

android {
    namespace = "com.hubara.operator.feature.screens"
}

dependencies {
    implementation(project(":core:sdui"))
    implementation(libs.coil.compose)
    testImplementation(libs.kotlinx.coroutines.test)
}

// RealScreensTest carga las pantallas reales del repo (android_operator/screens/).
tasks.withType<Test>().configureEach {
    val screens = rootProject.layout.projectDirectory.dir("screens")
    inputs.dir(screens).withPathSensitivity(PathSensitivity.RELATIVE)
    systemProperty("screens.dir", screens.asFile.absolutePath)
}
