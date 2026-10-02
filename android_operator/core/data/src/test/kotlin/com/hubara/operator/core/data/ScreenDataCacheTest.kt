package com.hubara.operator.core.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.FileScreenDataCache
import kotlinx.serialization.json.Json
import org.junit.Test
import org.junit.runner.RunWith

/** Lo último que mostró cada pantalla del servidor: sin red se ve igual (como la bandeja con Room) y se borra al cerrar sesión. */
@RunWith(AndroidJUnit4::class)
class ScreenDataCacheTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test fun recuerda_por_pantalla_fuente_y_ruta() {
        val cache = FileScreenDataCache(context)
        val ventas = Json.parseToJsonElement("""{"orders": [{"id": "o1"}]}""")
        cache.write("ventas|pedidos|/api/orders/orders?limit=500", ventas)
        assertThat(FileScreenDataCache(context).read("ventas|pedidos|/api/orders/orders?limit=500")).isEqualTo(ventas)
        assertThat(cache.read("ventas|pedidos|/api/orders/orders?limit=10")).isNull()
    }

    @Test fun cerrar_sesion_lo_borra_todo() {
        val cache = FileScreenDataCache(context)
        cache.write("a|b|/api/x", Json.parseToJsonElement("[1]"))
        cache.clear()
        assertThat(cache.read("a|b|/api/x")).isNull()
    }
}
