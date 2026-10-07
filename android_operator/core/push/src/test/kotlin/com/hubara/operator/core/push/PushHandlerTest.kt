package com.hubara.operator.core.push

import android.app.NotificationManager
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.test.runTest
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf

/**
 * Un push es el corrientazo: no trae datos de clientes (pasan por Google), solo «ponte al día». La app se pone al
 * día en el momento —incendios graves, ventas calientes y widget— y, si no alcanza, lo deja a WorkManager.
 */
@RunWith(AndroidJUnit4::class)
class PushHandlerTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val manager = context.getSystemService(NotificationManager::class.java)
    private var passes = 0
    private var later = 0

    @Before fun setUp() {
        Channels.register(context)
        shadowOf(context as android.app.Application).grantPermissions(android.Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun handler(pass: suspend () -> Boolean = { passes++; true }) =
        PushHandler(context, { pass() }, { later++ })

    @Test fun el_push_de_sincronizar_pone_al_dia_la_app_en_el_momento() = runTest {
        handler().handle(mapOf("type" to "sync", "reason" to "fire"))

        assertThat(passes).isEqualTo(1)
        assertThat(later).isEqualTo(0)
    }

    @Test fun si_no_alcanza_a_ponerse_al_dia_lo_deja_para_workmanager() = runTest {
        handler(pass = { false }).handle(mapOf("type" to "sync"))
        assertThat(later).isEqualTo(1)

        handler(pass = { error("sin red") }).handle(mapOf("type" to "sync"))
        assertThat(later).isEqualTo(2)

        // Google da unos segundos para atender un push: lo que no termina a tiempo sigue en WorkManager.
        handler(pass = { awaitCancellation() }).handle(mapOf("type" to "sync"))
        assertThat(later).isEqualTo(3)
    }

    @Test fun el_aviso_de_prueba_dice_que_los_avisos_funcionan() = runTest {
        handler().handle(mapOf("type" to "test"))

        val shown = shadowOf(manager).allNotifications.single()
        assertThat(shown.extras.getString("android.title")).isEqualTo("Los avisos funcionan")
        assertThat(passes).isEqualTo(0)
    }

    @Test fun un_tipo_que_esta_version_no_conoce_se_ignora() = runTest {
        handler().handle(mapOf("type" to "algo_nuevo"))
        handler().handle(emptyMap())

        assertThat(passes).isEqualTo(0)
        assertThat(later).isEqualTo(0)
        assertThat(shadowOf(manager).allNotifications).isEmpty()
    }
}
