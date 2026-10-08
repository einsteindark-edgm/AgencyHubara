plugins {
    id("hubara.android.library")
    id("hubara.android.hilt")
}

android {
    namespace = "com.hubara.operator.widget.hot"
}

dependencies {
    implementation(project(":core:push"))
    implementation(libs.androidx.core.ktx)
    // Colecciones de RemoteViews con los datos adentro (RemoteCollectionItems): en Android 12+ las nativas; en el
    // Android 11 del operador, por el RemoteViewsService de la librería. Sin servicio ni fábrica propios.
    implementation(libs.androidx.core.remoteviews)
    implementation(libs.kotlinx.coroutines.android)
}
