package com.hubara.operator.core.data.config

import android.content.Context
import com.hubara.operator.core.network.config.ServerConfig
import com.hubara.operator.core.network.config.parseServerConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request

/** De dónde baja la configuración ([configUrl], vacío = no hay) y qué usar mientras tanto ([fallback], del build). */
data class ServerConfigDefaults(val configUrl: String, val fallback: ServerConfig, val allowCleartext: Boolean)

/**
 * La configuración del servidor (`mobile/config.json`): a qué backend ir, Cognito, la política de privacidad y la
 * versión mínima. Arranca con la última buena guardada (o la del build) y la renueva del servidor. Lo inválido o la
 * falta de red nunca borran lo que funcionaba.
 */
@Singleton
class ServerConfigStore @Inject constructor(
    @ApplicationContext context: Context,
    private val defaults: ServerConfigDefaults,
) {
    // SharedPreferences y no DataStore: el valor guardado hace falta SÍNCRONO al crear el grafo de Hilt (antes de
    // la primera llamada). No es secreto, y no entra en copias de seguridad (allowBackup=false).
    private val prefs = context.getSharedPreferences("server_config", Context.MODE_PRIVATE)
    private val http = OkHttpClient.Builder().callTimeout(8, TimeUnit.SECONDS).build()

    private val _current = MutableStateFlow(saved() ?: defaults.fallback)
    val current: StateFlow<ServerConfig> = _current.asStateFlow()

    val hasSaved: Boolean get() = prefs.contains(KEY)

    /**
     * Al abrir la app. Sin configuración guardada espera la primera (todavía no sabe a qué servidor ir); con una
     * guardada arranca ya y la renueva en segundo plano.
     */
    suspend fun start(scope: CoroutineScope) {
        if (hasSaved) scope.launch { refresh() } else refresh()
    }

    /** Baja la configuración del servidor; true si llegó una válida (y ya está aplicada y guardada). */
    suspend fun refresh(): Boolean = withContext(Dispatchers.IO) {
        if (defaults.configUrl.isBlank()) return@withContext false
        val body = runCatching {
            http.newCall(Request.Builder().url(defaults.configUrl).header("Cache-Control", "no-cache").build()).execute()
                .use { if (it.isSuccessful) it.body.string() else null }
        }.getOrNull() ?: return@withContext false
        val parsed = parseServerConfig(body, defaults.allowCleartext) ?: return@withContext false
        prefs.edit().putString(KEY, body).apply()
        _current.value = parsed.orBuild()
        true
    }

    /** De dónde bajan las pantallas del servidor: lo que diga config.json o, si no, `screens/` junto a él. */
    fun screensBase(): HttpUrl? = current.value.screensUrl ?: defaults.configUrl.toHttpUrlOrNull()?.resolve("screens/")

    private fun saved(): ServerConfig? =
        prefs.getString(KEY, null)?.let { parseServerConfig(it, defaults.allowCleartext) }?.orBuild()

    /** Lo que el servidor no manda sale del build. */
    private fun ServerConfig.orBuild() = copy(
        cognitoClientId = cognitoClientId.ifBlank { defaults.fallback.cognitoClientId },
        privacyUrl = privacyUrl ?: defaults.fallback.privacyUrl,
    )

    private companion object {
        const val KEY = "config_json"
    }
}
