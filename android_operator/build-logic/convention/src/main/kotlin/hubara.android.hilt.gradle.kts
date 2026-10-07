// Hilt con KSP (AGP 9: nada de kapt). Se aplica después de un plugin de Android.
plugins {
    id("com.google.devtools.ksp")
    id("com.google.dagger.hilt.android")
}

val catalog = extensions.getByType<VersionCatalogsExtension>().named("libs")
fun lib(alias: String) = catalog.findLibrary(alias).get()

dependencies {
    "implementation"(lib("hilt-android"))
    "ksp"(lib("hilt-compiler"))
    "testImplementation"(lib("hilt-android-testing"))
    "kspTest"(lib("hilt-compiler"))
}
