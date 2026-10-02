import com.android.build.api.variant.BuildConfigField

plugins {
    id("hubara.android.application")
    id("hubara.android.hilt")
}

// AGP 9: los campos de BuildConfig van por la API de variantes (skill agp-9-upgrade, BuildConfig).
val apiUrl = providers.gradleProperty("hubara.apiUrl").getOrElse("http://10.0.2.2:8000")
val cognitoClientId = providers.gradleProperty("hubara.cognitoClientId").getOrElse("")
val cognitoRegion = providers.gradleProperty("hubara.cognitoRegion").getOrElse("us-east-1")
val privacyUrl = providers.gradleProperty("hubara.privacyUrl").getOrElse("")
// De dónde baja la app la configuración del servidor (backend, Cognito, privacidad, versión mínima). Es lo único fijo
// en el APK: si la IP del backend cambia, la app se entera por aquí. Vacío = usa los valores de arriba (debug local).
val configUrl = providers.gradleProperty("hubara.configUrl").getOrElse("")

// Clave de subida a Google Play: vive FUERA del repo (es público). Se configura en ~/.gradle/gradle.properties;
// receta en docs/mobile-native/publicar-en-google-play.html.
val uploadStoreFile = providers.gradleProperty("hubara.upload.storeFile").orNull
val uploadStorePassword = providers.gradleProperty("hubara.upload.storePassword").orNull
val uploadKeyAlias = providers.gradleProperty("hubara.upload.keyAlias").orNull
val uploadKeyPassword = providers.gradleProperty("hubara.upload.keyPassword").orNull

android {
    namespace = "com.hubara.operator"
    defaultConfig {
        applicationId = "com.hubara.operator"
        // Cada .aab que se sube a Google Play necesita un versionCode MAYOR que el anterior (nunca se reutiliza).
        // versionName es lo que ve el operador. Se suben juntos en cada release.
        versionCode = 1
        versionName = "1.0.0"
    }
    signingConfigs {
        if (uploadStoreFile != null) {
            create("upload") {
                storeFile = file(uploadStoreFile)
                storePassword = uploadStorePassword
                keyAlias = uploadKeyAlias
                keyPassword = uploadKeyPassword
            }
        }
    }
    buildTypes {
        getByName("release") {
            if (uploadStoreFile != null) signingConfig = signingConfigs.getByName("upload")
        }
    }
}

// El paquete para Google Play (bundleRelease) falla cerrado: sin HTTPS, sin Cognito o sin clave de subida no se arma.
// La CI compila `assembleRelease` con los valores de desarrollo solo para probar R8; eso no pasa por aquí.
val verifyPlayRelease by tasks.registering {
    group = "publishing"
    description = "Revisa que el .aab para Google Play apunte a producción y vaya firmado con la clave de subida."
    val url = apiUrl
    val remote = configUrl
    val clientId = cognitoClientId
    val store = uploadStoreFile
    val alias = uploadKeyAlias
    val privacy = privacyUrl
    doLast {
        val problems = buildList {
            if (remote.isNotBlank()) {
                if (!remote.startsWith("https://")) add("hubara.configUrl tiene que ser https:// (hoy: $remote)")
            } else {
                // Sin configuración remota, la dirección y Cognito quedan fijos en el APK.
                if (!url.startsWith("https://")) add("hubara.apiUrl tiene que ser https:// (hoy: $url), o define hubara.configUrl")
                if (clientId.isBlank()) add("falta hubara.cognitoClientId (sin él la app no deja entrar), o define hubara.configUrl")
            }
            if (store == null || !File(store).isFile) add("falta la clave de subida: hubara.upload.storeFile no apunta a un archivo")
            if (alias.isNullOrBlank()) add("falta hubara.upload.keyAlias")
            if (!privacy.startsWith("https://")) add("falta hubara.privacyUrl (Google Play exige la política de privacidad dentro de la app)")
        }
        if (problems.isNotEmpty()) {
            throw GradleException(
                "El paquete para Google Play no se puede armar:\n- " + problems.joinToString("\n- ") +
                    "\nReceta: docs/mobile-native/publicar-en-google-play.html",
            )
        }
    }
}
tasks.matching { it.name == "bundleRelease" }.configureEach { dependsOn(verifyPlayRelease) }

// Las pantallas del servidor del repo viajan en el APK (`assets/screens/`): sirven sin red desde la primera vez. Los
// esquemas del editor (`*.schema.json`) no viajan. La tarea está en build-logic (ver ahí por qué).
val bundleServerScreens = tasks.register<BundleServerScreens>("bundleServerScreens") {
    screens.from(rootProject.fileTree("screens") { include("*.json"); exclude("*.schema.json") })
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(bundleServerScreens, BundleServerScreens::outputDir)
        variant.buildConfigFields?.put("API_URL", BuildConfigField("String", "\"$apiUrl\"", "URL del backend"))
        variant.buildConfigFields?.put("COGNITO_CLIENT_ID", BuildConfigField("String", "\"$cognitoClientId\"", "Vacío = modo dev sin login"))
        variant.buildConfigFields?.put("COGNITO_REGION", BuildConfigField("String", "\"$cognitoRegion\"", "Región del user pool"))
        variant.buildConfigFields?.put("CONFIG_URL", BuildConfigField("String", "\"$configUrl\"", "Configuración del servidor; vacío = sin remota"))
        variant.buildConfigFields?.put("PRIVACY_URL", BuildConfigField("String", "\"$privacyUrl\"", "Política de privacidad pública; vacío = sin enlace"))
    }
}

dependencies {
    implementation(project(":core:model"))
    implementation(project(":core:navigation"))
    implementation(project(":core:network"))
    implementation(project(":core:data"))
    implementation(project(":core:designsystem"))
    implementation(project(":core:ui"))
    implementation(project(":feature:auth"))
    implementation(project(":feature:chat"))
    implementation(project(":feature:screens"))
    implementation(project(":core:sdui"))
    implementation(project(":core:push"))
    implementation(project(":widget:hot"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.hilt.lifecycle.viewmodel.compose)
    implementation(libs.androidx.hilt.work)
    ksp(libs.androidx.hilt.compiler)
    implementation(libs.androidx.work.runtime)
    implementation(libs.androidx.navigation3.ui)
    implementation(libs.androidx.compose.material3.adaptive.navigation.suite)
    implementation(libs.androidx.compose.material3.adaptive.navigation3)
    implementation(libs.kotlinx.collections.immutable)
    implementation(libs.coil.compose)
    implementation(libs.coil.network.okhttp)
}

dependencies {
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.androidx.work.testing)
    testImplementation(libs.hilt.android.testing)
    kspTest(libs.hilt.compiler)
}
