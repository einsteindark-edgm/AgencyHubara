// Motor de pantallas definidas por el servidor (Server-Driven UI): el contrato, el lenguaje de enlace de datos y la
// validación. Kotlin puro: se prueba en la JVM y también valida los archivos de `android_operator/screens/` en la CI.
plugins {
    id("hubara.jvm.library")
}

dependencies {
    api(libs.kotlinx.serialization.json)
}

// ScreensRepoTest valida cada pantalla del repo y que el esquema del editor esté al día.
// `-PupdateScreens=true` regenera screen.schema.json, app.schema.json y CATALOGO.md desde el catálogo.
val screensDir = rootProject.layout.projectDirectory.dir("screens")
val updateScreens = providers.gradleProperty("updateScreens").orElse("false")
tasks.test {
    inputs.dir(screensDir).withPathSensitivity(PathSensitivity.RELATIVE)
    inputs.dir(rootProject.layout.projectDirectory.dir("e2e/screens")).withPathSensitivity(PathSensitivity.RELATIVE)
    inputs.property("updateScreens", updateScreens)
    systemProperty("screens.dir", screensDir.asFile.absolutePath)
    systemProperty("screens.update", updateScreens.get())
    if (updateScreens.get() == "true") outputs.upToDateWhen { false }
}
