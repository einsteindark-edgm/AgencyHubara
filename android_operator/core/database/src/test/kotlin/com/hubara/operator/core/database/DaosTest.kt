package com.hubara.operator.core.database

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DaosTest {
    private lateinit var db: OperatorDatabase

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), OperatorDatabase::class.java)
            .allowMainThreadQueries().build()
    }

    @After fun tearDown() = db.close()

    private fun conv(id: String, updated: Long, window: Long? = null) = ConversationEntity(
        id, "", "", "ventas", updated, null, 0, null, null, null, 1, window,
    )

    @Test fun la_bandeja_reemplaza_la_lista_y_conserva_la_ventana_del_detalle() = runTest {
        val dao = db.conversations()
        dao.upsert(listOf(conv("wa_a", 1, window = 99), conv("wa_viejo", 2)))
        dao.replaceAll(listOf(conv("wa_a", 5), conv("wa_b", 7)))
        val rows = dao.observeAll().first()
        assertThat(rows.map { it.sessionId }).containsExactly("wa_b", "wa_a").inOrder()
        assertThat(rows.first { it.sessionId == "wa_a" }.windowExpiresAtMs).isEqualTo(99)
    }

    @Test fun el_historial_se_reemplaza_por_sesion() = runTest {
        val dao = db.messages()
        dao.replaceSession("wa_a", listOf(MessageEntity("wa_a", "k1", 0, "CUSTOMER", "hola", null, 1)))
        dao.replaceSession("wa_b", listOf(MessageEntity("wa_b", "k1", 0, "BOT", "x", null, 1)))
        dao.replaceSession("wa_a", listOf(
            MessageEntity("wa_a", "k1", 0, "CUSTOMER", "hola", null, 1),
            MessageEntity("wa_a", "k2", 1, "BOT", "¡hola!", null, 2),
        ))
        assertThat(dao.observe("wa_a").first().map { it.key }).containsExactly("k1", "k2").inOrder()
        assertThat(dao.observe("wa_b").first()).hasSize(1)
    }

    @Test fun los_ocultos_se_olvidan_cuando_el_incendio_sale_del_feed() = runTest {
        val dao = db.fires()
        dao.replaceAll(listOf(FireEntity("chat:wa_a", 0, 1, "{}"), FireEntity("chat:wa_b", 0, 2, "{}")))
        dao.hide(HiddenFireEntity("chat:wa_a", 10))
        assertThat(dao.observeHidden().first()).containsExactly("chat:wa_a")
        dao.replaceAll(listOf(FireEntity("chat:wa_b", 0, 2, "{}")))
        assertThat(dao.observeHidden().first()).isEmpty()
        assertThat(dao.observeAll().first().map { it.fireId }).containsExactly("chat:wa_b")
    }

    @Test fun el_outbox_solo_muestra_lo_que_no_se_envio() = runTest {
        val dao = db.outbox()
        dao.upsert(OutboxEntity("c1", "wa_a", "text", "hola", null, null, "hola", "queued", null, 1))
        dao.upsert(OutboxEntity("c2", "wa_a", "tool", null, "present_products", "{}", "Enviar productos", "pending_undo", null, 2))
        dao.setState("c1", "sent")
        assertThat(dao.observeUnsent("wa_a").first().map { it.clientActionId }).containsExactly("c2")
    }
}
