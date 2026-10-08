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
import kotlinx.coroutines.launch

/**
 * Solo debug (arnés E2E, escenario S24): hospeda el widget REAL de la pantalla de inicio en una ventana propia, como
 * haría el launcher, para que un guion lo deslice y toque sus filas (agregar un widget al launcher no se puede
 * guionizar). Necesita el permiso de enlazar widgets: `adb shell appwidget grantbind --package com.acktos.operator`.
 * Antes de pintar corre una vuelta del vigía, igual que un push.
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
        val manager = AppWidgetManager.getInstance(this)
        val id = host.allocateAppWidgetId()
        if (!manager.bindAppWidgetIdIfAllowed(id, ComponentName(this, HotSalesWidgetReceiver::class.java))) {
            root.addView(TextView(this).apply { text = "Sin permiso para hospedar el widget (appwidget grantbind)." })
            return
        }
        val density = resources.displayMetrics.density
        val view = host.createView(applicationContext, id, manager.getAppWidgetInfo(id))
        root.addView(view, FrameLayout.LayoutParams((340 * density).toInt(), (260 * density).toInt(), Gravity.CENTER))
        val deps = EntryPointAccessors.fromApplication(this, Deps::class.java)
        lifecycleScope.launch {
            deps.widget().update(applicationContext)  // lo guardado, al instante
            deps.vigia().run()                        // y lo de ahora (la vuelta ya repinta el widget)
        }
    }

    override fun onStart() {
        super.onStart()
        host.startListening()
    }

    override fun onStop() {
        super.onStop()
        host.stopListening()
    }

    private companion object {
        const val HOST_ID = 4_242
    }
}
