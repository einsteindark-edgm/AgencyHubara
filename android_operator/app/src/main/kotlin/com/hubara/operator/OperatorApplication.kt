package com.hubara.operator

import android.app.Application
import androidx.hilt.work.HiltWorkerFactory
import androidx.work.Configuration
import com.hubara.operator.core.push.Channels
import com.hubara.operator.core.push.VigiaWorker
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import okhttp3.OkHttpClient
import com.hubara.operator.core.network.di.ApiConfig
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.HiltAndroidApp
import dagger.hilt.components.SingletonComponent
import javax.inject.Inject
import javax.inject.Singleton
import okhttp3.HttpUrl.Companion.toHttpUrl

@HiltAndroidApp
class OperatorApplication : Application(), Configuration.Provider, SingletonImageLoader.Factory {
    @Inject lateinit var workerFactory: HiltWorkerFactory
    @Inject lateinit var okHttp: OkHttpClient

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().setWorkerFactory(workerFactory).build()

    override fun onCreate() {
        super.onCreate()
        Channels.register(this)
        // Con la app cerrada: incendios graves y widget cada 15 min, hasta que llegue FCM.
        VigiaWorker.schedule(this)
    }

    /** Coil usa el mismo cliente HTTP (con el token): las fotos del chat están detrás de auth. */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context).components { add(OkHttpNetworkFetcherFactory(callFactory = { okHttp })) }.build()
}

/** Retrofit exige que la URL base termine en `/`. */
fun normalizeBaseUrl(raw: String) = raw.trimEnd('/').plus("/").toHttpUrl()

@Module
@InstallIn(SingletonComponent::class)
object AppModule {
    @Provides @Singleton
    fun apiConfig(): ApiConfig = ApiConfig(
        baseUrl = normalizeBaseUrl(BuildConfig.API_URL),
        cognitoClientId = BuildConfig.COGNITO_CLIENT_ID,
        cognitoRegion = BuildConfig.COGNITO_REGION,
        // Sin login solo en debug (backend local); un release sin client id pide login.
        devModeAllowed = BuildConfig.DEBUG,
    )
}
