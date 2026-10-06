package com.hubara.operator

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.hubara.operator.core.push.PushHandler
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Solo debug (arnés E2E).
 * - `E2E_WAKE`: recibirlo basta para arrancar el proceso, y el vigía corre al arrancar (escenario S12).
 * - `E2E_PUSH`: un push de Firebase sin Google de por medio. Los extras son los datos del push (`type`, `reason`)
 *   y van al MISMO [PushHandler] que usa `OperatorMessagingService` (escenario S20; el arnés reenvía lo que el
 *   backend de prueba habría mandado a Firebase).
 */
class E2eWakeReceiver : BroadcastReceiver() {
    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface Deps {
        fun pushHandler(): PushHandler
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_PUSH) return
        val push = EntryPointAccessors.fromApplication(context, Deps::class.java).pushHandler()
        val extras = intent.extras ?: return
        val data = extras.keySet().associateWith { extras.getString(it).orEmpty() }
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                push.handle(data)
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        const val ACTION_PUSH = "com.hubara.operator.E2E_PUSH"
    }
}
