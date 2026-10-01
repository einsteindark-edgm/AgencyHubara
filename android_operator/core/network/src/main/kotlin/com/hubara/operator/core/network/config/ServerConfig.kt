package com.hubara.operator.core.network.config

import com.hubara.operator.core.network.OperatorJson
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Lo que la app necesita del servidor para arrancar. Llega en `mobile/config.json` (lo publica el deploy del dashboard
 * con los valores de Terraform), así la dirección del backend no va fija en el APK.
 */
data class ServerConfig(
    val apiBaseUrl: HttpUrl,
    val cognitoClientId: String,
    val cognitoRegion: String,
    val privacyUrl: String? = null,
    /** Si la app instalada tiene un versionCode menor, pide actualizarse. */
    val minVersionCode: Int = 0,
)

@Serializable
private data class ServerConfigDto(
    @SerialName("api_base_url") val apiBaseUrl: String? = null,
    @SerialName("cognito_region") val cognitoRegion: String? = null,
    @SerialName("cognito_client_id") val cognitoClientId: String? = null,
    @SerialName("privacy_url") val privacyUrl: String? = null,
    @SerialName("min_version_code") val minVersionCode: Int = 0,
)

/** En debug, la única excepción a https: el backend de prueba en el emulador o la Mac. */
private val CLEARTEXT_HOSTS = setOf("10.0.2.2", "localhost", "127.0.0.1")

private fun HttpUrl.isTrusted(allowCleartext: Boolean) = isHttps || (allowCleartext && host in CLEARTEXT_HOSTS)

/**
 * Lee y valida la configuración del servidor. null si no es JSON, si falta la dirección del backend o si no es https
 * (ahí va el token). Lo que no se reconoce se ignora: el servidor puede agregar campos sin romper versiones viejas.
 */
fun parseServerConfig(json: String, allowCleartext: Boolean): ServerConfig? {
    val dto = runCatching { OperatorJson.decodeFromString(ServerConfigDto.serializer(), json) }.getOrNull() ?: return null
    val base = dto.apiBaseUrl?.takeIf { it.isNotBlank() }?.toHttpUrlOrNull()?.takeIf { it.isTrusted(allowCleartext) } ?: return null
    return ServerConfig(
        apiBaseUrl = base,
        cognitoClientId = dto.cognitoClientId.orEmpty(),
        cognitoRegion = dto.cognitoRegion?.takeIf { it.isNotBlank() } ?: "us-east-1",
        privacyUrl = dto.privacyUrl?.toHttpUrlOrNull()?.takeIf { it.isHttps }?.toString(),
        minVersionCode = dto.minVersionCode.coerceAtLeast(0),
    )
}
