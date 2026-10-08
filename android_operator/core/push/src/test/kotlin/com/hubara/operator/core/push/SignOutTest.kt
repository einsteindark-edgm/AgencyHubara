package com.hubara.operator.core.push

import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.FileScreenDataCache
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.InMemoryTokenStore
import com.hubara.operator.core.data.repo.SeenRepository
import com.hubara.operator.core.database.ConversationEntity
import com.hubara.operator.core.database.OperatorDatabase
import com.hubara.operator.core.model.SeenCounts
import com.hubara.operator.core.network.auth.CognitoClient
import com.hubara.operator.core.network.di.ApiConfig
import com.hubara.operator.core.network.dto.FirebaseOptionsDto
import com.hubara.operator.core.network.dto.HotSaleDto
import com.hubara.operator.core.network.dto.PushConfigDto
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf

/**
 * Auditoría de seguridad: cerrar sesión solo borraba los tokens. En un teléfono perdido o compartido quedaban la
 * bandeja con nombres y mensajes de clientes, el widget de ventas y las notificaciones. Cerrar sesión no deja datos
 * de clientes en el teléfono.
 */
@RunWith(AndroidJUnit4::class)
class SignOutTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val db = Room.inMemoryDatabaseBuilder(context, OperatorDatabase::class.java).allowMainThreadQueries().build()

    @After fun tearDown() = db.close()

    @Test fun cerrar_sesion_no_deja_datos_de_clientes_en_el_telefono() = runTest {
        db.conversations().upsert(listOf(ConversationEntity("wa_000000000101", "000000000101", "", "ventas", 1, null, 2, null, null, null, 0)))
        val ambient = AmbientStore(context)
        ambient.savePage(WidgetPageKind.HOT, hotPage(listOf(HotSaleDto(sessionId = "wa_000000000101", name = "Laura Prueba"))))
        // Las otras dos páginas del widget también tienen nombres de clientes.
        ambient.savePage(WidgetPageKind.FIRES, WidgetPage(listOf(WidgetRow("Laura pide un humano", "grave", "hubara://chat/wa_000000000101")), 1))
        ambient.savePage(WidgetPageKind.HUMAN, WidgetPage(listOf(WidgetRow("Laura", "2 sin responder", "hubara://chat/wa_000000000101")), 1))
        assertThat(ambient.pages.first().human).isNotNull()
        val seen = SeenRepository(context, db.conversations())
        seen.update { SeenCounts(baseline = true, seen = mapOf("wa_000000000101" to 2)) }
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(android.app.NotificationChannel("t", "t", NotificationManager.IMPORTANCE_DEFAULT))
        NotificationManagerCompat.from(context).notify(7, NotificationCompat.Builder(context, "t").setSmallIcon(android.R.drawable.ic_dialog_info).build())
        val auth = AuthRepository(
            ApiConfig("http://localhost/".toHttpUrl(), "client-test", "us-east-1"),
            CognitoClient(OkHttpClient(), "http://localhost:1/".toHttpUrl(), "client-test"),
            InMemoryTokenStore(),
        ) { 0L }

        val screenData = FileScreenDataCache(context)
        screenData.write("ventas|pedidos|/api/orders/orders", kotlinx.serialization.json.Json.parseToJsonElement("""{"orders": [{"customer": "Laura Prueba"}]}"""))

        // El teléfono recibía avisos push: al cerrar sesión deja de recibirlos (el backend borra su token).
        val pushApi = FakePushApi().apply {
            config = PushConfigDto(enabled = true, firebase = FirebaseOptionsDto("proyecto-prueba", "1:000:android:abc", "llave-prueba", "000"))
        }
        val pushCache = PushCache(context).apply { clear() }
        val push = PushRegistrar(pushApi, FakePushTransport(), pushCache, { "1.0.0" }) { 0L }
        push.sync()

        var widgetRefreshed = 0
        SignOut(context, auth, db, seen, ambient, HotWidgetUpdater { widgetRefreshed++ }, screenData, push)()

        assertThat(db.conversations().observeAll().first()).isEmpty()
        assertThat(ambient.pages.first()).isEqualTo(WidgetPages())  // ni ventas, ni incendios, ni quién atiende
        assertThat(seen.counts.first()).isEqualTo(SeenCounts())
        assertThat(shadowOf(manager).allNotifications).isEmpty()
        assertThat(widgetRefreshed).isEqualTo(1)  // el widget de la pantalla de inicio deja de mostrar nombres
        assertThat(screenData.read("ventas|pedidos|/api/orders/orders")).isNull()  // ni lo último de las pantallas del servidor
        assertThat(pushApi.unregistered).containsExactly("token-1")                  // ni avisos push a este teléfono
        assertThat(pushCache.registration()).isNull()
    }
}
