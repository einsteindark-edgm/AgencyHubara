package com.hubara.operator

import android.appwidget.AppWidgetHost
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.os.Bundle
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import com.hubara.operator.core.push.HotWidgetUpdater
import com.hubara.operator.core.push.Vigia
import com.hubara.operator.widget.hot.HotSalesWidgetReceiver
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Solo debug (arnés E2E, escenarios S24 y S25): hospeda el widget REAL de la pantalla de inicio en una ventana propia,
 * como haría el launcher, para que un guion toque sus pestañas y filas (agregar un widget al launcher no se puede
 * guionizar). Necesita el permiso de enlazar widgets: `adb shell appwidget grantbind --package com.acktos.operator`.
 * `--es size compact` lo pone en 2×2; `--es then large|compact` le cambia el tamaño después, como cuando el operador lo estira en el
 * launcher (S26). Antes de pintar corre una vuelta del vigía, igual que un push.
 */
class E2eWidgetHostActivity : ComponentActivity() {
    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface Deps {
        fun vigia(): Vigia
        fun widget(): HotWidgetUpdater
    }

    private lateinit var host: AppWidgetHost

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this)
        setContentView(root)
        host = AppWidgetHost(applicationContext, HOST_ID)
        host.deleteHost()  // un widget por corrida: el de la vez anterior se suelta
        // Escuchar ANTES de crear la vista, como un launcher: si no, el aviso de que llegaron las filas de la lista
        // (notifyAppWidgetViewDataChanged) se pierde y la lista queda vacía (lo vio S24 en Android 11).
        host.startListening()
        val manager = AppWidgetManager.getInstance(this)
        val id = host.allocateAppWidgetId()
        if (!manager.bindAppWidgetIdIfAllowed(id, ComponentName(this, HotSalesWidgetReceiver::class.java))) {
            root.addView(TextView(this).apply { text = "Sin permiso para hospedar el widget (appwidget grantbind)." })
            return
        }
        val density = resources.displayMetrics.density
        // El tamaño se lo dice al widget como lo haría el launcher: sin eso Glance usa el mínimo (2×2).
        val (w, h) = if (intent.getStringExtra("size") == "compact") 150 to 150 else 340 to 280
        val view = host.createView(applicationContext, id, manager.getAppWidgetInfo(id))
        @Suppress("DEPRECATION") view.updateAppWidgetSize(Bundle(), w, h, w, h)
        root.addView(view, FrameLayout.LayoutParams((w * density).toInt(), (h * density).toInt(), Gravity.CENTER))
        val deps = EntryPointAccessors.fromApplication(this, Deps::class.java)
        lifecycleScope.launch {
            deps.widget().update(applicationContext)  // lo guardado, al instante
            deps.vigia().run()                        // y lo de ahora (la vuelta ya repinta el widget)
            val next = when (intent.getStringExtra("then")) {
                "large" -> 340 to 280
                "compact" -> 150 to 150
                else -> null
            }
            if (next != null) {
                delay(2_000)
                @Suppress("DEPRECATION") view.updateAppWidgetSize(Bundle(), next.first, next.second, next.first, next.second)
                view.layoutParams = FrameLayout.LayoutParams((next.first * density).toInt(), (next.second * density).toInt(), Gravity.CENTER)
            }
        }
    }

    override fun onDestroy() {
        host.stopListening()
        super.onDestroy()
    }

    private companion object {
        const val HOST_ID = 4_242
    }
}
