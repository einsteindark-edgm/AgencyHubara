import com.android.build.api.variant.BuildConfigField

plugins {
    id("hubara.android.application")
    id("hubara.android.hilt")
}

android {
    namespace = "com.hubara.operator"
    defaultConfig {
        applicationId = "com.hubara.operator"
        versionCode = 1
        versionName = "0.1.0"
    }
}

// AGP 9: los campos de BuildConfig van por la API de variantes (skill agp-9-upgrade, BuildConfig).
val apiUrl = providers.gradleProperty("hubara.apiUrl").getOrElse("http://10.0.2.2:8000")
val cognitoClientId = providers.gradleProperty("hubara.cognitoClientId").getOrElse("")
val cognitoRegion = providers.gradleProperty("hubara.cognitoRegion").getOrElse("us-east-1")

androidComponents {
    onVariants { variant ->
        variant.buildConfigFields?.put("API_URL", BuildConfigField("String", "\"$apiUrl\"", "URL del backend"))
        variant.buildConfigFields?.put("COGNITO_CLIENT_ID", BuildConfigField("String", "\"$cognitoClientId\"", "Vacío = modo dev sin login"))
        variant.buildConfigFields?.put("COGNITO_REGION", BuildConfigField("String", "\"$cognitoRegion\"", "Región del user pool"))
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
    implementation(project(":feature:inbox"))
    implementation(project(":feature:chat"))
    implementation(project(":feature:fires"))
    implementation(project(":feature:orders"))
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
