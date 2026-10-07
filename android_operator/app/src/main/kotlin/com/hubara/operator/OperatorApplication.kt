package com.hubara.operator

import android.app.Application
import androidx.hilt.work.HiltWorkerFactory
import androidx.work.Configuration
import com.hubara.operator.core.push.Channels
import com.hubara.operator.core.push.PushRegistrar
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
import com.hubara.operator.core.network.config.ServerConfig
import com.hubara.operator.core.data.config.ServerConfigStore
import com.hubara.operator.core.data.config.ServerConfigDefaults

@HiltAndroidApp
class OperatorApplication : Application(), Configuration.Provider, SingletonImageLoader.Factory {
    @Inject lateinit var workerFactory: HiltWorkerFactory
    @Inject lateinit var okHttp: OkHttpClient
    @Inject lateinit var push: PushRegistrar

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().setWorkerFactory(workerFactory).build()

    override fun onCreate() {
        super.onCreate()
        Channels.register(this)
        // Firebase con las últimas opciones del servidor: si un push despertó el proceso, ya puede entregarlo.
        push.startFromCache()
        // Respaldo del push: incendios graves y widget cada 15 min aunque un push no llegue.
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
    /** La configuración del servidor baja de CONFIG_URL; lo del build es el respaldo mientras no llegue. */
    @Provides @Singleton
    fun serverConfigDefaults(): ServerConfigDefaults = ServerConfigDefaults(
        configUrl = BuildConfig.CONFIG_URL,
        fallback = ServerConfig(
            apiBaseUrl = normalizeBaseUrl(BuildConfig.API_URL),
            cognitoClientId = BuildConfig.COGNITO_CLIENT_ID,
            cognitoRegion = BuildConfig.COGNITO_REGION,
            privacyUrl = BuildConfig.PRIVACY_URL.ifBlank { null },
        ),
        // http solo hacia el backend de prueba en el emulador o la Mac, y solo en debug.
        allowCleartext = BuildConfig.DEBUG,
    )

    /** Lee siempre la configuración vigente: si el servidor cambia de dirección, la siguiente llamada va al nuevo. */
    @Provides @Singleton
    fun apiConfig(store: ServerConfigStore): ApiConfig =
        // Sin login solo en debug (backend local); un release sin client id pide login.
        ApiConfig({ store.current.value }, devModeAllowed = BuildConfig.DEBUG)
}
