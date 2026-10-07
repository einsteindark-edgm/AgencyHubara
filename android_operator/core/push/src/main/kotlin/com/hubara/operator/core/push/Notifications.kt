package com.hubara.operator.core.push

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject

/** Canales de la app. La pantalla bloqueada nunca muestra nombres: hay versión pública. */
object Channels {
    const val FIRES = "incendios_graves"
    const val SALES_RISK = "ventas_riesgo"
    const val GENERAL = "general"

    fun register(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannels(listOf(
            NotificationChannel(FIRES, "Incendios graves", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Chats y órdenes que no pueden esperar."
                lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
            },
            NotificationChannel(SALES_RISK, "Ventas en riesgo", NotificationManager.IMPORTANCE_HIGH).apply {
                lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
            },
            NotificationChannel(GENERAL, "General", NotificationManager.IMPORTANCE_DEFAULT),
        ))
    }
}

/** El deep link que abre cada incendio: el chat o la ficha de la orden. */
fun deepLinkFor(fire: Fire): String? {
    val order = (fire.subject as? FireSubject.Order)?.orderId
    val session = fire.subject.sessionId
    return when {
        fire.primaryAction.name == "open_order" && order != null -> "hubara://order/${order.raw}"
        session != null -> "hubara://chat/${session.raw}"
        else -> null
    }
}

/**
 * PendingIntent inmutable y explícito a la actividad de inicio de la app (skill android-intent-security).
 * El link se valida otra vez al llegar (`DeepLinks.parse`).
 */
fun openAppIntent(context: Context, link: String, requestCode: Int): PendingIntent? {
    val launch = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return null
    val intent = Intent(launch).setData(link.toUri()).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    return PendingIntent.getActivity(context, requestCode, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
}

fun canNotify(context: Context): Boolean =
    Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

/** Avisa un incendio grave. La versión pública (pantalla bloqueada) no lleva nombres ni mensajes. */
fun notifyFire(context: Context, fire: Fire, pendingCount: Int) {
    if (!canNotify(context)) return
    val link = deepLinkFor(fire) ?: return
    val publicVersion = NotificationCompat.Builder(context, Channels.FIRES)
        .setSmallIcon(android.R.drawable.stat_notify_error)
        .setContentTitle(if (pendingCount > 1) "$pendingCount incendios graves" else "1 incendio grave")
        .build()
    val notification = NotificationCompat.Builder(context, Channels.FIRES)
        .setSmallIcon(android.R.drawable.stat_notify_error)
        .setContentTitle(fire.title)
        .setContentText(fire.subtitle)
        .setCategory(NotificationCompat.CATEGORY_MESSAGE)
        .setPriority(NotificationCompat.PRIORITY_HIGH)
        .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
        .setPublicVersion(publicVersion)
        .setAutoCancel(true)
        .setContentIntent(openAppIntent(context, link, fire.id.raw.hashCode()))
        .build()
    try {
        NotificationManagerCompat.from(context).notify(fire.id.raw, 1, notification)
    } catch (_: SecurityException) {
        // El permiso se revocó entre el chequeo y el aviso: no hay nada más que hacer.
    }
}
