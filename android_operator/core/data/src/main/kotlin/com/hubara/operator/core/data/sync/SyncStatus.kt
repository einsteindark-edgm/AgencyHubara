package com.hubara.operator.core.data.sync

/** Cómo está el tiempo real. */
enum class SyncState {
    /** La app está en segundo plano o sin sesión: se reanuda al volver. */
    STOPPED,
    CONNECTING,
    LIVE,

    /** Se cortó y espera para volver a intentar. */
    RETRYING,
}

/**
 * Lo que se sabe de la sincronización desde que se abrió la app: si está en vivo, qué llegó, cuánto tardó y los cortes.
 * Lo muestra la pantalla «Sincronización» (fuente del teléfono `sync`) y lo cuenta el log `HubaraSync`.
 */
data class SyncStatus(
    val state: SyncState = SyncState.STOPPED,
    /** Desde cuándo está en vivo (null si no lo está). */
    val connectedSinceMs: Long? = null,
    /** Cuándo llegó el último evento (hora del teléfono). */
    val lastEventMs: Long? = null,
    /** Cuánto tardó en llegar el último evento: hora del teléfono menos la del servidor (aproximado: los relojes difieren). */
    val lagMs: Long? = null,
    val maxLagMs: Long? = null,
    val events: Int = 0,
    val bytes: Long = 0,
    /** Cortes del tiempo real desde que se abrió la app. */
    val cuts: Int = 0,
    val lastError: String? = null,
    val lastErrorMs: Long? = null,
    /** Cuándo vuelve a intentar (si está esperando). */
    val retryAtMs: Long? = null,
    /** La última vez que la bandeja se pidió por REST (al abrir, al tirar para actualizar o de respaldo) y si salió bien. */
    val lastRefreshMs: Long? = null,
    val lastRefreshOk: Boolean? = null,
)
