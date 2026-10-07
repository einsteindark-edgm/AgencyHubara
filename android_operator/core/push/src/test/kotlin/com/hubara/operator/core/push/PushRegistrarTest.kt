package com.hubara.operator.core.push

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.dto.DeviceRequest
import com.hubara.operator.core.network.dto.FirebaseOptionsDto
import com.hubara.operator.core.network.dto.PushConfigDto
import kotlinx.coroutines.test.runTest
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * El teléfono se apunta a los avisos push con lo que diga el servidor: si hay Firebase, arranca Firebase con esas
 * opciones y registra su token; si no, no arranca nada. Las opciones quedan guardadas para que un push despierte la
 * app aunque esté cerrada.
 */
@RunWith(AndroidJUnit4::class)
class PushRegistrarTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val options = FirebaseOptionsDto(
        projectId = "proyecto-prueba", applicationId = "1:000:android:abc", apiKey = "llave-prueba", gcmSenderId = "000",
    )
    private val api = FakePushApi()
    private val transport = FakePushTransport()
    private var now = 1_000_000L
    private lateinit var cache: PushCache

    @Before fun setUp() {
        cache = PushCache(context)
        cache.clear()
    }

    private fun registrar(cache: PushCache = this.cache) = PushRegistrar(api, transport, cache, { "1.0.0" }) { now }

    @Test fun con_firebase_en_el_servidor_registra_el_token_del_telefono() = runTest {
        api.config = PushConfigDto(enabled = true, firebase = options)
        transport.token = "token-1"

        assertThat(registrar().sync()).isEqualTo(PushStatus.REGISTERED)

        assertThat(transport.started).containsExactly(options.toPushOptions())
        assertThat(api.registered).containsExactly(DeviceRequest("token-1", "android", "1.0.0"))
    }

    @Test fun sin_firebase_en_el_servidor_no_arranca_firebase_ni_registra() = runTest {
        api.config = PushConfigDto(enabled = false)

        assertThat(registrar().sync()).isEqualTo(PushStatus.DISABLED)

        assertThat(transport.started).isEmpty()
        assertThat(api.registered).isEmpty()
    }

    @Test fun con_la_app_cerrada_arranca_firebase_con_lo_ultimo_que_dijo_el_servidor() = runTest {
        api.config = PushConfigDto(enabled = true, firebase = options)
        registrar().sync()
        transport.started.clear()

        // Otro proceso (un push despertó la app): sin red ni sesión, solo lo guardado.
        registrar(PushCache(context)).startFromCache()

        assertThat(transport.started).containsExactly(options.toPushOptions())
    }

    @Test fun si_el_servidor_apaga_los_avisos_el_telefono_suelta_su_token() = runTest {
        api.config = PushConfigDto(enabled = true, firebase = options)
        registrar().sync()

        api.config = PushConfigDto(enabled = false)
        assertThat(registrar().sync()).isEqualTo(PushStatus.DISABLED)

        assertThat(transport.deleted).isEqualTo(1)
        transport.started.clear()
        registrar(PushCache(context)).startFromCache()
        assertThat(transport.started).isEmpty()
    }

    @Test fun no_vuelve_a_registrar_el_mismo_token_cada_vez_que_se_abre_la_app() = runTest {
        api.config = PushConfigDto(enabled = true, firebase = options)
        registrar().sync()
        now += 60_000

        assertThat(registrar().sync()).isEqualTo(PushStatus.UP_TO_DATE)
        assertThat(api.registered).hasSize(1)

        // Medio día después se renueva (el servidor sabe que el teléfono sigue vivo) y un token nuevo va de una.
        now += PushRegistrar.REFRESH_MS
        assertThat(registrar().sync()).isEqualTo(PushStatus.REGISTERED)
        transport.token = "token-2"
        assertThat(registrar().sync()).isEqualTo(PushStatus.REGISTERED)
        assertThat(api.registered.map { it.token }).containsExactly("token-1", "token-1", "token-2").inOrder()
    }

    @Test fun cuando_firebase_cambia_el_token_se_registra_el_nuevo() = runTest {
        assertThat(registrar().register("token-nuevo")).isEqualTo(PushStatus.REGISTERED)
        assertThat(api.registered.single().token).isEqualTo("token-nuevo")
    }

    @Test fun cerrar_sesion_borra_el_token_del_servidor_y_del_telefono() = runTest {
        api.config = PushConfigDto(enabled = true, firebase = options)
        registrar().sync()

        registrar().unregister()

        assertThat(api.unregistered).containsExactly("token-1")
        assertThat(transport.deleted).isEqualTo(1)
        transport.started.clear()
        registrar(PushCache(context)).startFromCache()
        assertThat(transport.started).isEmpty()
    }

    @Test fun sin_red_o_sin_google_play_no_se_cae() = runTest {
        api.failing = true
        assertThat(registrar().sync()).isEqualTo(PushStatus.FAILED)

        api.failing = false
        api.config = PushConfigDto(enabled = true, firebase = options)
        transport.available = false
        assertThat(registrar().sync()).isEqualTo(PushStatus.UNAVAILABLE)
        assertThat(api.registered).isEmpty()
    }
}
