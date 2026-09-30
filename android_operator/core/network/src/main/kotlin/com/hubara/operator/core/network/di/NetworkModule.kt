package com.hubara.operator.core.network.di

import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.auth.CognitoClient
import com.hubara.operator.core.network.sse.EventStream
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import java.util.concurrent.TimeUnit
import javax.inject.Singleton
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.OkHttpClient

/** Configuración de la app (la provee `:app` desde BuildConfig). */
data class ApiConfig(val baseUrl: HttpUrl, val cognitoClientId: String, val cognitoRegion: String) {
    val cognitoEnabled: Boolean get() = cognitoClientId.isNotBlank()
    val cognitoEndpoint: HttpUrl get() = "https://cognito-idp.$cognitoRegion.amazonaws.com/".toHttpUrl()

    /**
     * Las fotos del historial llegan como ruta relativa (`/api/dashboard/media/...`). Solo se resuelven
     * contra NUESTRO backend: una URL absoluta de otro host se descarta (no se le manda el token a nadie más).
     */
    fun mediaUrl(path: String?): String? {
        if (path.isNullOrBlank()) return null
        val resolved = baseUrl.resolve(path) ?: return null
        return resolved.takeIf { it.host == baseUrl.host && it.port == baseUrl.port }?.toString()
    }
}

/** Da el access token vigente (o null en modo dev). Lo implementa `:core:data`. */
interface AccessTokenProvider {
    fun currentAccessToken(): String?

    /**
     * Refresca con el refresh token porque el servidor rechazó [rejectedToken]. Bloqueante: lo llama el
     * Authenticator de OkHttp en su hilo. Si otra llamada ya refrescó, devuelve el token nuevo sin pedir otro.
     */
    fun refreshAccessTokenBlocking(rejectedToken: String?): String? = null
}

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides @Singleton
    fun okHttp(tokens: AccessTokenProvider): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .addInterceptor(Interceptor { chain ->
            val token = tokens.currentAccessToken()
            val request = if (token.isNullOrBlank()) chain.request()
            else chain.request().newBuilder().header("Authorization", "Bearer $token").build()
            chain.proceed(request)
        })
        // Un 401 con token: se refresca una vez y se reintenta. Si el refresh falla, la sesión se cierra.
        .authenticator { _, response ->
            val hadToken = response.request.header("Authorization") != null
            if (!hadToken || response.priorResponse != null) return@authenticator null
            val rejected = response.request.header("Authorization")?.removePrefix("Bearer ")
            tokens.refreshAccessTokenBlocking(rejected)?.let { fresh ->
                response.request.newBuilder().header("Authorization", "Bearer $fresh").build()
            }
        }
        .build()

    @Provides @Singleton
    fun api(client: OkHttpClient, config: ApiConfig): OperatorApi =
        OperatorService.retrofit(client, config.baseUrl).create(OperatorApi::class.java)

    @Provides @Singleton
    fun service(api: OperatorApi): OperatorService = OperatorService(api)

    @Provides @Singleton
    fun eventStream(client: OkHttpClient, config: ApiConfig, api: OperatorApi): EventStream =
        EventStream(client, config.baseUrl, api)

    /** Cognito va sin el interceptor de auth: son llamadas anónimas al IDP. */
    @Provides @Singleton
    fun cognito(config: ApiConfig): CognitoClient =
        CognitoClient(OkHttpClient.Builder().callTimeout(20, TimeUnit.SECONDS).build(), config.cognitoEndpoint, config.cognitoClientId)
}
