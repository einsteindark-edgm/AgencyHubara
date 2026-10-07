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
import com.hubara.operator.core.network.config.ServerConfig
import okhttp3.Interceptor
import okhttp3.OkHttpClient

/**
 * Configuración de la app. Lee SIEMPRE la [ServerConfig] vigente: la dirección del backend y Cognito llegan del servidor
 * (`mobile/config.json`) y pueden cambiar sin publicar otra versión. [devModeAllowed]: si sin Cognito se entra sin
 * login (solo el build debug contra el backend local).
 */
class ApiConfig(private val current: () -> ServerConfig, val devModeAllowed: Boolean = false) {

    /** Configuración fija (tests y builds sin configuración remota). */
    constructor(baseUrl: HttpUrl, cognitoClientId: String, cognitoRegion: String, devModeAllowed: Boolean = false) :
        this(ServerConfig(baseUrl, cognitoClientId, cognitoRegion).let { fixed -> { fixed } }, devModeAllowed)

    val server: ServerConfig get() = current()
    val baseUrl: HttpUrl get() = current().apiBaseUrl
    val cognitoClientId: String get() = current().cognitoClientId
    val cognitoRegion: String get() = current().cognitoRegion
    val cognitoEnabled: Boolean get() = cognitoClientId.isNotBlank()
    val cognitoEndpoint: HttpUrl get() = "https://cognito-idp.$cognitoRegion.amazonaws.com/".toHttpUrl()

    /**
     * Las fotos del historial llegan como ruta relativa (`/api/dashboard/media/...`). Solo se resuelven
     * contra NUESTRO backend: una URL absoluta de otro host se descarta (no se le manda el token a nadie más).
     */
    fun mediaUrl(path: String?): String? {
        val base = baseUrl
        if (path.isNullOrBlank()) return null
        val resolved = base.resolve(path) ?: return null
        return resolved.takeIf { it.host == base.host && it.port == base.port }?.toString()
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

/**
 * Retrofit nace con esta dirección comodín y el interceptor la cambia, en cada llamada, por el backend vigente
 * ([ApiConfig.baseUrl]): si el servidor se muda, la siguiente llamada ya va al nuevo.
 */
val PLACEHOLDER_BASE_URL: HttpUrl = "https://api.hubara.invalid/".toHttpUrl()

/** La URL comodín apuntando al backend vigente (con su ruta base, si la tuviera). */
internal fun HttpUrl.toServer(base: HttpUrl): HttpUrl = base.newBuilder()
    .encodedPath(base.encodedPath.trimEnd('/') + encodedPath)
    .encodedQuery(encodedQuery)
    .build()

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides @Singleton
    fun okHttp(tokens: AccessTokenProvider, config: ApiConfig): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        // Las llamadas de Retrofit van al backend vigente; el token va SOLO a él (Coil usa este mismo cliente y una
        // foto de otro host no lo recibe).
        .addInterceptor(Interceptor { chain ->
            val base = config.baseUrl
            var request = chain.request()
            if (request.url.host == PLACEHOLDER_BASE_URL.host) request = request.newBuilder().url(request.url.toServer(base)).build()
            val ours = request.url.host == base.host && request.url.port == base.port
            val token = tokens.currentAccessToken()
            if (ours && !token.isNullOrBlank()) request = request.newBuilder().header("Authorization", "Bearer $token").build()
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
        OperatorService.retrofit(client, PLACEHOLDER_BASE_URL).create(OperatorApi::class.java)

    @Provides @Singleton
    fun service(api: OperatorApi): OperatorService = OperatorService(api)

    @Provides @Singleton
    fun eventStream(client: OkHttpClient, config: ApiConfig, api: OperatorApi): EventStream =
        EventStream(client, { config.baseUrl }, api)

    /** Cognito va sin el interceptor de auth: son llamadas anónimas al IDP. */
    @Provides @Singleton
    fun cognito(config: ApiConfig): CognitoClient =
        CognitoClient(OkHttpClient.Builder().callTimeout(20, TimeUnit.SECONDS).build(), { config.cognitoEndpoint }, { config.cognitoClientId })
}
