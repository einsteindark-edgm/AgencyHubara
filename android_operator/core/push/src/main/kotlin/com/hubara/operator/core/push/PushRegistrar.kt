package com.hubara.operator.core.push

import android.content.Context
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.dto.DeviceRequest
import com.hubara.operator.core.network.dto.FirebaseOptionsDto
import com.hubara.operator.core.network.dto.PushConfigDto
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Las opciones con las que el teléfono arranca Firebase (las cuatro de `google-services.json`). */
data class PushOptions(val projectId: String, val applicationId: String, val apiKey: String, val senderId: String)

fun FirebaseOptionsDto.toPushOptions() = PushOptions(projectId, applicationId, apiKey, gcmSenderId)

/** Firebase visto desde la app: arrancarlo y pedir o soltar el token. La de verdad es [FirebasePushTransport]. */
interface PushTransport {
    /** Arranca Firebase con [options]; false si no se puede (sin Google Play, o ya arrancó con otro proyecto). */
    fun start(options: PushOptions): Boolean
    suspend fun token(): String
    suspend fun deleteToken()
}

/** Las tres rutas del backend que usan los avisos. */
interface PushApi {
    suspend fun config(): PushConfigDto
    suspend fun register(body: DeviceRequest)
    suspend fun unregister(token: String)
}

class RetrofitPushApi @Inject constructor(private val api: OperatorApi) : PushApi {
    override suspend fun config(): PushConfigDto = api.pushConfig()

    override suspend fun register(body: DeviceRequest) {
        val response = api.registerDevice(body)
        check(response.isSuccessful) { "registrar el teléfono: HTTP ${response.code()}" }
    }

    override suspend fun unregister(token: String) {
        api.unregisterDevice(token)
    }
}

/**
 * Lo último que dijo el servidor (las opciones de Firebase) y el último token registrado. SharedPreferences y no
 * DataStore: las opciones hacen falta SÍNCRONAS en `Application.onCreate`, antes de que Firebase entregue el push
 * que despertó la app. No son secretas y no entran en copias de seguridad (allowBackup=false).
 */
class PushCache @Inject constructor(@ApplicationContext context: Context) {
    private val prefs = context.getSharedPreferences("push", Context.MODE_PRIVATE)

    fun options(): PushOptions? {
        val project = prefs.getString(PROJECT, null) ?: return null
        return PushOptions(
            projectId = project,
            applicationId = prefs.getString(APP, null) ?: return null,
            apiKey = prefs.getString(KEY, null) ?: return null,
            senderId = prefs.getString(SENDER, null) ?: return null,
        )
    }

    fun saveOptions(options: PushOptions) {
        prefs.edit()
            .putString(PROJECT, options.projectId).putString(APP, options.applicationId)
            .putString(KEY, options.apiKey).putString(SENDER, options.senderId)
            .apply()
    }

    /** (token, cuándo se registró en el backend), o null si nunca. */
    fun registration(): Pair<String, Long>? {
        val token = prefs.getString(TOKEN, null) ?: return null
        return token to prefs.getLong(REGISTERED_AT, 0L)
    }

    fun saveRegistration(token: String, atMs: Long) {
        prefs.edit().putString(TOKEN, token).putLong(REGISTERED_AT, atMs).apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    private companion object {
        const val PROJECT = "project_id"
        const val APP = "application_id"
        const val KEY = "api_key"
        const val SENDER = "sender_id"
        const val TOKEN = "token"
        const val REGISTERED_AT = "registered_at_ms"
    }
}

/** Qué pasó al poner al día los avisos del teléfono. */
enum class PushStatus { REGISTERED, UP_TO_DATE, DISABLED, UNAVAILABLE, FAILED }

/** La versión de la app que se registra con el token (el backend la guarda para saber qué teléfonos actualizar). */
fun interface AppVersion {
    operator fun invoke(): String
}

/**
 * Apunta el teléfono a los avisos push. El servidor manda: si tiene Firebase (`GET /mobile/push`), el teléfono
 * arranca Firebase con esas opciones y registra su token (`POST /mobile/devices`); si no, no arranca nada y suelta
 * el token que tuviera. Nada de Firebase va en el APK: el repo es público y cada tienda tiene su proyecto.
 */
@Singleton
class PushRegistrar @Inject constructor(
    private val api: PushApi,
    private val transport: PushTransport,
    private val cache: PushCache,
    private val appVersion: AppVersion,
    private val clock: Clock,
) {
    /** En `Application.onCreate`: arranca Firebase con lo guardado para recibir el push que despertó la app. */
    fun startFromCache() {
        cache.options()?.let(transport::start)
    }

    /** Al abrir la app con sesión. Barato: el mismo token no se vuelve a registrar antes de [REFRESH_MS]. */
    suspend fun sync(): PushStatus {
        val config = runCatching { api.config() }.getOrElse { return PushStatus.FAILED }
        val options = config.firebase?.takeIf { config.enabled }?.toPushOptions()
        if (options == null) {
            forget()
            return PushStatus.DISABLED
        }
        cache.saveOptions(options)
        if (!transport.start(options)) return PushStatus.UNAVAILABLE
        val token = runCatching { transport.token() }.getOrElse { return PushStatus.UNAVAILABLE }
        val last = cache.registration()
        if (last != null && last.first == token && clock.nowMs() - last.second < REFRESH_MS) return PushStatus.UP_TO_DATE
        return register(token)
    }

    /** Firebase cambió el token (o es el primero): el backend tiene que saberlo para poder avisar. */
    suspend fun register(token: String): PushStatus {
        runCatching { api.register(DeviceRequest(token = token, platform = "android", appVersion = appVersion())) }
            .getOrElse { return PushStatus.FAILED }
        cache.saveRegistration(token, clock.nowMs())
        return PushStatus.REGISTERED
    }

    /** Al cerrar sesión, ANTES de revocarla (el DELETE va con el token de acceso): el teléfono deja de recibir avisos. */
    suspend fun unregister() {
        cache.registration()?.let { (token, _) -> runCatching { api.unregister(token) } }
        forget()
    }

    private suspend fun forget() {
        if (cache.options() != null || cache.registration() != null) runCatching { transport.deleteToken() }
        cache.clear()
    }

    companion object {
        /** Cada cuánto se vuelve a registrar el mismo token: el backend sabe así qué teléfonos siguen vivos. */
        const val REFRESH_MS = 12 * 60 * 60 * 1000L
    }
}
