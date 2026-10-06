package com.hubara.operator.core.push

import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import kotlinx.coroutines.withTimeoutOrNull

/** Una vuelta del vigía ahora mismo; true si la app quedó al día. */
fun interface VigiaPass {
    suspend fun run(): Boolean
}

/** Deja la vuelta del vigía a WorkManager (con red), para cuando no alcanzó en el momento. */
fun interface VigiaLater {
    fun schedule()
}

/**
 * Lo que hace la app con un push. El push no trae datos de clientes —pasa por los servidores de Google—: dice
 * solo qué hacer (`type`). `sync` = ponerse al día (incendios graves, ventas calientes y widget) en el momento, y si
 * no alcanza, por WorkManager; `test` = el aviso de «Probar avisos». Un tipo que esta versión no conoce se ignora:
 * el backend puede estrenar uno sin romper las apps viejas.
 */
class PushHandler @Inject constructor(
    @ApplicationContext private val context: Context,
    private val pass: VigiaPass,
    private val later: VigiaLater,
) {
    suspend fun handle(data: Map<String, String>) {
        when (data["type"]) {
            TYPE_SYNC -> {
                val done = runCatching { withTimeoutOrNull(NOW_BUDGET_MS) { pass.run() } }.getOrNull() == true
                if (!done) later.schedule()
            }
            TYPE_TEST -> notifyPushTest(context)
        }
    }

    companion object {
        const val TYPE_SYNC = "sync"
        const val TYPE_TEST = "test"
        /** Android da unos 10 s para atender un push (menos en teléfonos viejos): lo que no cabe va a WorkManager. */
        const val NOW_BUDGET_MS = 8_000L
    }
}

/** El aviso de «Probar avisos»: si llega con la app cerrada, también llegan los incendios graves. */
fun notifyPushTest(context: Context) {
    if (!canNotify(context)) return
    val notification = NotificationCompat.Builder(context, Channels.GENERAL)
        .setSmallIcon(android.R.drawable.stat_notify_chat)
        .setContentTitle("Los avisos funcionan")
        .setContentText("Así te llega un incendio grave aunque la app esté cerrada.")
        .setAutoCancel(true)
        .build()
    try {
        NotificationManagerCompat.from(context).notify(TEST_NOTIFICATION_TAG, 1, notification)
    } catch (_: SecurityException) {
        // El permiso se revocó entre el chequeo y el aviso.
    }
}

private const val TEST_NOTIFICATION_TAG = "prueba_de_avisos"
