package com.hubara.operator

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Solo debug: recibir el broadcast basta para arrancar el proceso, y el vigía corre al arrancar (escenario S12). */
class E2eWakeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Unit
}
